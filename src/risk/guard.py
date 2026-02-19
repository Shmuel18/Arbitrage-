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
from typing import TYPE_CHECKING, Dict, Optional

from src.core.contracts import OrderRequest, OrderSide
from src.core.logging import get_logger

if TYPE_CHECKING:
    from src.core.config import Config
    from src.exchanges.adapter import ExchangeManager
    from src.storage.redis_client import RedisClient

logger = get_logger("risk")


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

    # ── Lifecycle ────────────────────────────────────────────────

    async def start(self) -> None:
        self._running = True
        self._tasks = [
            asyncio.create_task(self._fast_loop(), name="risk-fast"),
            asyncio.create_task(self._deep_loop(), name="risk-deep"),
        ]
        logger.info("Risk guard started")

    async def stop(self) -> None:
        self._running = False
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        logger.info("Risk guard stopped")

    def mark_trade_opened(self, symbol: str) -> None:
        """Mark symbol as having a recent trade - skip delta checks for 30s."""
        self._grace_timestamps[symbol] = time.time()
        logger.debug(f"Grace period started for {symbol} (30s)")

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
        now = time.time()
        positions_by_symbol: Dict[str, list] = {}  # For detailed logging
        failed_exchanges: list[str] = []

        for eid, adapter in self._exchanges.all().items():
            try:
                positions = await adapter.get_positions()
            except Exception as e:
                logger.warning(f"Cannot fetch positions from {eid}: {e}",
                               extra={"exchange": eid})
                failed_exchanges.append(eid)
                continue

            for pos in positions:
                signed = pos.quantity if pos.side == OrderSide.BUY else -pos.quantity
                delta_by_symbol[pos.symbol] = delta_by_symbol.get(pos.symbol, Decimal(0)) + signed
                
                if pos.symbol not in positions_by_symbol:
                    positions_by_symbol[pos.symbol] = []
                positions_by_symbol[pos.symbol].append((eid, pos.side.value, float(pos.quantity), float(signed)))

        # ── SAFETY: abort if any exchange failed ─────────────────
        if failed_exchanges:
            logger.warning(
                f"Delta check SKIPPED — {len(failed_exchanges)} exchange(s) "
                f"failed to return positions: {', '.join(failed_exchanges)}. "
                f"Cannot safely evaluate delta with incomplete data.",
                extra={"action": "delta_skip"},
            )
            return

        threshold = self._cfg.risk_limits.delta_threshold_pct / Decimal(100)

        for symbol, net in delta_by_symbol.items():
            # Skip symbols in grace period (30 seconds after trade opened)
            if symbol in self._grace_timestamps:
                if now - self._grace_timestamps[symbol] < 30:
                    continue
                else:
                    del self._grace_timestamps[symbol]
            
            if abs(net) > threshold:
                # Log detailed position breakdown
                pos_details = positions_by_symbol.get(symbol, [])
                pos_breakdown = "; ".join(
                    f"{eid}({side}): {qty:.1f}" 
                    for eid, side, qty, _ in pos_details
                )
                logger.warning(
                    f"Delta breach: {symbol} net={net} [threshold={threshold}] — Positions: {pos_breakdown}",
                    extra={"symbol": symbol, "action": "delta_breach", "data": {"net": str(net)}},
                )
                if self._cfg.risk_guard.enable_panic_close:
                    # Only close on exchanges that actually hold positions
                    exchanges_with_positions = {eid for eid, _, _, _ in pos_details}
                    await self._panic_close(symbol, exchanges_with_positions)

    # ── Panic close ──────────────────────────────────────────────

    async def _panic_close(self, symbol: str, target_exchanges: set[str] | None = None) -> None:
        """Close all positions for a symbol.
        
        If target_exchanges is provided, only close on those exchanges
        (avoids noisy errors on exchanges that don't list the symbol).
        Otherwise falls back to trying all exchanges.
        """
        logger.warning(f"PANIC CLOSE triggered for {symbol}",
                       extra={"symbol": symbol, "action": "panic_close"})

        exchanges_to_check = (
            {eid: adapter for eid, adapter in self._exchanges.all().items()
             if eid in target_exchanges}
            if target_exchanges
            else self._exchanges.all()
        )

        for eid, adapter in exchanges_to_check.items():
            try:
                positions = await adapter.get_positions(symbol)
                if not positions:
                    continue
                for pos in positions:
                    close_side = OrderSide.SELL if pos.side == OrderSide.BUY else OrderSide.BUY
                    req = OrderRequest(
                        exchange=eid,
                        symbol=symbol,
                        side=close_side,
                        quantity=pos.quantity,
                        reduce_only=True,
                    )
                    await adapter.place_order(req)
                    logger.info(
                        f"Panic-closed {pos.quantity} {symbol} on {eid}",
                        extra={"exchange": eid, "symbol": symbol, "action": "panic_closed"},
                    )
            except Exception as e:
                logger.error(f"Panic close failed on {eid}/{symbol}: {e}",
                             extra={"exchange": eid, "symbol": symbol})

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
        for eid, adapter in self._exchanges.all().items():
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
                logger.warning(f"Snapshot failed for {eid}: {e}",
                               extra={"exchange": eid})
