"""
Risk guard — continuous delta-neutrality enforcement.

Runs two loops:
  • fast (every 5 s)  — check positions, compute delta
  • deep (every 60 s) — recalculate full P&L, persist snapshots
"""

from __future__ import annotations

import asyncio
import time
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Dict, Optional

from src.core.contracts import OrderRequest, OrderSide
from src.core.logging import get_logger

if TYPE_CHECKING:
    from src.core.config import Config
    from src.exchanges.adapter import ExchangeManager
    from src.storage.redis_client import RedisClient

logger = get_logger("risk")


def _task_done_handler(t: asyncio.Task) -> None:
    """Log exceptions from background tasks — never let them vanish silently."""
    if t.cancelled():
        return
    exc = t.exception()
    if exc:
        logger.error(
            f"Task {t.get_name()} failed: {exc}",
            exc_info=exc,
            extra={"action": "task_failed", "task_name": t.get_name()},
        )


class RiskGuard:
    def __init__(
        self,
        config: "Config",
        exchange_mgr: "ExchangeManager",
        redis: "RedisClient",
    ):
        self._cfg = config
        self._exchanges = exchange_mgr
        self._redis = redis
        self._running = False
        self._tasks: list[asyncio.Task] = []
        self._grace_timestamps: Dict[str, float] = {}  # symbol -> timestamp
        self._warn_last_logged: Dict[str, float] = {}  # warning key -> last log time (monotonic-s)

    def _should_log_warning(self, key: str, interval_seconds: float) -> bool:
        """Rate-limit repetitive warning logs during transient exchange outages."""
        now = time.monotonic()
        last = self._warn_last_logged.get(key, 0.0)
        if now - last < interval_seconds:
            return False
        self._warn_last_logged[key] = now
        return True

    # ── Lifecycle ────────────────────────────────────────────────

    async def start(self) -> None:
        self._running = True
        self._tasks = [
            asyncio.create_task(self._fast_loop(), name="risk-fast"),
            asyncio.create_task(self._deep_loop(), name="risk-deep"),
        ]
        for task in self._tasks:
            task.add_done_callback(_task_done_handler)
        logger.info("Risk guard started")

    async def stop(self) -> None:
        self._running = False
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        logger.info("Risk guard stopped")

    def mark_trade_opened(self, symbol: str) -> None:
        """Mark symbol as having a recent trade — skip delta checks for grace period."""
        self._grace_timestamps[symbol] = time.time()
        grace = self._cfg.risk_guard.delta_grace_seconds
        logger.debug(f"Grace period started for {symbol} ({grace}s)")

    # ── Fast loop (delta check) ──────────────────────────────────

    async def _fast_loop(self) -> None:
        interval = self._cfg.risk_guard.fast_loop_interval_sec
        while self._running:
            try:
                await self._check_delta()
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.error(f"Risk fast loop error: {e}")
            await asyncio.sleep(interval)

    async def _check_delta(self) -> None:
        """Sum net exposure across all exchanges per symbol.
        
        CRITICAL: If any exchange fails to return positions, we abort the
        entire delta check for this cycle.  Running a delta check with
        incomplete data causes false breaches (e.g. seeing only the LONG
        leg because the SHORT leg's exchange API timed out).
        """
        delta_by_symbol: Dict[str, Decimal] = {}
        total_abs_by_symbol: Dict[str, Decimal] = {}  # total absolute qty per symbol
        now = time.time()
        positions_by_symbol: Dict[str, list] = {}  # For detailed logging
        failed_exchanges: list[str] = []

        # ── Fetch all exchanges in parallel to reduce latency ────
        adapters = list(self._exchanges.all().items())
        position_results = await asyncio.gather(
            *[adapter.get_positions() for _, adapter in adapters],
            return_exceptions=True,
        )

        for (eid, _), result in zip(adapters, position_results):
            if isinstance(result, Exception):
                if self._should_log_warning(f"positions_fetch:{eid}", 30.0):
                    logger.warning(f"Cannot fetch positions from {eid}: {result}",
                                   extra={"exchange": eid})
                failed_exchanges.append(eid)
                continue

            for pos in result:
                signed = pos.quantity if pos.side == OrderSide.BUY else -pos.quantity
                delta_by_symbol[pos.symbol] = delta_by_symbol.get(pos.symbol, Decimal(0)) + signed
                total_abs_by_symbol[pos.symbol] = total_abs_by_symbol.get(pos.symbol, Decimal(0)) + abs(pos.quantity)
                
                if pos.symbol not in positions_by_symbol:
                    positions_by_symbol[pos.symbol] = []
                positions_by_symbol[pos.symbol].append((eid, pos.side.value, float(pos.quantity), float(signed)))

        # ── SAFETY: abort if any exchange failed ─────────────────
        if failed_exchanges:
            failed_key = ",".join(sorted(failed_exchanges))
            if self._should_log_warning(f"delta_skip:{failed_key}", 30.0):
                logger.warning(
                    f"Delta check SKIPPED — {len(failed_exchanges)} exchange(s) "
                    f"failed to return positions: {', '.join(failed_exchanges)}. "
                    f"Cannot safely evaluate delta with incomplete data.",
                    extra={"action": "delta_skip"},
                )
            return

        # delta_threshold_pct is now compared as a PERCENTAGE of average
        # leg size, not as absolute coin quantity.  With the old code,
        # threshold = 5.0/100 = 0.05 coins, which triggered false panics
        # for any altcoin with quantity > 50 (virtually all of them).
        threshold_pct = self._cfg.risk_limits.delta_threshold_pct  # e.g. 5.0 = 5%

        for symbol, net in delta_by_symbol.items():
            # Skip symbols in grace period (cfg.risk_guard.delta_grace_seconds)
            if symbol in self._grace_timestamps:
                if now - self._grace_timestamps[symbol] < self._cfg.risk_guard.delta_grace_seconds:
                    continue
                else:
                    del self._grace_timestamps[symbol]

            total_abs = total_abs_by_symbol.get(symbol, Decimal(0))
            if total_abs <= 0:
                continue

            # Average leg size = total_abs / 2 (long + short)
            avg_leg = total_abs / 2
            delta_pct = abs(net) / avg_leg * Decimal("100") if avg_leg > 0 else Decimal("0")

            if delta_pct > threshold_pct:
                # Log detailed position breakdown
                pos_details = positions_by_symbol.get(symbol, [])
                pos_breakdown = "; ".join(
                    f"{eid}({side}): {qty:.1f}" 
                    for eid, side, qty, _ in pos_details
                )
                logger.warning(
                    f"Delta breach: {symbol} net={net} ({float(delta_pct):.1f}% imbalance) "
                    f"[threshold={float(threshold_pct)}%] — Positions: {pos_breakdown}",
                    extra={"symbol": symbol, "action": "delta_breach",
                           "data": {"net": str(net), "delta_pct": str(delta_pct)}},
                )
                if self._cfg.risk_guard.enable_panic_close:
                    # Only close on exchanges that actually hold positions
                    exchanges_with_positions = {eid for eid, _, _, _ in pos_details}
                    await self._panic_close(symbol, exchanges_with_positions)

    # ── Panic close ──────────────────────────────────────────────

    async def _panic_close(self, symbol: str, target_exchanges: set[str] | None = None) -> None:
        """Close all positions for a symbol.

        P1-1: Dispatches close orders to all target exchanges IN PARALLEL with
        bounded timeout per order — was sequential and unbounded, which meant a
        single stuck TCP connection could burn seconds while the exchange's own
        liquidation engine was already running.

        If target_exchanges is provided, only close on those exchanges
        (avoids noisy errors on exchanges that don't list the symbol).
        Otherwise falls back to trying all exchanges.
        """
        _PANIC_ORDER_TIMEOUT: float = 6.0  # hard wall per order — liquidation races are time-critical

        logger.warning(f"PANIC CLOSE triggered for {symbol}",
                       extra={"symbol": symbol, "action": "panic_close"})

        exchanges_to_check = (
            {eid: adapter for eid, adapter in self._exchanges.all().items()
             if eid in target_exchanges}
            if target_exchanges
            else self._exchanges.all()
        )

        # ── Fetch positions in parallel ──────────────────────────
        eid_list = list(exchanges_to_check.keys())
        pos_results = await asyncio.gather(
            *[exchanges_to_check[eid].get_positions(symbol) for eid in eid_list],
            return_exceptions=True,
        )

        # Build close tasks for all open positions
        close_tasks: list = []
        close_meta: list[tuple[str, str, float]] = []  # (eid, side, qty)
        for eid, result in zip(eid_list, pos_results):
            if isinstance(result, Exception):
                logger.warning(
                    f"Panic close: position fetch failed on {eid}/{symbol}: {result}",
                    extra={"exchange": eid, "symbol": symbol},
                )
                continue
            adapter = exchanges_to_check[eid]
            for pos in result:
                close_side = OrderSide.SELL if pos.side == OrderSide.BUY else OrderSide.BUY
                req = OrderRequest(
                    exchange=eid,
                    symbol=symbol,
                    side=close_side,
                    quantity=pos.quantity,
                    reduce_only=True,
                )
                close_tasks.append(
                    asyncio.wait_for(adapter.place_order(req), timeout=_PANIC_ORDER_TIMEOUT)
                )
                close_meta.append((eid, close_side.value, float(pos.quantity)))

        if not close_tasks:
            logger.info(f"Panic close: no open positions found for {symbol}",
                        extra={"symbol": symbol})
        else:
            # ── Dispatch ALL orders simultaneously ───────────────
            close_results = await asyncio.gather(*close_tasks, return_exceptions=True)
            for (eid, side, qty), res in zip(close_meta, close_results):
                if isinstance(res, Exception):
                    logger.error(
                        f"Panic close failed on {eid}/{symbol} ({side} {qty}): {res}",
                        extra={"exchange": eid, "symbol": symbol},
                    )
                else:
                    logger.info(
                        f"Panic-closed {qty} {symbol} ({side}) on {eid}",
                        extra={"exchange": eid, "symbol": symbol, "action": "panic_closed"},
                    )

        # ── Verify positions are actually gone ───────────────────
        await asyncio.sleep(2)  # brief settle time for exchange state
        still_open = []
        for eid, adapter in exchanges_to_check.items():
            try:
                remaining = await adapter.get_positions(symbol)
                for pos in remaining:
                    if abs(pos.quantity) > 0:
                        still_open.append((eid, pos.side.value, float(pos.quantity)))
            except Exception as e:
                logger.warning(
                    f"Post-panic position check failed on {eid}/{symbol}: {e}",
                    extra={"exchange": eid, "symbol": symbol},
                )

        if still_open:
            breakdown = "; ".join(f"{eid}({side}): {qty}" for eid, side, qty in still_open)
            logger.error(
                f"⚠️ PANIC CLOSE INCOMPLETE — positions still open for {symbol}: {breakdown}. "
                f"Manual intervention required!",
                extra={"symbol": symbol, "action": "panic_close_incomplete",
                        "data": {"remaining": still_open}},
            )
        else:
            logger.info(
                f"✅ Panic close verified — all {symbol} positions confirmed closed.",
                extra={"symbol": symbol, "action": "panic_close_verified"},
            )

        # Cooldown after panic
        cooldown_sec = self._cfg.trading_params.cooldown_after_orphan_hours * 3600
        await self._redis.set_cooldown(symbol, cooldown_sec)

    # ── Deep loop (snapshots) ────────────────────────────────────

    async def _deep_loop(self) -> None:
        interval = self._cfg.risk_guard.deep_loop_interval_sec
        while self._running:
            try:
                await self._snapshot_positions()
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.error(f"Risk deep loop error: {e}")
            await asyncio.sleep(interval)

    async def _snapshot_positions(self) -> None:
        async def _snapshot_one(eid: str, adapter: Any) -> None:
            try:
                positions = await adapter.get_positions()
                data = [
                    {
                        "symbol": p.symbol,
                        "side": p.side.value,
                        "qty": str(p.quantity),
                        "entry": str(p.entry_price),
                        "upnl": str(p.unrealized_pnl),
                    }
                    for p in positions
                ]
                await self._redis.set_position_snapshot(eid, data)
            except Exception as e:
                if self._should_log_warning(f"snapshot:{eid}", 60.0):
                    logger.warning(f"Snapshot failed for {eid}: {e}",
                                   extra={"exchange": eid})

        await asyncio.gather(
            *[_snapshot_one(eid, adapter) for eid, adapter in self._exchanges.all().items()],
        )
