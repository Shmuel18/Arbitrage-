"""Scanner evaluator mixin — pair evaluation and opportunity building.

Extracted from scanner.py to keep file sizes manageable.
Contains:
  _classify_tier()        — module-level tier classification helper
  _ScannerEvaluatorMixin  — _evaluate_pair, _evaluate_direction, _build_opportunity
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Dict, List, Optional

from src.core.contracts import EntryTier, OpportunityCandidate, OrderSide, TradeMode
from src.core.logging import get_logger
from src.discovery.calculator import (
    analyze_per_payment_pnl,
    calculate_cherry_pick_edge,
    calculate_fees,
    calculate_funding_spread,
)

if TYPE_CHECKING:
    from src.core.config import Config
    from src.exchanges.adapter import ExchangeAdapter

logger = get_logger("scanner")

_MIN_WINDOW_MINUTES = 30
_MIN_CHERRY_GAP_MINUTES = 30  # income and cost must fire at least this far apart


def _classify_tier(
    tier_net: Decimal,
    price_spread_pct: Decimal,
    total_cost_pct: Decimal,
    min_funding_spread: Decimal,
    weak_min_funding_excess: Decimal,
) -> Optional[str]:
    """Classify a funding opportunity into TOP / MEDIUM / WEAK tier.

    Returns the tier value string, or ``None`` if the opportunity
    doesn't qualify for any tier (adverse spread too large).

    Args:
        tier_net: Net funding after costs (%).
        price_spread_pct: Price spread between exchanges (%).
        total_cost_pct: Total fees/costs (%).
        min_funding_spread: Minimum net funding to qualify for any tier.
        weak_min_funding_excess: Funding must exceed adverse spread by this %.
    """
    if tier_net < min_funding_spread:
        return None
    # price_spread_pct = (ask_long - bid_short) / bid_short
    # Negative = ask_long < bid_short = buy cheap, sell expensive = FAVORABLE → TOP
    # Positive = ask_long > bid_short = buy expensive, sell cheap = ADVERSE
    if price_spread_pct <= Decimal("0"):
        return EntryTier.TOP.value
    if price_spread_pct <= total_cost_pct:
        return EntryTier.MEDIUM.value
    if tier_net - price_spread_pct >= weak_min_funding_excess:
        return EntryTier.WEAK.value
    return None


class _ScannerEvaluatorMixin:
    """Mixin providing pair evaluation logic for Scanner."""

    # ── 24h volume helper ────────────────────────────────────────
    async def _get_24h_volume_usd(
        self,
        eid: str,
        symbol: str,
        adapter: "ExchangeAdapter",
    ) -> Optional[Decimal]:
        """Return cached 24h quote volume (USD) for (exchange, symbol).

        Uses a TTL-bounded in-memory cache (`self._volume_cache`). On cache
        miss/expiry, fetches the ticker via REST and pulls quote volume.

        Per-exchange field mapping (ccxt's `quoteVolume` is USDT-denominated
        for most spot pairs but is often missing on futures/perpetuals — try
        the raw `info` dict before giving up):
          - All:           ticker.quoteVolume
          - KuCoin Fut:    info.turnoverOf24h  (USDT 24h turnover)
                           info.volValue        (alternate name)
          - Bitget:        info.usdtVolume
          - Generic:       info.volume * info.lastTradePrice (base × price)
          - Last resort:   ticker.baseVolume * ticker.last

        Returns ``None`` only if all fallbacks fail — callers treat None as
        "unknown" and fail-closed (block the trade rather than risk a
        thin-book entry).
        """
        cache_key = f"{eid}:{symbol}"
        ttl = float(getattr(self._cfg.trading_params, "volume_cache_ttl_sec", 300))
        cached = self._volume_cache.get(cache_key)
        now = time.time()
        if cached is not None and (now - cached[1]) < ttl:
            return cached[0]
        try:
            ticker = await adapter.get_ticker(symbol)
        except Exception as e:
            logger.debug(
                f"[VOL] {eid}:{symbol} ticker fetch failed: {e}",
                extra={"exchange": eid, "symbol": symbol, "action": "volume_fetch_failed"},
            )
            return None
        if not ticker:
            return None

        info = ticker.get("info") or {}
        # Ordered candidates — first non-None numeric wins. Keys span the
        # "USDT-quote turnover" naming conventions of major futures venues.
        candidates: List = [
            ticker.get("quoteVolume"),
            info.get("turnoverOf24h"),     # KuCoin Futures
            info.get("volValue"),          # KuCoin alt
            info.get("turnover24h"),       # Bybit
            info.get("usdtVolume"),        # Bitget alt
            info.get("quoteVolume"),       # raw passthrough
            info.get("turnover"),          # generic
        ]
        vol: Optional[Decimal] = None
        for raw in candidates:
            if raw is None or raw == "" or raw == "0":
                continue
            try:
                v = Decimal(str(raw))
            except (InvalidOperation, ValueError, TypeError):
                # Exchange returned a non-numeric placeholder (e.g. "N/A");
                # skip this candidate and try the next field in the chain.
                continue
            if v > 0:
                vol = v
                break

        if vol is None:
            # Final fallback: base volume × last price. If parsing fails here
            # we just return None (handled below) rather than blow up the
            # whole scan loop — caller treats None as "vol_unknown".
            try:
                base_v = ticker.get("baseVolume") or info.get("volume")
                last_p = ticker.get("last") or info.get("lastTradePrice") or info.get("lastPrice")
                if base_v is not None and last_p is not None:
                    bv = Decimal(str(base_v))
                    lp = Decimal(str(last_p))
                    if bv > 0 and lp > 0:
                        vol = bv * lp
            except (InvalidOperation, ValueError, TypeError) as exc:
                logger.debug(
                    f"[VOL] {eid}:{symbol} baseVolume×last fallback parse failed: {exc}",
                    extra={"exchange": eid, "symbol": symbol, "action": "volume_fallback_parse_failed"},
                )

        if vol is None or vol <= 0:
            return None
        self._volume_cache[cache_key] = (vol, now)
        return vol

    # ── Evaluate a single pair ───────────────────────────────────

    async def _evaluate_pair(
        self,
        symbol: str,
        eid_a: str,
        eid_b: str,
        funding: Dict[str, dict],
        adapters: Dict[str, "ExchangeAdapter"],
        cheap: bool = False,
    ) -> Optional[OpportunityCandidate]:
        rate_a = funding[eid_a]["rate"]
        rate_b = funding[eid_b]["rate"]
        interval_a = funding[eid_a].get("interval_hours", 8)
        interval_b = funding[eid_b].get("interval_hours", 8)

        # Try both directions, pick the one with the higher funding spread
        # Prefer qualified over unqualified
        best = None
        for long_eid, short_eid in [(eid_a, eid_b), (eid_b, eid_a)]:
            long_rate = funding[long_eid]["rate"]
            short_rate = funding[short_eid]["rate"]
            long_interval = funding[long_eid].get("interval_hours", 8)
            short_interval = funding[short_eid].get("interval_hours", 8)

            opp = await self._evaluate_direction(
                symbol, long_eid, short_eid,
                long_rate, short_rate,
                long_interval, short_interval,
                funding, adapters,
                cheap=cheap,
            )
            if opp is None:
                continue
            if best is None:
                best = opp
            elif opp.qualified and not best.qualified:
                best = opp  # prefer qualified
            elif opp.qualified == best.qualified and opp.net_edge_pct > best.net_edge_pct:
                best = opp  # same qualification level, pick better next-payment net

        return best

    async def _evaluate_direction(
        self,
        symbol: str,
        long_eid: str, short_eid: str,
        long_rate: Decimal, short_rate: Decimal,
        long_interval: int, short_interval: int,
        funding: Dict[str, dict],
        adapters: Dict[str, "ExchangeAdapter"],
        cheap: bool = False,
    ) -> Optional[OpportunityCandidate]:
        """Evaluate one direction (long on A, short on B).

        Entry logic — PURE FUNDING ARBITRAGE:
          1. Compute immediate funding spread: (-long_rate) + short_rate (actual next payment, no 8h normalization)
          2. Per-payment analysis → HOLD (both sides income) or CHERRY_PICK (one income, one cost)
          3. HOLD:        both sides income, imminent spread ≥ min_funding_spread
          4. CHERRY_PICK: income side fires first, collect BEFORE the cost side fires
        """
        tp = self._cfg.trading_params

        # ── Compute funding spread (no 8h normalization) ─────────
        spread_info = calculate_funding_spread(
            long_rate, short_rate,
            long_interval_hours=long_interval,
            short_interval_hours=short_interval,
        )
        immediate_spread = spread_info["immediate_spread_pct"]
        funding_spread = spread_info["funding_spread_pct"]

        # Guard f-string: called for every exchange pair on every scan cycle
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                f"[PAIR_EVAL] [{symbol}] PAIR EVALUATION: "
                f"LONG({long_eid})={long_rate:.8f} ({long_rate*100:.6f}%, {long_interval}h) | "
                f"SHORT({short_eid})={short_rate:.8f} ({short_rate*100:.6f}%, {short_interval}h) | "
                f"Spread={immediate_spread:.4f}% (immediate), {funding_spread:.4f}% (8h norm)",
                extra={
                    "action": "pair_evaluation",
                    "symbol": symbol,
                    "long_eid": long_eid,
                    "short_eid": short_eid,
                    "long_rate": str(long_rate),
                    "short_rate": str(short_rate),
                },
            )

        # ── Per-payment analysis ─────────────────────────────────
        pnl = analyze_per_payment_pnl(long_rate, short_rate)

        # Both sides cost us → skip
        if pnl["both_cost"]:
            return None

        # ── Fees & buffers ───────────────────────────────────────
        # Use the in-memory cache (sync, zero coroutine overhead) when available.
        # Falls back to a REST fetch only on the very first scan after startup.
        long_spec = (
            adapters[long_eid].get_cached_instrument_spec(symbol)
            or await adapters[long_eid].get_instrument_spec(symbol)
        )
        short_spec = (
            adapters[short_eid].get_cached_instrument_spec(symbol)
            or await adapters[short_eid].get_instrument_spec(symbol)
        )
        if not long_spec or not short_spec:
            return None
        fees_pct = calculate_fees(long_spec.taker_fee, short_spec.taker_fee)
        # slippage + safety buffers (fixed costs paid at entry/exit regardless)
        buffers_pct = tp.slippage_buffer_pct + tp.safety_buffer_pct
        total_cost_pct = fees_pct + buffers_pct
        max_market_data_age_ms = int(getattr(tp, "max_market_data_age_ms", 2000))

        # ── Live price basis check (info only — NOT added to entry cost) ──
        price_basis_pct = Decimal("0")
        _live_basis_available = False
        _mark_price_fallback = False
        async def _call_metric(
            adapter: "ExchangeAdapter",
            method_name: str,
            default: object,
        ) -> object:
            method = getattr(adapter, method_name, None)
            if not callable(method):
                return default
            try:
                value = method(symbol)
                if inspect.isawaitable(value):
                    value = await value
                return value
            except Exception:
                return default

        async def _snapshot_top_of_book() -> tuple[Decimal, Decimal, Optional[float], Optional[float], bool]:
            long_ask_age_ms_raw = await _call_metric(adapters[long_eid], "get_best_ask_age_ms", None)
            short_bid_age_ms_raw = await _call_metric(adapters[short_eid], "get_best_bid_age_ms", None)
            long_ask_age = float(long_ask_age_ms_raw) if long_ask_age_ms_raw is not None else None
            short_bid_age = float(short_bid_age_ms_raw) if short_bid_age_ms_raw is not None else None

            long_has_live = bool(await _call_metric(adapters[long_eid], "has_live_ask", True))
            short_has_live = bool(await _call_metric(adapters[short_eid], "has_live_bid", True))
            mark_fallback = not (long_has_live and short_has_live)

            long_price_raw = await _call_metric(adapters[long_eid], "get_best_ask", None)
            short_price_raw = await _call_metric(adapters[short_eid], "get_best_bid", None)
            long_px = Decimal(str(long_price_raw)) if long_price_raw else Decimal("0")
            short_px = Decimal(str(short_price_raw)) if short_price_raw else Decimal("0")
            return long_px, short_px, long_ask_age, short_bid_age, mark_fallback

        try:
            long_price, short_price, long_ask_age_ms, short_bid_age_ms, _mark_price_fallback = await _snapshot_top_of_book()

            _long_stale_by_age = (
                long_ask_age_ms is None or long_ask_age_ms > max_market_data_age_ms
            )
            _short_stale_by_age = (
                short_bid_age_ms is None or short_bid_age_ms > max_market_data_age_ms
            )
            _needs_refresh = (
                _mark_price_fallback
                or long_price <= 0
                or short_price <= 0
                or _long_stale_by_age
                or _short_stale_by_age
            )

            if _needs_refresh:
                await asyncio.gather(
                    adapters[long_eid].fetch_top_of_book(symbol),
                    adapters[short_eid].fetch_top_of_book(symbol),
                    return_exceptions=True,
                )
                long_price, short_price, long_ask_age_ms, short_bid_age_ms, _mark_price_fallback = await _snapshot_top_of_book()
                _long_stale_by_age = (
                    long_ask_age_ms is None or long_ask_age_ms > max_market_data_age_ms
                )
                _short_stale_by_age = (
                    short_bid_age_ms is None or short_bid_age_ms > max_market_data_age_ms
                )

            if _mark_price_fallback:
                logger.debug(
                    f"[{symbol}] Skipping cycle: missing live top-of-book "
                    f"after refresh ({long_eid}, {short_eid})"
                )
                return None
            if long_price <= 0 or short_price <= 0:
                logger.debug(
                    f"[{symbol}] Skipping cycle: invalid top-of-book after refresh "
                    f"({long_eid} ask={long_price}, {short_eid} bid={short_price})"
                )
                return None
            if _long_stale_by_age or _short_stale_by_age:
                logger.debug(
                    f"[{symbol}] Skipping cycle: top-of-book still stale after refresh: "
                    f"{long_eid} ask age={long_ask_age_ms}ms, {short_eid} bid age={short_bid_age_ms}ms, "
                    f"threshold={max_market_data_age_ms}ms"
                )
                return None

            _live_basis_available = True
            raw_basis = (long_price - short_price) / short_price * Decimal("100")
            price_basis_pct = raw_basis  # signed — informational only
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(
                    f"[{symbol}] Entry price basis: {long_eid}=ask({long_price}) vs "
                    f"{short_eid}=bid({short_price}) → {float(price_basis_pct):+.4f}% (info only, not added to cost)"
                )
        except Exception as _basis_err:
            logger.debug(f"[{symbol}] Price basis check failed: {_basis_err}")
            return None

        # Guard f-string: spec.taker_fee formatting on every pair per scan
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                f"[{symbol}] NEV Calculation: "
                f"Spread={immediate_spread:.4f}% (immediate), {funding_spread:.4f}% (8h) - "
                f"Fees={fees_pct:.4f}% (L:{long_spec.taker_fee*100:.4f}% + S:{short_spec.taker_fee*100:.4f}% × 2) - "
                f"Slippage={tp.slippage_buffer_pct:.4f}% - "
                f"Safety={tp.safety_buffer_pct:.4f}% - "
                f"PriceBasis={float(price_basis_pct):+.4f}% (info only)"
            )

        # ── Tier classification (funding arb + price arb) ─────────
        # Use consistent basis formula: (long - short) / short * 100
        # This matches entry_basis_pct calculation in _entry_orders_mixin.py
        # Always compute price_spread_pct from cached prices (even when stale)
        # so the frontend shows the real spread rather than 0.00%.
        # The _live_basis_available / _stale_market_data_gate flags still gate entry.
        if short_price > 0 and long_price > 0:
            price_spread_pct = (long_price - short_price) / short_price * Decimal("100")
        else:
            price_spread_pct = Decimal("0")

        # Net funding after costs (used for tier classification)
        _tier_net = immediate_spread - total_cost_pct
        entry_tier = _classify_tier(
            _tier_net, price_spread_pct, total_cost_pct,
            tp.min_funding_spread, tp.weak_min_funding_excess,
        )

        # Safety policy: reject when live entry basis is so adverse that no
        # tier classification could justify it.  MEDIUM/WEAK tiers already
        # account for a positive price spread (MEDIUM: spread ≤ total_cost,
        # WEAK: funding excess covers it).  The old `> 0` check made those
        # tiers dead code — any micro-cap cross-exchange spread oscillating
        # around zero would randomly reject perfectly viable entries.
        # The execution layer (max_entry_basis_spread_pct) provides a second
        # safety net at order-placement time.
        _adverse_price_gate = (
            _live_basis_available
            and price_spread_pct > Decimal("0")
            and entry_tier is None          # tier classification already rejected
        )
        if _adverse_price_gate:
            entry_tier = "adverse"

        if entry_tier and logger.isEnabledFor(logging.DEBUG):
            tier_emoji = {"top": "🏆", "medium": "📊", "weak": "⚡"}.get(entry_tier, "")
            logger.debug(
                f"[{symbol}] Tier: {tier_emoji} {entry_tier.upper()} | "
                f"Price spread: {float(price_spread_pct):+.4f}% | "
                f"Net funding: {float(_tier_net):.4f}%"
            )

        # ── Gate: price spread too adverse for all tiers ──────────
        _tier_too_adverse = (
            entry_tier is None
            and _live_basis_available
            and _tier_net >= tp.min_funding_spread
        )
        if _tier_too_adverse:
            entry_tier = "adverse"
            logger.debug(
                f"[{symbol}] Display-only (adverse): price spread {float(price_spread_pct):+.4f}% "
                f"too adverse for all tiers (funding excess {float(_tier_net - abs(price_spread_pct)):.4f}% "
                f"< required {tp.weak_min_funding_excess}%)"
            )

        # ── Qualification tracking (soft gates for display) ──────
        qualified = True
        # ── Entry window: ANY income side with imminent funding ────
        current_entry_window_minutes = tp.narrow_entry_window_minutes

        now_ms = time.time() * 1000

        # P2-3: Normalize next_timestamp to milliseconds.  Some exchanges deliver
        # epoch-seconds (~1.7×10⁹) rather than epoch-ms (~1.7×10¹²).  Without
        # normalization (next_ts - now_ms) is a large negative, making long_mins=None
        # so an income side is silently treated as "not imminent" and entry is skipped.
        def _to_ms(ts: Optional[float]) -> Optional[float]:
            if ts is None:
                return None
            return ts * 1000 if ts < 1e12 else ts

        long_next = _to_ms(funding[long_eid].get("next_timestamp"))
        short_next = _to_ms(funding[short_eid].get("next_timestamp"))

        # Classify each side: income or cost?
        long_is_income = long_rate < 0   # long on negative → we get paid
        short_is_income = short_rate > 0  # short on positive → we get paid

        # Minutes until each side's next funding
        long_mins = (long_next - now_ms) / 60_000 if (long_next and long_next > now_ms) else None
        short_mins = (short_next - now_ms) / 60_000 if (short_next and short_next > now_ms) else None

        # Is each income side within the entry window?
        long_imminent = long_is_income and long_mins is not None and long_mins <= current_entry_window_minutes
        short_imminent = short_is_income and short_mins is not None and short_mins <= current_entry_window_minutes

        # Stale: income side has funding timestamp in the past
        long_stale = long_is_income and long_next is not None and long_next <= now_ms
        short_stale = short_is_income and short_next is not None and short_next <= now_ms

        # Calculate imminent spread: income from imminent payments
        # minus cost from payments that also fire during the hold
        imminent_income_pct = Decimal("0")
        imminent_cost_pct = Decimal("0")
        if long_imminent:
            imminent_income_pct += abs(long_rate) * Decimal("100")
        if short_imminent:
            imminent_income_pct += abs(short_rate) * Decimal("100")
        # Cost sides that also fire during the hold window
        if not long_is_income and long_mins is not None and long_mins <= current_entry_window_minutes:
            imminent_cost_pct += abs(long_rate) * Decimal("100")
        if not short_is_income and short_mins is not None and short_mins <= current_entry_window_minutes:
            imminent_cost_pct += abs(short_rate) * Decimal("100")
        imminent_spread_pct = imminent_income_pct - imminent_cost_pct

        # Earliest income payment within window → entry target
        closest_ms = None
        _income_ts = []
        if long_imminent:
            _income_ts.append(long_next)
        if short_imminent:
            _income_ts.append(short_next)
        if _income_ts:
            closest_ms = min(_income_ts)
        elif long_next and long_next > now_ms:
            closest_ms = long_next   # display only
        elif short_next and short_next > now_ms:
            closest_ms = short_next  # display only

        # ── Gate: imminent income must exist & meet threshold ────
        hold_qualified = True
        # Surface why the gate rejected so the UI can render a specific
        # badge instead of the generic "below 0.3%" label.
        _disq_reason: Optional[str] = None
        if long_stale or short_stale:
            hold_qualified = False
            _disq_reason = "funding_stale"
        elif not (long_imminent or short_imminent):
            hold_qualified = False
            _disq_reason = "funding_no_imminent"
        elif (imminent_spread_pct - total_cost_pct) < tp.min_funding_spread:
            hold_qualified = False
            _disq_reason = "funding_spread_low"

        # ── Determine mode & net (based on imminent payments) ────
        mode: TradeMode = TradeMode.HOLD
        exit_before = None
        n_collections = 0

        gross_pct = imminent_spread_pct
        net_pct = imminent_spread_pct - total_cost_pct

        if hold_qualified:
            # ── HOLD / POT / NUTCRACKER Block ─────────────────
            if pnl["both_income"]:
                mode = TradeMode.POT
                emoji = "🍯"
                label = "POT"
            elif pnl["long_is_income"] or pnl["short_is_income"]:
                if pnl["long_is_income"]:
                    cost_imminent_now = short_mins is not None and short_mins <= current_entry_window_minutes
                    _cost_next_ts_hold = short_next
                else:
                    cost_imminent_now = long_mins is not None and long_mins <= current_entry_window_minutes
                    _cost_next_ts_hold = long_next
                if cost_imminent_now:
                    mode = TradeMode.NUTCRACKER
                    emoji = "🔨🥜"
                    label = "NUTCRACKER"
                else:
                    mode = TradeMode.CHERRY_PICK
                    emoji = "🍒"
                    label = "CHERRY"
                    if _cost_next_ts_hold and _cost_next_ts_hold > now_ms:
                        exit_before = datetime.fromtimestamp(
                            (_cost_next_ts_hold - 120_000) / 1000, tz=timezone.utc
                        )
                    _income_next_ts = long_next if pnl["long_is_income"] else short_next
                    _cost_next_ts = short_next if pnl["long_is_income"] else long_next
                    if _income_next_ts and _cost_next_ts:
                        # Directional gap (not abs): cost-before-income yields
                        # negative value → always classified as NUTCRACKER.
                        _cherry_gap_min = (_cost_next_ts - _income_next_ts) / 60_000
                        if _cherry_gap_min < _MIN_CHERRY_GAP_MINUTES:
                            # Cost fires before or too close to income — treat as NUTCRACKER
                            mode = TradeMode.NUTCRACKER
                            emoji = "🔨🥜"
                            label = "NUTCRACKER"
                            exit_before = None
            else:
                mode = TradeMode.HOLD
                emoji = "🤝"
                label = "HOLD"

            if mode == TradeMode.CHERRY_PICK and entry_tier is None:
                entry_tier = _classify_tier(
                    net_pct, price_spread_pct, total_cost_pct,
                    tp.min_funding_spread, tp.weak_min_funding_excess,
                )
                # Adverse basis gate: if tier is still None, price spread
                # overwhelms the cherry-pick funding edge → reject.
                if entry_tier is None and _live_basis_available and price_spread_pct > 0:
                    qualified = False
                    _disq_reason = "adverse_basis"
                    logger.info(
                        f"\u26a0\ufe0f [{symbol}] CHERRY_PICK rejected: adverse price basis "
                        f"{float(price_spread_pct):+.4f}% overwhelms net edge {float(net_pct):.4f}%"
                    )
            min_to_funding = int((closest_ms - now_ms) / 60_000) if closest_ms else None
            funding_tag = f"{min_to_funding}min" if min_to_funding is not None else "unknown"
            tier_tag = f" [{entry_tier.upper()}]" if entry_tier else ""
            price_tag = f" price_spread={float(price_spread_pct):+.4f}%" if _live_basis_available else ""
            _is_adverse = entry_tier == "adverse"
            _log_prefix = "⚠️ [NO ENTRY — adverse price spread]" if _is_adverse else "🎯 OPPORTUNITY FOUND"
            if self._should_emit_opportunity_log(
                symbol=symbol,
                long_exchange=long_eid,
                short_exchange=short_eid,
                entry_tier=entry_tier,
                net_pct=net_pct,
                price_spread_pct=price_spread_pct,
                is_adverse=_is_adverse,
            ):
                logger.info(
                    f"{_log_prefix} [{symbol}] ({label} {emoji}){tier_tag}: "
                    f"L({long_eid}) @ {long_rate:.8f} | S({short_eid}) @ {short_rate:.8f} | "
                    f"NET={net_pct:.4f}%{price_tag} | NEXT_FUNDING={funding_tag}",
                    extra={
                        "action": "opportunity_found" if not _is_adverse else "opportunity_adverse",
                        "symbol": symbol,
                        "mode": mode,
                        "long_rate": str(long_rate),
                        "short_rate": str(short_rate),
                        "min_to_funding": min_to_funding,
                    },
                )
        else:
            # ── HOLD didn't qualify — try CHERRY_PICK ────────────
            qualified = False  # default off, cherry_pick turns it back on
            # _disq_reason inherited from the HOLD path; cherry-pick success
            # below clears it again. If cherry also fails, keep the existing
            # reason so we surface the most actionable rejection cause.
            _disq_reason = _disq_reason or "cherry_unsuitable"
            if not pnl["both_cost"] and not (long_stale or short_stale):
                cherry_ok = False
                if pnl["long_is_income"]:
                    income_pnl = pnl["long_pnl_per_payment"]
                    income_interval = long_interval
                    income_eid = long_eid
                    cost_eid = short_eid
                elif pnl["short_is_income"]:
                    income_pnl = pnl["short_pnl_per_payment"]
                    income_interval = short_interval
                    income_eid = short_eid
                    cost_eid = long_eid
                else:
                    income_pnl = None

                if income_pnl is not None:
                    # P1-1: Normalize to ms — same treatment as HOLD path above.
                    # Epoch-seconds exchanges (~1.75×10⁹) yield ms_until_cost ≈ −1.74×10¹²
                    # without normalization, silently killing this entire branch.
                    cost_next_ts = _to_ms(funding[cost_eid].get("next_timestamp"))
                    income_next_ts = _to_ms(funding[income_eid].get("next_timestamp"))
                    if cost_next_ts and income_next_ts:
                        cp_now_ms = time.time() * 1000
                        ms_until_cost = cost_next_ts - cp_now_ms
                        ms_until_income = income_next_ts - cp_now_ms
                        minutes_until_cost = ms_until_cost / 60_000
                        minutes_until_income = ms_until_income / 60_000

                        _MIN_INCOME_MINUTES = 2.0
                        if (minutes_until_cost >= _MIN_WINDOW_MINUTES
                                and minutes_until_income >= _MIN_INCOME_MINUTES
                                and (minutes_until_cost - minutes_until_income) >= _MIN_CHERRY_GAP_MINUTES
                                and minutes_until_income < minutes_until_cost
                                and minutes_until_income <= current_entry_window_minutes):
                            cp_gross = calculate_cherry_pick_edge(income_pnl, 1)
                            cp_net = cp_gross - total_cost_pct
                            if cp_net >= tp.min_funding_spread:
                                cherry_ok = True
                                qualified = True
                                _disq_reason = None  # cherry rescued the opp
                                mode = TradeMode.CHERRY_PICK
                                gross_pct = cp_gross
                                net_pct = cp_net
                                n_collections = 1
                                exit_before = datetime.fromtimestamp(
                                    (cost_next_ts - 120_000) / 1000, tz=timezone.utc
                                )
                                closest_ms = income_next_ts
                                logger.info(
                                    f"🍒 Cherry-pick {symbol}: collect 1× {income_interval}h payment "
                                    f"(gross={float(cp_gross):.4f}%, net={float(cp_net):.4f}%) — "
                                    f"enter {int(minutes_until_income)}min before payment, "
                                    f"exit before {exit_before.strftime('%H:%M UTC')}",
                                    extra={
                                        "action": "cherry_pick_found", "symbol": symbol, "mode": "cherry_pick"},
                                )
                                entry_tier = _classify_tier(
                                    cp_net, price_spread_pct, total_cost_pct,
                                    tp.min_funding_spread, tp.weak_min_funding_excess,
                                )
                                # Adverse basis gate: reject if tier is None
                                if entry_tier is None and _live_basis_available and price_spread_pct > 0:
                                    cherry_ok = False
                                    qualified = False
                                    _disq_reason = "adverse_basis"
                                    logger.info(
                                        f"\u26a0\ufe0f [{symbol}] CHERRY_PICK rejected (alt path): "
                                        f"adverse price basis {float(price_spread_pct):+.4f}% "
                                        f"overwhelms net edge {float(cp_net):.4f}%"
                                    )

        # ── Force disqualify if price spread is too adverse for any tier ──
        if _tier_too_adverse or _adverse_price_gate:
            qualified = False
            if _disq_reason is None:
                _disq_reason = "adverse_basis"

        # ── Liquidity filter: reject thin-market opportunities ───
        # Root cause of the NTRN -$14 trade was basis divergence on a low-volume
        # token. We require both legs to clear `min_24h_volume_usd`. If volume
        # data is unavailable for either leg we treat that as failure (fail-closed):
        # without volume context we can't certify the trade as safe.
        min_vol_floor = Decimal(str(getattr(
            self._cfg.trading_params, "min_24h_volume_usd", Decimal("0")
        )))
        _vol_reject = False
        if qualified and min_vol_floor > 0:
            long_vol = await self._get_24h_volume_usd(long_eid, symbol, adapters[long_eid])
            short_vol = await self._get_24h_volume_usd(short_eid, symbol, adapters[short_eid])
            if long_vol is None or short_vol is None:
                qualified = False
                _vol_reject = True
                _disq_reason = "vol_unknown"
                if logger.isEnabledFor(logging.INFO):
                    logger.info(
                        f"🚫 [{symbol}] REJECT: VOL_UNKNOWN "
                        f"({long_eid}={long_vol}, {short_eid}={short_vol}) — fail-closed"
                    )
            else:
                weakest = min(long_vol, short_vol)
                if weakest < min_vol_floor:
                    qualified = False
                    _vol_reject = True
                    _disq_reason = "low_vol"
                    if logger.isEnabledFor(logging.INFO):
                        logger.info(
                            f"🚫 [{symbol}] REJECT: LOW_VOL "
                            f"min(L={long_vol:.0f}, S={short_vol:.0f}) < {min_vol_floor:.0f} USD"
                        )

        # ── Skip truly uninteresting candidates (no positive spread) ──
        if not qualified and immediate_spread <= Decimal("0"):
            return None

        # ── Build opportunity ────────────────────────────────────
        if qualified:
            if cheap:
                # WS-only path: skip balance fetches, REST ticker, and VWAP walks.
                # suggested_qty=0 is safe — execution sizer always recalculates
                # from order_qty at entry time (P1-1 fix).
                _min_iv = min(long_interval, short_interval)
                _imm_net = immediate_spread - fees_pct
                _hrly = _imm_net / Decimal(str(_min_iv)) if _min_iv > 0 else Decimal("0")
                return OpportunityCandidate(
                    symbol=symbol,
                    long_exchange=long_eid,
                    short_exchange=short_eid,
                    long_funding_rate=long_rate,
                    short_funding_rate=short_rate,
                    funding_spread_pct=funding_spread,
                    immediate_spread_pct=immediate_spread,
                    immediate_net_pct=_imm_net,
                    gross_edge_pct=gross_pct,
                    fees_pct=fees_pct,
                    net_edge_pct=net_pct,
                    suggested_qty=Decimal("0"),
                    reference_price=Decimal("0"),
                    min_interval_hours=_min_iv,
                    hourly_rate_pct=_hrly,
                    next_funding_ms=closest_ms,
                    long_next_funding_ms=long_next,
                    short_next_funding_ms=short_next,
                    long_interval_hours=long_interval,
                    short_interval_hours=short_interval,
                    mode=mode,
                    exit_before=exit_before,
                    n_collections=n_collections,
                    entry_tier=entry_tier,
                    price_spread_pct=price_spread_pct,
                    stale_price=False,
                    # qualified defaults to True
                )
            opp = await self._build_opportunity(
                symbol, long_eid, short_eid,
                long_rate, short_rate,
                gross_pct, fees_pct, net_pct,
                adapters, mode=mode,
                long_interval_hours=long_interval,
                short_interval_hours=short_interval,
                next_funding_ms=closest_ms,
                long_next_funding_ms=long_next,
                short_next_funding_ms=short_next,
                exit_before=exit_before,
                n_collections=n_collections,
                entry_tier=entry_tier,
                price_spread_pct=price_spread_pct,
                stale_price=False,
            )
            return opp
        else:
            # Lightweight display-only candidate (no API calls for balance/ticker)
            min_interval = min(long_interval, short_interval)
            immediate_net = immediate_spread - total_cost_pct

            # ── Determine display mode FIRST ────────────────────
            if pnl["both_income"]:
                mode = TradeMode.POT
            elif pnl["long_is_income"] and not pnl["short_is_income"]:
                cost_mins_disp = short_mins
                income_interval_mins = long_interval * 60
                if cost_mins_disp is not None and cost_mins_disp < income_interval_mins:
                    mode = TradeMode.NUTCRACKER
                else:
                    mode = TradeMode.CHERRY_PICK
            elif pnl["short_is_income"] and not pnl["long_is_income"]:
                cost_mins_disp = long_mins
                income_interval_mins = short_interval * 60
                if cost_mins_disp is not None and cost_mins_disp < income_interval_mins:
                    mode = TradeMode.NUTCRACKER
                else:
                    mode = TradeMode.CHERRY_PICK

            # ── Projected net: accurate per-mode calculation ──────
            _display_window = float(tp.max_entry_window_minutes)
            projected_income_pct = Decimal("0")
            if long_is_income and long_mins is not None and long_mins <= _display_window:
                projected_income_pct += abs(long_rate) * Decimal("100")
            if short_is_income and short_mins is not None and short_mins <= _display_window:
                projected_income_pct += abs(short_rate) * Decimal("100")

            projected_cost_pct = Decimal("0")
            if mode == TradeMode.NUTCRACKER:
                if not long_is_income:
                    projected_cost_pct += abs(long_rate) * Decimal("100")
                if not short_is_income:
                    projected_cost_pct += abs(short_rate) * Decimal("100")

            projected_net_pct = projected_income_pct - projected_cost_pct - total_cost_pct
            hourly_rate = projected_net_pct / Decimal(str(min_interval)) if min_interval > 0 else Decimal("0")
            return OpportunityCandidate(
                symbol=symbol,
                long_exchange=long_eid,
                short_exchange=short_eid,
                long_funding_rate=long_rate,
                short_funding_rate=short_rate,
                funding_spread_pct=funding_spread,
                immediate_spread_pct=immediate_spread,
                immediate_net_pct=immediate_net,
                gross_edge_pct=gross_pct,
                fees_pct=fees_pct,
                net_edge_pct=projected_net_pct,
                suggested_qty=Decimal("0"),
                reference_price=Decimal("0"),
                min_interval_hours=min_interval,
                hourly_rate_pct=hourly_rate,
                next_funding_ms=closest_ms,
                long_next_funding_ms=long_next,
                short_next_funding_ms=short_next,
                long_interval_hours=long_interval,
                short_interval_hours=short_interval,
                qualified=False,
                disqualify_reason=_disq_reason,
                mode=mode,
                exit_before=exit_before,
                n_collections=n_collections,
                entry_tier=entry_tier,
                price_spread_pct=price_spread_pct,
                stale_price=False,
            )

    async def _build_opportunity(
        self,
        symbol: str,
        long_eid: str, short_eid: str,
        long_rate: Decimal, short_rate: Decimal,
        gross_pct: Decimal, fees_pct: Decimal, net_pct: Decimal,
        adapters: Dict[str, "ExchangeAdapter"],
        mode: "TradeMode" = TradeMode.HOLD,
        exit_before: Optional[datetime] = None,
        n_collections: int = 0,
        long_interval_hours: int = 8,
        short_interval_hours: int = 8,
        next_funding_ms: Optional[float] = None,
        long_next_funding_ms: Optional[float] = None,
        short_next_funding_ms: Optional[float] = None,
        entry_tier: Optional[str] = None,
        price_spread_pct: Decimal = Decimal("0"),
        stale_price: bool = False,
    ) -> Optional[OpportunityCandidate]:
        """Build opportunity with position sizing (70% of min balance × leverage)."""
        # Parallelize balance fetches (both exchanges) with ticker fetch (long side only)
        # so all 3 REST calls happen concurrently instead of sequentially.
        long_bal, short_bal, long_ticker = await asyncio.gather(
            adapters[long_eid].get_balance(),
            adapters[short_eid].get_balance(),
            adapters[long_eid].get_ticker(symbol),
        )
        free_usd = min(long_bal["free"], short_bal["free"])

        position_pct = self._cfg.risk_limits.position_size_pct
        long_exc_cfg = self._cfg.exchanges.get(long_eid)
        leverage = Decimal(str(long_exc_cfg.leverage if long_exc_cfg and long_exc_cfg.leverage else 5))
        # P2-2: Mirror the sizer's max_margin_usage cap so that suggested_qty
        # never exceeds what sizer.compute() will actually approve.  Without
        # this, _check_pre_entry_liquidity tests inflated depth and may reject
        # entries the sizer would scale down to fit.
        total_long_usd = long_bal.get("total", long_bal["free"])
        total_short_usd = short_bal.get("total", short_bal["free"])
        max_margin_usage = getattr(self._cfg.risk_limits, "max_margin_usage", Decimal("0.70"))
        used_long = total_long_usd - long_bal["free"]
        used_short = total_short_usd - short_bal["free"]
        avail_long = max(Decimal("0"), total_long_usd * max_margin_usage - used_long)
        avail_short = max(Decimal("0"), total_short_usd * max_margin_usage - used_short)
        margin_capped = min(avail_long, avail_short, free_usd)
        margin = margin_capped * position_pct
        notional = margin * leverage
        max_pos = self._cfg.risk_limits.max_position_size_usd
        notional = min(max_pos, notional)

        price = Decimal(str(long_ticker.get("last", 0)))
        if price <= 0:
            logger.warning(
                f"[{symbol}] _build_opportunity skipped: {long_eid} ticker has no "
                f"valid last price (got {long_ticker.get('last', 'MISSING')})",
                extra={"symbol": symbol, "action": "build_opportunity_no_price"},
            )
            return None
        quantity = notional / price

        spread_info = calculate_funding_spread(
            long_rate, short_rate,
            long_interval_hours=long_interval_hours,
            short_interval_hours=short_interval_hours,
        )

        # Use executable prices from live order book to report realistic price spread.
        # Long leg enters with BUY (consume asks), short leg enters with SELL (consume bids).
        long_exec_buy_raw, short_exec_sell_raw = await asyncio.gather(
            adapters[long_eid].get_executable_price(symbol, quantity, side=OrderSide.BUY.value),
            adapters[short_eid].get_executable_price(symbol, quantity, side=OrderSide.SELL.value),
        )
        try:
            long_exec_buy = Decimal(str(long_exec_buy_raw))
            short_exec_sell = Decimal(str(short_exec_sell_raw))
        except Exception:
            long_exec_buy = Decimal("0")
            short_exec_sell = Decimal("0")
        if long_exec_buy > 0 and short_exec_sell > 0:
            price_spread_pct = (long_exec_buy - short_exec_sell) / short_exec_sell * Decimal("100")

        min_interval = min(long_interval_hours, short_interval_hours)
        immediate_net = spread_info["immediate_spread_pct"] - fees_pct
        hourly_rate = immediate_net / Decimal(str(min_interval)) if min_interval > 0 else Decimal("0")

        return OpportunityCandidate(
            symbol=symbol,
            long_exchange=long_eid,
            short_exchange=short_eid,
            long_funding_rate=long_rate,
            short_funding_rate=short_rate,
            funding_spread_pct=spread_info["funding_spread_pct"],
            immediate_spread_pct=spread_info["immediate_spread_pct"],
            immediate_net_pct=immediate_net,
            gross_edge_pct=gross_pct,
            fees_pct=fees_pct,
            net_edge_pct=net_pct,
            suggested_qty=quantity,
            reference_price=price,
            min_interval_hours=min_interval,
            hourly_rate_pct=hourly_rate,
            next_funding_ms=next_funding_ms,
            long_next_funding_ms=long_next_funding_ms,
            short_next_funding_ms=short_next_funding_ms,
            long_interval_hours=long_interval_hours,
            short_interval_hours=short_interval_hours,
            mode=mode,
            exit_before=exit_before,
            n_collections=n_collections,
            entry_tier=entry_tier,
            price_spread_pct=price_spread_pct,
            stale_price=stale_price,
        )
