"""Scanner — find funding-rate arbitrage opportunities across exchange pairs.

Two modes:
  HOLD:        both sides are income -> hold until edge reverses
  CHERRY_PICK: one side is income, one is cost -> collect income payments,
               exit BEFORE the next costly payment
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Dict, List, Optional

import json

from src.core.contracts import EntryTier, OpportunityCandidate, OrderSide, TradeMode
from src.core.logging import get_logger
from src.discovery._executable_status import compute_statuses_for
from src.discovery._scanner_evaluator import _ScannerEvaluatorMixin, _classify_tier
from src.discovery.calculator import (
    analyze_per_payment_pnl,
    calculate_cherry_pick_edge,
    calculate_fees,
    calculate_funding_spread,
)

if TYPE_CHECKING:
    from src.core.config import Config
    from src.exchanges.adapter import ExchangeAdapter, ExchangeManager
    from src.storage.redis_client import RedisClient

# Re-export for backward compatibility (tests import from src.discovery.scanner)
__all__ = ["Scanner", "_classify_tier"]

logger = get_logger("scanner")

_FUNDING_STALE_SEC = 3600
_TOP_OPPS_LOG_INTERVAL_SEC = 300
_SUSPEND_GAP_SECONDS = 900.0
# Debounce window for hot-scan: collect price updates for this many ms before evaluating.
# Reduces CPU on exchanges that push tickers at 10+ Hz.
_HOT_DEBOUNCE_MS = 100
# Minimum gap between consecutive hot-scan callbacks for the same symbol.
# Prevents flooding the controller when a price bounces repeatedly near a threshold.
_HOT_CALLBACK_COOLDOWN_SEC = 10
# Minimum gap between repeated INFO opportunity logs for the same route/signature.
_OPPORTUNITY_LOG_COOLDOWN_SEC = 60
# Net penalty (%) applied to stale-price items in display sort.
# Soft penalty instead of hard binary barrier so stale items with great
# nets can still compete — avoids complete top-5 list swaps.
_STALE_DISPLAY_PENALTY = 0.05
# Mini-OB refresh: keep live ask/bid for the top stale candidates.
_OB_REFRESH_INTERVAL_SEC = 3
# P2-1: Circuit-breaker constants — hoisted to module level (were incorrectly
# defined inside the while loop, re-binding every 5 s).
_CB_MAX_ERRORS: int = 3
_CB_BACKOFF_SEC: float = 300.0
_OB_REFRESH_MAX_TARGETS = 10   # (exchange, symbol) pairs to track
_OB_REFRESH_CONCURRENCY = 4   # max parallel OB REST calls


def _hot_scan_task_done(task: asyncio.Task) -> None:  # type: ignore[type-arg]
    if task.cancelled():
        return
    exc = task.exception()
    if exc:
        logger.error(f"[hot-scan] Task exited unexpectedly: {exc}")


def _ob_refresh_task_done(task: asyncio.Task) -> None:  # type: ignore[type-arg]
    if task.cancelled():
        return
    exc = task.exception()
    if exc:
        logger.error(f"[ob-refresh] Task exited unexpectedly: {exc}")


def _hot_entry_task_done(task: asyncio.Task) -> None:  # type: ignore[type-arg]
    """Supervision callback for per-opportunity entry tasks spawned by hot-scan."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc:
        logger.error(f"[hot-entry] Task {task.get_name()} failed: {exc}")


