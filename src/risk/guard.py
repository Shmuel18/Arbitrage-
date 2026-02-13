"""
Risk Guard
Background loops to keep delta-neutral and reconcile positions
"""

import asyncio
from decimal import Decimal
from typing import Dict, Optional

from src.core.config import get_config
from src.core.contracts import OrderRequest, OrderSide
from src.core.logging import get_logger
from src.exchanges.adapter import ExchangeManager
from src.storage.redis_client import RedisClient

logger = get_logger("risk_guard")


class RiskGuard:
    """Runs background risk control loops"""

    def __init__(self, exchange_manager: ExchangeManager, redis_client: Optional[RedisClient] = None):
        self.config = get_config()
        self.exchange_manager = exchange_manager
        self.redis_client = redis_client
        self._stop_event = asyncio.Event()
        self._fast_task: Optional[asyncio.Task] = None
        self._deep_task: Optional[asyncio.Task] = None

    async def start(self):
        """Start background loops"""
        if self._fast_task or self._deep_task:
            return

        self._fast_task = asyncio.create_task(self._fast_loop())
        self._deep_task = asyncio.create_task(self._deep_loop())
        logger.info("Risk guard loops started")

    async def stop(self):
        """Stop background loops"""
        self._stop_event.set()
        tasks = [t for t in [self._fast_task, self._deep_task] if t]

        for task in tasks:
            task.cancel()

        await asyncio.gather(*tasks, return_exceptions=True)
        self._fast_task = None
        self._deep_task = None
        logger.info("Risk guard loops stopped")

    async def _fast_loop(self):
        """Fast loop for delta checks"""
        interval = self.config.risk_guard.fast_loop_interval_sec

        while not self._stop_event.is_set():
            try:
                await self._check_delta_neutrality()
            except Exception as e:
                logger.warning("Fast loop error", error=str(e))
            await asyncio.sleep(interval)

    async def _deep_loop(self):
        """Deep loop for reconciliation snapshots"""
        interval = self.config.risk_guard.deep_loop_interval_sec

        while not self._stop_event.is_set():
            try:
                await self._snapshot_positions()
            except Exception as e:
                logger.warning("Deep loop error", error=str(e))
            await asyncio.sleep(interval)

    async def _check_delta_neutrality(self):
        """Check portfolio delta neutrality across exchanges"""
        symbol_stats: Dict[str, Dict[str, Decimal]] = {}
        positions_by_symbol: Dict[str, list] = {}

        for exchange_id, adapter in self.exchange_manager.adapters.items():
            try:
                positions = await adapter.get_positions()
            except Exception as e:
                logger.debug("Failed to fetch positions", exchange=exchange_id, error=str(e))
                continue
            for pos in positions:
                positions_by_symbol.setdefault(pos.symbol, []).append((exchange_id, pos))
                if pos.symbol not in symbol_stats:
                    symbol_stats[pos.symbol] = {
                        "net_notional": Decimal("0"),
                        "gross_notional": Decimal("0"),
                    }

                # Use entry_price (Position has no mark_price field)
                price = pos.entry_price if pos.entry_price > 0 else Decimal("1")
                # Signed quantity: positive for long, negative for short
                signed_qty = pos.quantity if pos.side == OrderSide.BUY else -pos.quantity
                notional = signed_qty * price
                symbol_stats[pos.symbol]["net_notional"] += notional
                symbol_stats[pos.symbol]["gross_notional"] += abs(notional)

        threshold_pct = self.config.risk_limits.delta_threshold_pct

        for symbol, stats in symbol_stats.items():
            gross = stats["gross_notional"]
            net = stats["net_notional"]
            if gross <= 0:
                continue

            delta_pct = abs(net / gross) * Decimal("100")
            if delta_pct > threshold_pct:
                logger.warning(
                    "Delta breach detected",
                    symbol=symbol,
                    delta_pct=float(delta_pct),
                    threshold_pct=float(threshold_pct),
                )

                await self._handle_delta_breach(symbol, positions_by_symbol.get(symbol, []))

    async def _handle_delta_breach(self, symbol: str, positions):
        """Handle delta breach with optional rebalance/close actions"""
        if not positions:
            return

        if self.redis_client:
            acquired = await self.redis_client.acquire_lock(f"panic:{symbol}")
            if not acquired:
                return
        else:
            acquired = False

        try:
            if self.config.risk_guard.enable_auto_rebalance and self.redis_client:
                secs = self.config.trading_params.cooldown_after_orphan_hours * 3600
                await self.redis_client.set_cooldown(symbol, secs)

            if not self.config.risk_guard.enable_panic_close:
                return

            for exchange_id, pos in positions:
                adapter = self.exchange_manager.get_adapter(exchange_id)
                if not adapter:
                    continue

                close_order = OrderRequest(
                    exchange=exchange_id,
                    symbol=pos.symbol,
                    side=OrderSide.BUY if pos.quantity < 0 else OrderSide.SELL,
                    quantity=abs(pos.quantity),
                    reduce_only=True,
                )

                try:
                    await adapter.place_order(close_order)
                    logger.warning("Panic close executed", symbol=symbol, exchange=exchange_id)
                except Exception as e:
                    logger.error("Panic close failed", symbol=symbol, exchange=exchange_id, error=str(e))
        finally:
            if acquired and self.redis_client:
                await self.redis_client.release_lock(f"panic:{symbol}")

    def _cooldown_until(self):
        """Cooldown expiry time for delta breach"""
        from datetime import datetime, timedelta
        return datetime.utcnow() + timedelta(hours=self.config.trading_params.cooldown_after_orphan_hours)

    async def _snapshot_positions(self):
        """Store position snapshots in Redis"""
        if not self.redis_client:
            return

        for exchange_id, adapter in self.exchange_manager.adapters.items():
            try:
                positions = await adapter.get_positions()
            except Exception as e:
                logger.debug("Failed to fetch positions for snapshot", exchange=exchange_id, error=str(e))
                continue
            snapshot = [
                {
                    "symbol": pos.symbol,
                    "side": pos.side.value,
                    "quantity": str(pos.quantity),
                    "entry_price": str(pos.entry_price),
                }
                for pos in positions
            ]
            await self.redis_client.set_position_snapshot(exchange_id, snapshot)
