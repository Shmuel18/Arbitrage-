"""
Risk Guard
Background loops to keep delta-neutral and reconcile positions
"""

import asyncio
from decimal import Decimal
from typing import Dict, Optional

from src.core.config import get_config
from src.core.logging import get_logger
from src.exchanges.base import ExchangeManager
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

        for exchange_id, adapter in self.exchange_manager.adapters.items():
            positions = await adapter.get_positions()
            for pos in positions:
                if pos.symbol not in symbol_stats:
                    symbol_stats[pos.symbol] = {
                        "net_notional": Decimal("0"),
                        "gross_notional": Decimal("0"),
                    }

                notional = pos.quantity * pos.mark_price
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

    async def _snapshot_positions(self):
        """Store position snapshots in Redis"""
        if not self.redis_client:
            return

        for exchange_id, adapter in self.exchange_manager.adapters.items():
            positions = await adapter.get_positions()
            for pos in positions:
                await self.redis_client.set_position_snapshot(
                    exchange_id,
                    pos.symbol,
                    {
                        "quantity": str(pos.quantity),
                        "mark_price": str(pos.mark_price),
                        "timestamp": pos.timestamp.isoformat(),
                    },
                )