class Scanner(_ScannerEvaluatorMixin):
    def __init__(
        self,
        config: "Config",
        exchange_mgr: "ExchangeManager",
        redis: "RedisClient",
        publisher=None,
    ):
        self._cfg = config
        self._exchanges = exchange_mgr
        self._redis = redis
        self._running = False
        self._publisher = publisher
        self._last_top_log_ts = 0.0
        # Cache for common_symbols — rebuilt every 60 scans or when exchanges change
        self._common_symbols_cache: Optional[set] = None
        self._cache_exchange_ids: List[str] = []
        self._cache_scan_count: int = 0        # Hot-scan queue: adapters push (exchange_id, symbol) here on every fresh price update.
        # _hot_scan_loop() drains this queue and evaluates only the affected symbols.
        # P1-1: Increased from 500 → 5000. At 10 Hz across 3 exchanges × 200 symbols
        # the queue could saturate in under 1s during volatile pre-funding periods;
        # dropped entries mean missed entries rather than slowed evaluation.
        self._hot_queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue(maxsize=5000)
        self._hot_scan_task: Optional[asyncio.Task] = None
        # Phase-3: candidates shortlist — only symbols with a meaningful funding spread
        # (updated after each full scan_all()) are evaluated in the hot-scan path.
        # Falls back to all common_symbols during startup before the first scan_all().
        self._hot_candidates: set[str] = set()
        # Per-symbol epoch-sec of last callback fire from the hot path.
        # Guards against repeated callbacks for the same opportunity within 10 s.
        self._hot_cb_last_fire: Dict[str, float] = {}
        # Per-opportunity INFO log throttle cache to avoid repeated log spam when
        # scanner cycles evaluate the same stale candidate continuously.
        self._opp_log_last_fire: Dict[str, float] = {}
        # 24h volume cache (USD) keyed by f"{exchange_id}:{symbol}" → (volume_usd, cached_at_ts).
        # Backs the liquidity filter in _evaluate_direction; TTL set via config to
        # avoid hammering REST every scan cycle (volume changes slowly).
        self._volume_cache: Dict[str, tuple[Decimal, float]] = {}
        self._opp_log_signature: Dict[str, str] = {}
        # P2-2: Circuit breaker per exchange.  After _CB_MAX_ERRORS consecutive
        # maybe_reload_markets failures the exchange is skipped for _CB_BACKOFF_SEC
        # seconds, preventing log floods and wasted CPU during outages.
        self._exchange_consecutive_errors: Dict[str, int] = {}
        self._exchange_backoff_until: Dict[str, float] = {}
        # Hysteresis: only push a new top-5 to Redis/WebSocket when the list
        # changes meaningfully (symbol, exchange pair, stale flag, or net_pct
        # rounded to 1 dp).  Prevents every-5s flicker from tiny price drift.
        self._last_opp_fingerprint: str = ""
        # Sticky top-5: remember the previous display list so new items must
        # beat existing incumbents by a meaningful margin to earn a slot.
        self._prev_display_keys: set[str] = set()
        # Retain previous display opportunities for 1-cycle gap tolerance.
        # Maps opp_key → (OpportunityCandidate, cycles_retained).
        self._prev_display_opps: Dict[str, tuple] = {}
        # Mini-OB refresh: (exchange_id, symbol) pairs to keep fresh every 3s.
        self._ob_refresh_targets: set[tuple[str, str]] = set()
        self._ob_refresh_task: Optional[asyncio.Task] = None
        # Near-window watch: symbols approaching the entry window that need
        # more frequent evaluation than the ~3-minute full scan cycle provides.
        # The hot-scan loop injects these on its 1s timeout so they are
        # re-evaluated every second until they enter (or pass) the window.
        self._near_window_watch: set[str] = set()
        # Callback for entry dispatch — stored so scan_all() can dispatch
        # qualified opportunities immediately (not wait for gather to complete).
        self._scan_callback = None
        # Track routes already dispatched during the current scan_all() to
        # avoid double-dispatch when the main loop also processes results.
        self._early_dispatched: set[str] = set()

    async def _publish_display_if_changed(
        self,
        display_top: list[OpportunityCandidate],
    ) -> bool:
        """Compute fingerprint over display_top and publish to Redis on change.

        Shared by scan_all() and the hot-scan loop so the dashboard sees
        disqualify_reason/qualified flips within seconds of a funding
        boundary, not on the next 3-minute full-scan cycle.

        Returns True if a publish actually happened.
        """
        new_fingerprint = self._opportunity_fingerprint(display_top)
        if new_fingerprint == self._last_opp_fingerprint:
            return False
        self._last_opp_fingerprint = new_fingerprint
        if not self._publisher:
            return False

        # Pre-compute executable_status so the dashboard can distinguish
        # scanner-qualified rows that the bot WILL try to enter from those
        # it will silently skip (e.g. lot_size_too_large when one leg is
        # low on margin). Reads balances + active positions from Redis —
        # no extra exchange round-trips.
        balances_map: Dict[str, float] = {}
        busy_symbols: frozenset = frozenset()
        try:
            bal_raw = await self._redis.get("trinity:balances")
            if bal_raw:
                bd = json.loads(bal_raw)
                balances_map = {
                    k: float(v) for k, v in (bd.get("balances") or {}).items()
                }
            pos_raw = await self._redis.get("trinity:positions")
            if pos_raw:
                pd = json.loads(pos_raw)
                items = pd if isinstance(pd, list) else pd.get("positions", [])
                busy_symbols = frozenset(
                    p["symbol"] for p in items
                    if isinstance(p, dict) and p.get("symbol")
                )
        except Exception as exc:
            logger.debug(f"executable_status snapshot read failed: {exc}")
        exec_statuses = await compute_statuses_for(
            display_top, balances_map, self._exchanges,
            self._cfg, busy_symbols,
        )

        # Tier classification (_classify_tier) uses tier_net =
        # immediate_spread - taker_fees, which deliberately ignores the
        # larger exit_slippage_buffer the exit gates require. Result: an
        # opportunity can show entry_tier="top" while net_edge_pct (the
        # user-visible "Net" column) is already negative — the row earns
        # a 🏆 badge for a trade that cannot exit profitably. Demote the
        # displayed tier to None whenever net_edge_pct is non-positive
        # so the badge only appears on rows that have a chance at green
        # PnL. Internal entry_tier on the OpportunityCandidate is left
        # alone so executor / scanner code paths are unchanged.
        def _display_tier(o) -> Optional[str]:
            if o.entry_tier is None:
                return None
            if float(o.net_edge_pct) <= 0:
                return None
            return o.entry_tier

        opp_data = [
            {
                "symbol": o.symbol,
                "long_exchange": o.long_exchange,
                "short_exchange": o.short_exchange,
                "net_pct": float(o.net_edge_pct),
                "gross_pct": float(o.gross_edge_pct),
                "funding_spread_pct": float(o.funding_spread_pct),
                "immediate_spread_pct": float(o.immediate_spread_pct),
                "immediate_net_pct": float(o.immediate_net_pct),
                "hourly_rate_pct": float(o.hourly_rate_pct),
                "min_interval_hours": o.min_interval_hours,
                "next_funding_ms": o.next_funding_ms,
                "long_next_funding_ms": o.long_next_funding_ms,
                "short_next_funding_ms": o.short_next_funding_ms,
                "long_rate": float(o.long_funding_rate),
                "short_rate": float(o.short_funding_rate),
                "price": float(o.reference_price),
                "mode": o.mode,
                "qualified": o.qualified,
                "long_interval_hours": o.long_interval_hours,
                "short_interval_hours": o.short_interval_hours,
                "entry_tier": _display_tier(o),
                "price_spread_pct": float(o.price_spread_pct),
                "stale_price": o.stale_price,
                "executable_status": exec_statuses[i],
                "disqualify_reason": o.disqualify_reason,
            }
            for i, o in enumerate(display_top)
        ]
        await self._publisher.publish_opportunities(opp_data)
        return True

    def _opportunity_fingerprint(self, display_top: list[OpportunityCandidate]) -> str:
        """Return a stable fingerprint for the published display opportunities.

        Bug fix: previously included only price/funding-time deltas, not
        qualification state. So when Net flipped above/below threshold or
        the rejection reason changed (vol_unknown ↔ funding_no_imminent),
        the fingerprint stayed equal and the dashboard kept rendering the
        last-published reason — frequently 5–15 minutes stale.
        Now includes qualified, disqualify_reason and entry_tier so any
        meaningful state change triggers a re-publish.
        """

        def _bucket(ts_ms: Optional[float]) -> str:
            if ts_ms is None:
                return "none"
            return str(int(float(ts_ms) // 60_000))

        parts = sorted(
            f"{o.symbol}|{o.long_exchange}|{o.short_exchange}"
            f"|{1 if o.stale_price else 0}"
            f"|{round(float(o.net_edge_pct), 1):.1f}"
            f"|{_bucket(o.next_funding_ms)}"
            f"|{_bucket(o.long_next_funding_ms)}"
            f"|{_bucket(o.short_next_funding_ms)}"
            f"|q{1 if o.qualified else 0}"
            f"|d{o.disqualify_reason or '-'}"
            f"|t{o.entry_tier or '-'}"
            for o in display_top
        )
        return ",".join(parts)

    def _should_emit_opportunity_log(
        self,
        symbol: str,
        long_exchange: str,
        short_exchange: str,
        entry_tier: Optional[str],
        net_pct: Decimal,
        price_spread_pct: Decimal,
        is_adverse: bool,
    ) -> bool:
        """Return True when an INFO opportunity log should be emitted.

        Logs are emitted immediately when signature changes materially, otherwise
        they are rate-limited per (symbol, route, adverse-state).
        """
        log_key = f"{symbol}|{long_exchange}|{short_exchange}|{int(is_adverse)}"
        # Coarse signature: 2 dp net, 1 dp price-spread.
        # Prevents tiny price drift from resetting the cooldown timer
        # (was 4 dp, causing 20+ logs/min for hot-scan symbols).
        signature = (
            f"{entry_tier or 'none'}|"
            f"{net_pct.quantize(Decimal('0.01'))}|"
            f"{price_spread_pct.quantize(Decimal('0.1'))}"
        )
        now_monotonic = time.monotonic()
        last_signature = self._opp_log_signature.get(log_key)

        if signature != last_signature:
            self._opp_log_signature[log_key] = signature
            self._opp_log_last_fire[log_key] = now_monotonic
            return True

        last_fire = self._opp_log_last_fire.get(log_key, 0.0)
        if now_monotonic - last_fire >= _OPPORTUNITY_LOG_COOLDOWN_SEC:
            self._opp_log_last_fire[log_key] = now_monotonic
            return True

        return False
    # ── Lifecycle ────────────────────────────────────────────────

    async def start(self, callback) -> None:
        """Continuously scan; call *callback(opp)* when an opportunity is found."""
        self._running = True
        self._scan_callback = callback
        scan_interval = self._cfg.risk_guard.scanner_interval_sec

        # Start WebSocket watchers for all symbols
        adapters = self._exchanges.all()
        all_symbols = set()
        for adapter in adapters.values():
            all_symbols.update(adapter.symbols)

        for adapter in adapters.values():
            try:
                await adapter.start_funding_rate_watchers(list(all_symbols))
            except Exception as e:
                logger.warning(f"Failed to start watchers for {adapter.exchange_id}: {e}")

        # Wire up hot-scan queue so every fresh price update triggers immediate re-evaluation.
        for adapter in adapters.values():
            if hasattr(adapter, "register_price_update_queue"):
                adapter.register_price_update_queue(self._hot_queue)

        # Run hot-scan loop concurrently alongside the periodic full scan.
        self._hot_scan_task = asyncio.create_task(
            self._hot_scan_loop(callback), name="hot-scan"
        )
        self._hot_scan_task.add_done_callback(_hot_scan_task_done)

        # Run mini-OB refresh loop: keeps ask/bid fresh for top stale candidates.
        self._ob_refresh_task = asyncio.create_task(
            self._ob_refresh_loop(), name="ob-refresh"
        )
        self._ob_refresh_task.add_done_callback(_ob_refresh_task_done)

        logger.info(
            f"Scanner started (interval: {scan_interval}s, WebSocket monitoring {len(all_symbols)} symbols)",
            extra={"action": "scanner_start"},
        )

        while self._running:
            try:
                # Refresh market data (fees, specs) if stale — no-op on most cycles.
                # Circuit breaker: skip adapters that have hit the error threshold
                # and are still within their backoff window.
                # (Constants _CB_MAX_ERRORS / _CB_BACKOFF_SEC defined at module level.)
                _now_t = time.monotonic()
                _reload_adapters = [
                    (eid, a)
                    for eid, a in self._exchanges.all().items()
                    if _now_t >= self._exchange_backoff_until.get(eid, 0.0)
                ]
                _reload_results = await asyncio.gather(
                    *[a.maybe_reload_markets() for _, a in _reload_adapters],
                    return_exceptions=True,
                )
                for _eid, _res in zip(
                    [e for e, _ in _reload_adapters], _reload_results
                ):
                    if isinstance(_res, Exception):
                        _cnt = self._exchange_consecutive_errors.get(_eid, 0) + 1
                        self._exchange_consecutive_errors[_eid] = _cnt
                        if _cnt >= _CB_MAX_ERRORS:
                            self._exchange_backoff_until[_eid] = _now_t + _CB_BACKOFF_SEC
                            logger.error(
                                f"[circuit-breaker] {_eid}: {_cnt} consecutive reload "
                                f"failures — backing off for {int(_CB_BACKOFF_SEC)}s: {_res}",
                                extra={"exchange": _eid, "action": "circuit_breaker_open"},
                            )
                        elif _cnt == 1:
                            logger.warning(
                                f"[circuit-breaker] {_eid}: reload error "
                                f"({_cnt}/{_CB_MAX_ERRORS}): {_res}"
                            )
                    else:
                        if self._exchange_consecutive_errors.pop(_eid, None) is not None:
                            logger.info(
                                f"[circuit-breaker] {_eid}: reload recovered — circuit closed"
                            )
                        self._exchange_backoff_until.pop(_eid, None)
                self._early_dispatched = set()
                opps = await self.scan_all()

                # ── Order-book enrichment for stale-price candidates ─────
                # Symbols whose ticker stream lacks ask/bid get flagged
                # stale_price=True.  For the most promising ones (funding
                # rate above half the minimum threshold), fetch L1 from the
                # order book so the scanner can compute a real price spread.
                _ob_min_spread = self._cfg.trading_params.min_funding_spread / 2
                _stale_candidates = [
                    o for o in opps
                    if getattr(o, "stale_price", False)
                    and o.net_edge_pct >= _ob_min_spread
                ]
                if _stale_candidates:
                    # Collect unique (exchange, symbol) pairs that need OB data
                    _ob_tasks: set[tuple[str, str]] = set()
                    for o in _stale_candidates:
                        adapters = self._exchanges.all()
                        long_adapter = adapters.get(o.long_exchange)
                        short_adapter = adapters.get(o.short_exchange)
                        if long_adapter and not long_adapter.has_live_ask(o.symbol):
                            _ob_tasks.add((o.long_exchange, o.symbol))
                        if short_adapter and not short_adapter.has_live_bid(o.symbol):
                            _ob_tasks.add((o.short_exchange, o.symbol))

                    if _ob_tasks:
                        _ob_sem = asyncio.Semaphore(6)
                        async def _fetch_ob(eid: str, sym: str) -> None:
                            async with _ob_sem:
                                adapter = self._exchanges.all().get(eid)
                                if adapter:
                                    await adapter.fetch_top_of_book(sym)

                        await asyncio.gather(
                            *[_fetch_ob(eid, sym) for eid, sym in _ob_tasks],
                            return_exceptions=True,
                        )
                        if logger.isEnabledFor(logging.DEBUG):
                            logger.debug(
                                f"[OB-fallback] Fetched order book L1 for "
                                f"{len(_ob_tasks)} (exchange, symbol) pairs",
                                extra={"action": "ob_fallback"},
                            )

                        # Re-evaluate only the affected pairs with fresh ask/bid
                        _re_eval_adapters = self._exchanges.all()
                        _re_eval_set: set[tuple[str, str, str]] = set()
                        for o in _stale_candidates:
                            _re_eval_set.add((o.symbol, o.long_exchange, o.short_exchange))

                        _funding_cache: dict[str, dict[str, dict]] = {}
                        for sym, long_eid, short_eid in _re_eval_set:
                            if sym not in _funding_cache:
                                _funding_cache[sym] = {}
                            for eid in (long_eid, short_eid):
                                if eid not in _funding_cache[sym]:
                                    cached = _re_eval_adapters[eid].get_funding_rate_cached(sym)
                                    if cached:
                                        _funding_cache[sym][eid] = cached

                        _refreshed: list[OpportunityCandidate] = []
                        for sym, long_eid, short_eid in _re_eval_set:
                            sym_funding = _funding_cache.get(sym, {})
                            if long_eid in sym_funding and short_eid in sym_funding:
                                new_opp = await self._evaluate_pair(
                                    sym, long_eid, short_eid,
                                    sym_funding, _re_eval_adapters,
                                )
                                if new_opp:
                                    _refreshed.append(new_opp)

                        # Replace old stale entries with refreshed evaluations
                        _stale_keys = {
                            (o.symbol, o.long_exchange, o.short_exchange)
                            for o in _stale_candidates
                        }
                        opps = [
                            o for o in opps
                            if (o.symbol, o.long_exchange, o.short_exchange) not in _stale_keys
                        ]
                        opps.extend(_refreshed)
                        logger.info(
                            f"📖 OB fallback: enriched {len(_refreshed)}/{len(_stale_candidates)} "
                            f"stale candidates with live order-book ask/bid",
                            extra={"action": "ob_fallback_done"},
                        )

                # ── Update mini-OB refresh targets ───────────────────────
                # Collect (exchange, symbol) pairs that still lack live
                # ask/bid from the current opportunities so the background
                # _ob_refresh_loop keeps them fresh between full scans.
                _new_ob_targets: set[tuple[str, str]] = set()
                _ob_consider = sorted(
                    opps, key=lambda o: o.net_edge_pct, reverse=True,
                )
                _adapters_snapshot = self._exchanges.all()
                for o in _ob_consider:
                    if len(_new_ob_targets) >= _OB_REFRESH_MAX_TARGETS:
                        break
                    long_a = _adapters_snapshot.get(o.long_exchange)
                    short_a = _adapters_snapshot.get(o.short_exchange)
                    if long_a and not long_a.has_live_ask(o.symbol):
                        _new_ob_targets.add((o.long_exchange, o.symbol))
                    if short_a and not short_a.has_live_bid(o.symbol):
                        _new_ob_targets.add((o.short_exchange, o.symbol))
                self._ob_refresh_targets = _new_ob_targets

                # Split qualified (tradeable) and display-only
                qualified_opps = [o for o in opps if o.qualified]
                all_opps = list(opps)

                # Sort for DISPLAY: near-term opportunities (payment within 1h) first,
                # then by funding-only net_edge_pct (stable — does NOT change with live
                # price ticks).  Avoid using immediate_net_pct or price_spread_pct as
                # sort keys; those fluctuate every scan and cause constant rank-shuffling
                # which makes the front-end list flicker.
                #
                # Stability measures:
                #  • net_edge_pct is quantized to 2 dp so micro-drift (±0.001%) does
                #    NOT cause two items to swap ranks back-and-forth.
                #  • A deterministic tiebreaker (symbol name) guarantees that items
                #    with identical scores keep a fixed order across scans.
                _now_ms = time.time() * 1000
                _one_hour_ms = 3600_000
                _tier_rank = {"top": 3, "medium": 2, "weak": 1, "adverse": -1}
                all_opps.sort(
                    key=lambda o: (
                        0 if o.entry_tier == "adverse" else 1,
                        1 if (o.next_funding_ms is not None and (o.next_funding_ms - _now_ms) <= _one_hour_ms) else 0,
                        _tier_rank.get(o.entry_tier or "", 0),
                        round(float(o.net_edge_pct), 2),
                        o.symbol,  # stable tiebreaker — prevents flickering
                    ),
                    reverse=True,
                )
                qualified_opps.sort(
                    key=lambda o: (
                        _tier_rank.get(o.entry_tier or "", 0),
                        round(float(o.net_edge_pct), 2),
                        o.symbol,
                    ),
                    reverse=True,
                )

                # Display top 5: qualified first, then fill with display-only.
                # Sticky hysteresis: items already in the previous display get a
                # bonus so they are not swapped out by newcomers with a trivially
                # higher net spread (prevents UI flicker).
                #
                # Retention: if an incumbent disappears from scan results for
                # ≤ 2 cycles (cache miss, WS lag), inject it from cache to
                # avoid jarring 1-cycle gaps.
                _MAX_RETAIN_CYCLES = 2
                _all_keys = {
                    f"{o.symbol}|{o.long_exchange}|{o.short_exchange}"
                    for o in all_opps
                }
                for prev_key, (prev_opp, age) in list(self._prev_display_opps.items()):
                    if prev_key not in _all_keys and age < _MAX_RETAIN_CYCLES:
                        all_opps.append(prev_opp)

                _STICKY_BONUS = 0.10  # 0.10% net bonus for incumbents
                def _display_sort_key(o):
                    opp_key = f"{o.symbol}|{o.long_exchange}|{o.short_exchange}"
                    bonus = _STICKY_BONUS if opp_key in self._prev_display_keys else 0.0
                    # Soft stale penalty instead of binary barrier.
                    # Stale items CAN still rank highly if their net edge is
                    # sufficiently above the penalty — prevents complete
                    # top-5 list swaps when items toggle stale/non-stale.
                    stale_pen = _STALE_DISPLAY_PENALTY if getattr(o, "stale_price", False) else 0.0
                    # entry_tier is NOT used as a sort dimension — it depends
                    # on live price_spread which fluctuates every tick and
                    # caused items to jump tiers (medium→top) abruptly.
                    return (
                        0 if o.entry_tier == "adverse" else 1,
                        1 if o.qualified else 0,
                        1 if (o.next_funding_ms is not None and (o.next_funding_ms - _now_ms) <= _one_hour_ms) else 0,
                        round(float(o.net_edge_pct) + bonus - stale_pen, 1),
                        o.symbol,
                    )
                all_opps.sort(key=_display_sort_key, reverse=True)

                display_top = all_opps[:5]
                # Update sticky keys + retain cache for next cycle
                self._prev_display_keys = set()
                new_opps_cache: Dict[str, tuple] = {}
                for o in display_top:
                    opp_key = f"{o.symbol}|{o.long_exchange}|{o.short_exchange}"
                    self._prev_display_keys.add(opp_key)
                    # If item was in scan results → age 0; if retained → age + 1
                    old_age = self._prev_display_opps.get(opp_key, (None, -1))[1]
                    new_age = 0 if opp_key in _all_keys else old_age + 1
                    new_opps_cache[opp_key] = (o, new_age)
                self._prev_display_opps = new_opps_cache

                if display_top:
                    now_ts = time.time()
                    if now_ts - self._last_top_log_ts >= _TOP_OPPS_LOG_INTERVAL_SEC:
                        self._last_top_log_ts = now_ts
                        if qualified_opps:
                            logger.info(
                                "📊 TOP 5 OPPORTUNITIES (near-term first, then by Net)",
                                extra={"action": "top_opportunities"},
                            )
                        else:
                            best_net = float(all_opps[0].net_edge_pct) if all_opps else 0.0
                            logger.info(
                                f"⚠️ No qualified opportunities now (best display net={best_net:+.4f}%). Showing display-only top 5.",
                                extra={"action": "top_opportunities_empty"},
                            )
                        for idx, opp in enumerate(display_top, 1):
                            immediate_spread = (
                                (-opp.long_funding_rate) + opp.short_funding_rate
                            ) * Decimal("100")
                            q_mark = "✅" if opp.qualified else "○ "
                            reject_reason = ""
                            if not opp.qualified:
                                if opp.net_edge_pct <= Decimal("0"):
                                    reject_reason = " [REJECT: NET<=0]"
                                elif opp.entry_tier == "adverse":
                                    reject_reason = " [REJECT: ADVERSE]"
                                else:
                                    reject_reason = " [REJECT: RULES]"
                            tier_mark = f" [{opp.entry_tier.upper()}]" if opp.entry_tier else ""
                            price_mark = f" P={float(opp.price_spread_pct):+.2f}%" if opp.price_spread_pct else ""
                            logger.info(
                                f"  {idx}. {q_mark} {opp.symbol} | {opp.long_exchange}↔{opp.short_exchange} | "
                                f"L={opp.long_funding_rate:.6f} S={opp.short_funding_rate:.6f} | "
                                f"Spread: {immediate_spread:.4f}% | Net: {opp.net_edge_pct:.4f}%{tier_mark}{price_mark}{reject_reason} | "
                                f"/h: {opp.hourly_rate_pct:.4f}% ({opp.min_interval_hours}h)",
                                extra={
                                    "action": "opportunity",
                                    "data": {
                                        "rank": idx,
                                        "symbol": opp.symbol,
                                        "funding_spread_pct": opp.funding_spread_pct,
                                        "net_pct": opp.net_edge_pct,
                                        "pair": f"{opp.long_exchange}_{opp.short_exchange}",
                                    },
                                },
                            )
                        if self._publisher:
                            await self._publisher.publish_log(
                                "INFO",
                                "Top 5 opportunities updated (5 min interval)",
                            )

                    # Publish ALL display opportunities to Redis for frontend
                    # — but only when the list has changed meaningfully (hysteresis).
                    # Fingerprint is ORDER-INDEPENDENT (sorted) so that pure rank
                    # reshuffles of the same 5 items do NOT trigger a publish.
                    _published = await self._publish_display_if_changed(display_top)
                    if _published and now_ts - self._last_top_log_ts < 1:
                        await self._publisher.publish_log(
                            "INFO",
                            f"Top 5 updated: {len(qualified_opps)} qualified, {len(all_opps) - len(qualified_opps)} display-only"
                        )

                    # Send opportunities to controller
                    execute_only_best = self._cfg.trading_params.execute_only_best_opportunity

                    # Skip opportunities already dispatched early during scan_all()
                    _remaining_qualified = [
                        o for o in qualified_opps
                        if f"{o.symbol}|{o.long_exchange}|{o.short_exchange}" not in self._early_dispatched
                    ]
                    if execute_only_best and _remaining_qualified:
                        # Send best opportunity PER exchange pair
                        seen_pairs: set[tuple[str, str]] = set()
                        best_per_pair: list = []
                        for opp in _remaining_qualified:
                            pair = tuple(sorted([opp.long_exchange, opp.short_exchange]))
                            if pair not in seen_pairs:
                                seen_pairs.add(pair)
                                best_per_pair.append(opp)
                        for opp in best_per_pair:
                            logger.info(
                                f"🎯 Sending BEST for {opp.long_exchange}↔{opp.short_exchange}: "
                                f"{opp.symbol} net={opp.net_edge_pct:.4f}%"
                            )
                            _task_name = (
                                f"scan-entry:{opp.symbol}"
                                f"|{opp.long_exchange}|{opp.short_exchange}"
                            )
                            _t = asyncio.create_task(
                                callback(opp), name=_task_name,
                            )
                            _t.add_done_callback(_hot_entry_task_done)
                    elif _remaining_qualified:
                        # Send top qualified opportunities — controller handles further filtering
                        for opp in _remaining_qualified[:5]:
                            _task_name = (
                                f"scan-entry:{opp.symbol}"
                                f"|{opp.long_exchange}|{opp.short_exchange}"
                            )
                            _t = asyncio.create_task(
                                callback(opp), name=_task_name,
                            )
                            _t.add_done_callback(_hot_entry_task_done)
                else:
                    if self._publisher:
                        await self._publisher.publish_opportunities([])
                        if time.time() - self._last_top_log_ts >= _TOP_OPPS_LOG_INTERVAL_SEC:
                            self._last_top_log_ts = time.time()
                            await self._publisher.publish_log("INFO", "Top 5 updated: 0 opportunities found")

                # ── Near-window watch ────────────────────────────────
                # Identify display-only candidates whose funding enters the
                # 15-min entry window within the next 5 minutes.  The hot-scan
                # loop injects these on its 1s timer so they are re-evaluated
                # every second — much faster than the ~3-min full scan cycle.
                _tp_nw = self._cfg.trading_params
                _now_ms_nw = time.time() * 1000
                _window_min_nw = float(_tp_nw.narrow_entry_window_minutes)
                _margin_min_nw = 5.0
                _old_watch = self._near_window_watch
                self._near_window_watch = set()
                for o in (all_opps if opps else []):
                    if (not o.qualified
                            and float(o.net_edge_pct) >= float(_tp_nw.min_funding_spread)
                            and o.entry_tier not in (None, "adverse")
                            and o.next_funding_ms is not None):
                        _mins_nw = (o.next_funding_ms - _now_ms_nw) / 60_000
                        if 0 < _mins_nw <= _window_min_nw + _margin_min_nw:
                            self._near_window_watch.add(o.symbol)
                if self._near_window_watch and self._near_window_watch != _old_watch:
                    logger.info(
                        f"⏰ {len(self._near_window_watch)} symbol(s) approaching entry window "
                        f"— fast-tracking via hot-scan: "
                        f"{sorted(self._near_window_watch)[:5]}",
                        extra={"action": "near_window_watch"},
                    )
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.warning(f"Scan cycle error (transient): {e}")
                if self._publisher:
                    try:
                        await self._publisher.publish_log("WARNING", f"Scan error: {e}")
                    except Exception as exc:
                        logger.debug(f"Scan error log publish failed: {exc}")
            await asyncio.sleep(scan_interval)

    def stop(self) -> None:
        self._running = False
        if self._hot_scan_task and not self._hot_scan_task.done():
            self._hot_scan_task.cancel()
        if self._ob_refresh_task and not self._ob_refresh_task.done():
            self._ob_refresh_task.cancel()
        # Cancel all WebSocket watcher tasks via the adapter's public method,
        # which avoids accessing private attributes from outside the class.
        for adapter in self._exchanges.all().values():
            result = adapter.cancel_ws_tasks()
            if inspect.isawaitable(result):
                # Some tests use AsyncMock adapters where methods are awaitable.
                # Resolve the awaitable to avoid "coroutine was never awaited"
                # warnings while keeping stop() synchronous in production.
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(result)
                except RuntimeError:
                    asyncio.run(result)

    # ── Hot-scan loop ───────────────────────────────────────────

    async def _hot_scan_loop(self, callback) -> None:
        """Re-evaluate symbols that received a fresh price update without waiting for the
        full scan cycle.  The debounce window (_HOT_DEBOUNCE_MS) collapses bursts of
        rapid ticker updates into a single evaluation pass per symbol."""
        debounce_ms = _HOT_DEBOUNCE_MS / 1000
        # P2-3: Evict stale entries from per-route fire-time dicts every N iterations
        # to prevent unbounded growth over multi-day uptime (200 symbols × 6 routes =
        # ~1200 entries that otherwise accumulate forever, including delisted pairs).
        _hot_loop_iter: int = 0
        _EVICT_INTERVAL_ITERS: int = 1000
        _EVICT_AFTER_SEC: float = _HOT_CALLBACK_COOLDOWN_SEC * 10  # 100 s
        while self._running:
            try:
                # Block until at least one update arrives (or 1s timeout to recheck _running)
                _got_ws_tick = False
                try:
                    _, first_sym = await asyncio.wait_for(self._hot_queue.get(), timeout=1.0)
                    _got_ws_tick = True
                except asyncio.TimeoutError:
                    # No WS tick — but near-window candidates still need evaluation.
                    if not self._near_window_watch:
                        continue

                # Collect all updates that arrive within the debounce window
                dirty: set[str] = {first_sym} if _got_ws_tick else set()
                await asyncio.sleep(debounce_ms)
                if _got_ws_tick:
                    while not self._hot_queue.empty():
                        try:
                            _, sym = self._hot_queue.get_nowait()
                            dirty.add(sym)
                        except asyncio.QueueEmpty:
                            break

                # Timer-based: inject near-window candidates that need fast-tracking.
                # These micro-cap symbols rarely get WS ticks but need second-level
                # evaluation to catch the exact moment funding enters the window.
                if self._near_window_watch:
                    dirty |= self._near_window_watch

                # Bail early if common_symbols not yet built
                if not self._common_symbols_cache:
                    continue
                hot_symbols = dirty & self._common_symbols_cache
                if not hot_symbols:
                    continue

                # P2-3: Periodically evict stale entries from all three fire-time dicts.
                _hot_loop_iter += 1
                if _hot_loop_iter % _EVICT_INTERVAL_ITERS == 0:
                    _t_evict = time.monotonic()
                    self._hot_cb_last_fire = {
                        k: v for k, v in self._hot_cb_last_fire.items()
                        if _t_evict - v < _EVICT_AFTER_SEC
                    }
                    self._opp_log_last_fire = {
                        k: v for k, v in self._opp_log_last_fire.items()
                        if _t_evict - v < _EVICT_AFTER_SEC
                    }
                    self._opp_log_signature = {
                        k: v for k, v in self._opp_log_signature.items()
                        if k in self._opp_log_last_fire
                    }

                adapters = self._exchanges.all()
                exchange_ids = list(adapters.keys())
                if len(exchange_ids) < 2:
                    continue

                # P1-4: The _hot_candidates filter was added for CPU reduction but creates
                # a blind spot: symbols whose funding rate JUST crossed the threshold
                # (emergent spikes) are invisible until the next full scan_all() cycle
                # (up to 10s away), by which time the entry window may have closed.
                # The callback path already debounces via _HOT_CALLBACK_COOLDOWN_SEC,
                # so removing the filter does not flood the controller.
                # The heavy REST work (fetch_top_of_book) is still gated by the stale-
                # price refresh logic inside _evaluate_direction.

                cooled_symbols = await self._redis.get_cooled_down_symbols(list(hot_symbols))

                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug(
                        f"[hot-scan] Evaluating {len(hot_symbols)} symbol(s): "
                        f"{sorted(hot_symbols)[:5]}{'...' if len(hot_symbols) > 5 else ''}",
                        extra={"action": "hot_scan_start"},
                    )

                # P3-1: Collect every fresh evaluation so we can refresh
                # the published display rows for any symbol that's currently
                # in the dashboard's top-5. Without this the dashboard waits
                # for the next 3-min full-scan to see disqualify_reason flips
                # (e.g. a row going from POT 🍯 → ⏰ funding_no_imminent the
                # second a funding payment fires).
                _hot_evals: list[OpportunityCandidate] = []

                for symbol in hot_symbols:
                    try:
                        # cheap=True: skip _build_opportunity REST calls (balance+ticker+VWAP).
                        # The hot path only needs a WS-cache qualification signal;
                        # suggested_qty=0 is safe because the entry sizer always
                        # recalculates from order_qty at execution time (P1-1).
                        opps = await self._scan_symbol(
                            symbol, adapters, exchange_ids, cooled_symbols, cheap=True,
                        )
                        _hot_evals.extend(opps)
                        for opp in opps:
                            if opp.qualified:
                                # P1-2: Key debounce by route, not just symbol.
                                # With 3+ exchanges a symbol can have multiple qualified
                                # routes (e.g. Binance↔Bybit AND Binance↔OKX). The old
                                # symbol-only key silenced the second route for 10 s even
                                # when it had a higher net spread.
                                _cb_key = f"{opp.symbol}|{opp.long_exchange}|{opp.short_exchange}"
                                _now = time.monotonic()
                                _last = self._hot_cb_last_fire.get(_cb_key, 0.0)
                                if _now - _last < _HOT_CALLBACK_COOLDOWN_SEC:
                                    if logger.isEnabledFor(logging.DEBUG):
                                        logger.debug(
                                            f"[hot-scan] Debounced {opp.symbol} "
                                            f"({_now - _last:.1f}s since last fire)",
                                        )
                                    continue
                                self._hot_cb_last_fire[_cb_key] = _now
                                logger.info(
                                    f"🔥 [hot-scan] {opp.symbol} "
                                    f"{opp.long_exchange}↔{opp.short_exchange} "
                                    f"net={opp.net_edge_pct:.4f}%",
                                    extra={"action": "hot_scan_opportunity", "symbol": opp.symbol},
                                )
                                # Fire-and-forget with supervision: entry path runs in its
                                # own task so the discovery loop is never blocked by order
                                # placement, pre-flight REST, or lock acquisition.
                                _task_name = (
                                    f"hot-entry:{opp.symbol}"
                                    f"|{opp.long_exchange}|{opp.short_exchange}"
                                )
                                _t = asyncio.create_task(
                                    callback(opp), name=_task_name,
                                )
                                _t.add_done_callback(_hot_entry_task_done)
                    except asyncio.CancelledError:
                        return
                    except Exception as exc:
                        logger.warning(f"[hot-scan] Error evaluating {symbol}: {exc}")

                # ── P3-1: refresh dashboard rows from this hot pass ─────
                # Overlay the freshly-evaluated opps onto the previously
                # published display_top (cached in _prev_display_opps) so
                # the dashboard sees state changes within seconds rather
                # than waiting up to 3 minutes for the next full scan.
                # Only rows whose (symbol, long, short) match a hot eval
                # are replaced; others keep their stale value (which is
                # fine — fingerprint will only flip if the change is on a
                # currently-displayed row).
                if _hot_evals and self._prev_display_opps:
                    _hot_index: Dict[str, OpportunityCandidate] = {
                        f"{o.symbol}|{o.long_exchange}|{o.short_exchange}": o
                        for o in _hot_evals
                    }
                    _refreshed_top: list[OpportunityCandidate] = []
                    for opp_key, (cached_opp, _age) in self._prev_display_opps.items():
                        _refreshed_top.append(_hot_index.get(opp_key, cached_opp))
                    if _refreshed_top:
                        try:
                            await self._publish_display_if_changed(_refreshed_top)
                        except Exception as exc:
                            logger.warning(
                                f"[hot-scan] publish refresh failed: {exc}",
                                extra={"action": "hot_scan_publish_failed"},
                            )

            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.warning(f"[hot-scan] Unexpected loop error: {exc}")

    # ── Mini-OB refresh loop ────────────────────────────────────

    async def _ob_refresh_loop(self) -> None:
        """Continuously fetch order-book L1 for top stale candidates.

        Targets are set by the main scan loop after each cycle: up to
        ``_OB_REFRESH_MAX_TARGETS`` (exchange, symbol) pairs that lack
        live ask/bid data.  This loop fetches them every
        ``_OB_REFRESH_INTERVAL_SEC`` seconds so that price-spread
        estimates stay reasonably fresh between full scan cycles.
        """
        sem = asyncio.Semaphore(_OB_REFRESH_CONCURRENCY)

        async def _fetch_one(adapter: "ExchangeAdapter", symbol: str) -> None:
            async with sem:
                try:
                    await adapter.fetch_top_of_book(symbol)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    if logger.isEnabledFor(logging.DEBUG):
                        logger.debug(
                            f"[ob-refresh] fetch failed {adapter.exchange_id}:{symbol}: {exc}",
                        )

        while self._running:
            try:
                await asyncio.sleep(_OB_REFRESH_INTERVAL_SEC)
                targets = list(self._ob_refresh_targets)
                if not targets:
                    continue
                adapters = self._exchanges.all()
                tasks: list[asyncio.Task] = []
                for eid, sym in targets:
                    adapter = adapters.get(eid)
                    if adapter is None:
                        continue
                    tasks.append(_fetch_one(adapter, sym))
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug(
                        f"[ob-refresh] Refreshed {len(tasks)} OB targets",
                        extra={"action": "ob_refresh_cycle"},
                    )
            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.warning(f"[ob-refresh] Loop error: {exc}")

    # ── Scan logic ───────────────────────────────────────────────

    async def scan_all(self) -> List[OpportunityCandidate]:
        """Scan every (symbol × exchange-pair) for funding edge."""
        t0 = time.monotonic()
        adapters = self._exchanges.all()
        exchange_ids = list(adapters.keys())
        if len(exchange_ids) < 2:
            return []

        # Common symbols set is stable between scans.
        # Rebuild only every 60 calls (~5 min at 5 s intervals) or when exchanges change.
        self._cache_scan_count += 1
        if (
            self._common_symbols_cache is None
            or exchange_ids != self._cache_exchange_ids
            or self._cache_scan_count % 60 == 0
        ):
            symbol_sets = [set(adapters[eid].symbols) for eid in exchange_ids]
            all_symbols = set.union(*symbol_sets)
            symbol_counts = {s: sum(1 for ss in symbol_sets if s in ss) for s in all_symbols}
            self._common_symbols_cache = {s for s, c in symbol_counts.items() if c >= 2}
            self._cache_exchange_ids = exchange_ids
        common_symbols = self._common_symbols_cache

        # Batch cooldown check: one Redis pipeline instead of N round-trips
        cooled_symbols = await self._redis.get_cooled_down_symbols(list(common_symbols))

        parallelism = self._cfg.execution.scan_parallelism
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(f"Scanning {len(common_symbols)} symbols (on 2+ exchanges) across {len(exchange_ids)} exchanges (parallelism={parallelism})")

        results: List[OpportunityCandidate] = []

        symbol_list = list(common_symbols)
        semaphore = asyncio.Semaphore(parallelism)
        # P1: Early dispatch — send qualified opportunities to execution
        # immediately as they're found, instead of waiting for all 629 symbols
        # to complete in the gather.  This prevents 15+ minute delays when the
        # full scan takes longer than the entry window.
        _early_cb = self._scan_callback
        _execute_best = self._cfg.trading_params.execute_only_best_opportunity
        _early_seen_pairs: set[tuple[str, str]] = set()

        # Tier priority for sorting: TOP first, then MEDIUM, then WEAK/None
        _TIER_PRIORITY = {"TOP": 0, "MEDIUM": 1, "WEAK": 2}
        _MAX_EARLY_PER_SYMBOL = 2  # dispatch at most 2 best routes per symbol

        async def bounded_scan(symbol: str) -> List[OpportunityCandidate]:
            async with semaphore:
                opps = await self._scan_symbol(symbol, adapters, exchange_ids, cooled_symbols, cheap=True)
                # Dispatch qualified opportunities immediately
                if _early_cb and opps:
                    qualified_opps = [o for o in opps if o.qualified]
                    # Sort by tier (TOP first) then net_edge_pct descending
                    # so the best route grabs the execution lock first.
                    qualified_opps.sort(
                        key=lambda o: (
                            _TIER_PRIORITY.get((o.entry_tier or "").upper(), 9),
                            -o.net_edge_pct,
                        )
                    )
                    _dispatched_count = 0
                    for opp in qualified_opps:
                        if _dispatched_count >= _MAX_EARLY_PER_SYMBOL:
                            break
                        _route_key = f"{opp.symbol}|{opp.long_exchange}|{opp.short_exchange}"
                        if _execute_best:
                            _pair = tuple(sorted([opp.long_exchange, opp.short_exchange]))
                            if _pair in _early_seen_pairs:
                                continue
                            _early_seen_pairs.add(_pair)
                        self._early_dispatched.add(_route_key)
                        _dispatched_count += 1
                        logger.info(
                            f"⚡ [early-dispatch] {opp.symbol} "
                            f"{opp.long_exchange}↔{opp.short_exchange} "
                            f"tier={opp.entry_tier} net={opp.net_edge_pct:.4f}% "
                            f"price_spread={opp.price_spread_pct:+.4f}% — dispatching immediately",
                            extra={"action": "early_dispatch", "symbol": opp.symbol},
                        )
                        _task_name = (
                            f"early-entry:{opp.symbol}"
                            f"|{opp.long_exchange}|{opp.short_exchange}"
                        )
                        _t = asyncio.create_task(
                            _early_cb(opp), name=_task_name,
                        )
                        _t.add_done_callback(_hot_entry_task_done)
                return opps

        gathered = await asyncio.gather(*[bounded_scan(s) for s in symbol_list], return_exceptions=True)

        for symbol_results in gathered:
            if isinstance(symbol_results, Exception):
                logger.debug(f"Symbol scan error: {symbol_results}")
                continue
            if symbol_results:
                results.extend(symbol_results)

        elapsed = time.monotonic() - t0
        elapsed_for_log = elapsed
        if elapsed > _SUSPEND_GAP_SECONDS:
            logger.warning(
                f"⏸️ Large scan elapsed detected ({elapsed:.1f}s) — likely system sleep/resume; "
                f"excluding from performance telemetry",
                extra={
                    "action": "scan_elapsed_anomaly",
                    "data": {"elapsed": round(elapsed, 1), "threshold": _SUSPEND_GAP_SECONDS},
                },
            )
            elapsed_for_log = 0.0
        if results:
            results.sort(key=lambda o: o.immediate_net_pct, reverse=True)
            logger.info(
                f"✅ Scan completed: {len(results)} opportunities from {len(common_symbols)} symbols in {elapsed_for_log:.1f}s",
                extra={"action": "scan_complete", "data": {"count": len(results), "elapsed": round(elapsed_for_log, 1)}},
            )
        else:
            logger.info(
                f"✅ Scan completed: 0 opportunities from {len(common_symbols)} symbols in {elapsed_for_log:.1f}s",
                extra={"action": "scan_complete", "data": {"count": 0, "elapsed": round(elapsed_for_log, 1)}},
            )
        # Update hot-candidates shortlist for the event-driven hot-scan path.
        # Include any symbol that has a non-trivial net edge (>= threshold / 4)
        # so the hot-scan proactively monitors "almost interesting" symbols too.
        _min_spread = self._cfg.trading_params.min_funding_spread
        _hot_threshold = _min_spread / 4 if _min_spread > 0 else Decimal("0.001")
        new_candidates = {
            o.symbol for o in results
            if o.qualified or abs(o.net_edge_pct) >= _hot_threshold
        }
        if new_candidates != self._hot_candidates:
            self._hot_candidates = new_candidates
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(
                    f"[hot-scan] Candidates updated: {len(new_candidates)} symbols "
                    f"(threshold={float(_hot_threshold):.6f}%)",
                    extra={"action": "hot_candidates_updated"},
                )
        return results

    async def _scan_symbol(
        self, symbol: str, adapters: Dict[str, "ExchangeAdapter"], exchange_ids: List[str],
        cooled_symbols: set[str] = frozenset(),
        cheap: bool = False,
    ) -> List[OpportunityCandidate]:
        """Scan a single symbol for opportunities using WebSocket-cached rates."""
        if symbol in cooled_symbols:
            return []

        funding: Dict[str, dict] = {}
        eligible_eids = [eid for eid in exchange_ids if symbol in adapters[eid].symbols]
        if len(eligible_eids) < 2:
            return []

        for eid in eligible_eids:
            cached = adapters[eid].get_funding_rate_cached(symbol)
            if cached:
                funding[eid] = cached

        if len(funding) < 2:
            return []

        if logger.isEnabledFor(logging.DEBUG):
            funding_detail = " | ".join(
                f"{eid}: rate={funding[eid]['rate']:.8f} ({funding[eid]['rate']*100:.6f}%, interval={funding[eid].get('interval_hours', 8)}h"
                for eid in sorted(funding.keys())
            )
            logger.debug(
                f"[ALL_RATES] [{symbol}] SCANNER RETRIEVED RATES: {funding_detail}",
                extra={
                    "action": "scanner_rates_retrieved",
                    "symbol": symbol,
                },
            )

        results = []
        eids = list(funding.keys())
        for i in range(len(eids)):
            for j in range(i + 1, len(eids)):
                opp = await self._evaluate_pair(
                    symbol, eids[i], eids[j], funding, adapters,
                    cheap=cheap,
                )
                if opp:
                    results.append(opp)

        return results




