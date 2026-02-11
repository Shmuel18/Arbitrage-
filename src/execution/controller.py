"""
Execution Controller
Responsible for placing paired orders across exchanges
and managing the full trade lifecycle (open → funding → close)
"""

import asyncio
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Dict, List, Optional

from src.core.config import get_config
from src.core.contracts import OrderRequest, OrderSide, OpportunityCandidate, TradeRecord, TradeState
from src.core.logging import get_logger
from src.exchanges.base import ExchangeManager
from src.storage.redis_client import RedisClient

logger = get_logger("execution_controller")


class ActiveTrade:
    """Tracks an active hedged position awaiting funding payment"""

    def __init__(self, opportunity: OpportunityCandidate, trade: TradeRecord):
        self.opportunity = opportunity
        self.trade = trade
        self.opened_at = datetime.utcnow()
        self.funding_collected = False
        self.close_after: Optional[datetime] = None
        self.close_attempts = 0
        self.max_close_attempts = 5


class ExecutionController:
    """Executes opportunities across exchanges and manages trade lifecycle"""

    def __init__(self, exchange_manager: ExchangeManager, redis_client: Optional[RedisClient] = None):
        self.config = get_config()
        self.exchange_manager = exchange_manager
        self.redis_client = redis_client

        # Track active trades for exit management
        self.active_trades: List[ActiveTrade] = []
        self._exit_task: Optional[asyncio.Task] = None

    async def start_exit_monitor(self):
        """Start background loop that monitors and closes trades after funding"""
        if self._exit_task is None:
            self._exit_task = asyncio.create_task(self._exit_monitor_loop())
            logger.info("Exit monitor started")

    async def stop_exit_monitor(self):
        """Stop exit monitor"""
        if self._exit_task:
            self._exit_task.cancel()
            try:
                await self._exit_task
            except asyncio.CancelledError:
                pass
            self._exit_task = None

    async def _exit_monitor_loop(self):
        """Check every 10 seconds if any trade should be closed"""
        while True:
            try:
                await self._check_exits()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Exit monitor error", error=str(e))
            await asyncio.sleep(10)

    async def _check_exits(self):
        """Check all active trades and close those that have collected funding"""
        now = datetime.utcnow()
        trades_to_remove = []

        for active in self.active_trades:
            opp = active.opportunity

            if active.close_after and now >= active.close_after:
                # Check retry limit
                if active.close_attempts >= active.max_close_attempts:
                    logger.error(
                        "CRITICAL: Max close attempts reached! Manual intervention needed.",
                        symbol=opp.symbol,
                        attempts=active.close_attempts,
                    )
                    trades_to_remove.append(active)
                    continue

                active.close_attempts += 1

                # Time to close - funding has been paid
                logger.info(
                    "Closing trade after funding",
                    symbol=opp.symbol,
                    long_exchange=opp.exchange_long,
                    short_exchange=opp.exchange_short,
                    held_minutes=int((now - active.opened_at).total_seconds() / 60),
                )

                success = await self._close_trade(active)
                if success:
                    trades_to_remove.append(active)
                continue

            if active.close_after is None:
                # Determine when to close based on next funding time
                close_time = await self._get_close_time(active)
                if close_time:
                    active.close_after = close_time
                    mins = int((close_time - now).total_seconds() / 60)
                    logger.info(
                        "Scheduled trade exit",
                        symbol=opp.symbol,
                        close_in_minutes=mins,
                    )
                else:
                    # Fallback: close after max 60 minutes if can't determine funding time
                    age_minutes = (now - active.opened_at).total_seconds() / 60
                    if age_minutes > 60:
                        logger.warning("Trade too old, force closing", symbol=opp.symbol)
                        success = await self._close_trade(active)
                        if success:
                            trades_to_remove.append(active)

        for trade in trades_to_remove:
            self.active_trades.remove(trade)

    async def _get_close_time(self, active: ActiveTrade) -> Optional[datetime]:
        """Determine when to close based on next funding timestamp"""
        opp = active.opportunity
        try:
            # Check funding time from the long exchange
            adapter = self.exchange_manager.get_adapter(opp.exchange_long)
            if not adapter:
                return None

            funding_data = await adapter.get_funding_rate(opp.symbol)
            if not funding_data:
                return None

            # Get next funding timestamp
            next_funding_ts = funding_data.get('fundingTimestamp') or funding_data.get('nextFundingTimestamp')
            if next_funding_ts is None:
                info = funding_data.get('info', {})
                next_funding_ts = info.get('nextFundingTime') or info.get('fundingTimestamp')

            if next_funding_ts is None:
                return None

            # Convert to datetime
            if isinstance(next_funding_ts, (int, float)):
                if next_funding_ts > 1e12:
                    next_funding_ts = next_funding_ts / 1000  # ms to sec
                next_funding_dt = datetime.utcfromtimestamp(next_funding_ts)
            elif isinstance(next_funding_ts, str):
                next_funding_dt = datetime.fromisoformat(next_funding_ts.replace('Z', '+00:00').replace('+00:00', ''))
            else:
                return None

            # Close 30 seconds AFTER the funding payment
            return next_funding_dt + timedelta(seconds=30)

        except Exception as e:
            logger.debug("Could not determine close time", error=str(e))
            return None

    async def _close_trade(self, active: ActiveTrade) -> bool:
        """Close both legs of a trade"""
        opp = active.opportunity

        long_adapter = self.exchange_manager.get_adapter(opp.exchange_long)
        short_adapter = self.exchange_manager.get_adapter(opp.exchange_short)

        if not long_adapter or not short_adapter:
            logger.error("Missing adapter for close", symbol=opp.symbol)
            return False

        # Close long = sell, Close short = buy (with reduceOnly for safety)
        close_long = OrderRequest(
            exchange=opp.exchange_long,
            symbol=opp.symbol,
            side=OrderSide.SHORT,  # sell to close long
            quantity=opp.quantity,
            price=None,  # market order
        )
        close_short = OrderRequest(
            exchange=opp.exchange_short,
            symbol=opp.symbol,
            side=OrderSide.LONG,  # buy to close short
            quantity=opp.quantity,
            price=None,  # market order
        )

        # Execute both closes simultaneously with timeout
        timeout_sec = self.config.execution.order_timeout_ms / 1000
        long_task = asyncio.create_task(
            long_adapter.place_order(close_long, reduce_only=True)
        )
        short_task = asyncio.create_task(
            short_adapter.place_order(close_short, reduce_only=True)
        )

        results = await asyncio.gather(long_task, short_task, return_exceptions=True)
        long_result, short_result = results[0], results[1]

        long_ok = not isinstance(long_result, Exception)
        short_ok = not isinstance(short_result, Exception)

        if long_ok and short_ok:
            logger.info(
                "Trade closed successfully",
                symbol=opp.symbol,
                long_exchange=opp.exchange_long,
                short_exchange=opp.exchange_short,
            )
            active.trade.state = TradeState.CLOSED
            return True

        # Partial close - retry the failed leg
        if long_ok and not short_ok:
            logger.error("Failed to close short leg, retrying...", error=str(short_result))
            try:
                await short_adapter.place_order(close_short)
                active.trade.state = TradeState.CLOSED
                return True
            except Exception as e:
                logger.error("Retry close short failed", error=str(e))

        if short_ok and not long_ok:
            logger.error("Failed to close long leg, retrying...", error=str(long_result))
            try:
                await long_adapter.place_order(close_long)
                active.trade.state = TradeState.CLOSED
                return True
            except Exception as e:
                logger.error("Retry close long failed", error=str(e))

        logger.error("CRITICAL: Trade close failed on both legs!", symbol=opp.symbol)
        return False

    async def execute_opportunity(self, opportunity: OpportunityCandidate) -> TradeRecord:
        """Execute an opportunity (paired long/short) and schedule exit"""
        trade = TradeRecord(opportunity=opportunity, state=TradeState.PRE_FLIGHT)

        long_adapter = self.exchange_manager.get_adapter(opportunity.exchange_long)
        short_adapter = self.exchange_manager.get_adapter(opportunity.exchange_short)

        if not long_adapter or not short_adapter:
            trade.state = TradeState.ERROR_RECOVERY
            trade.errors.append("Missing exchange adapter")
            logger.error("Missing exchange adapter", opportunity_id=str(opportunity.opportunity_id))
            return trade

        long_order = OrderRequest(
            exchange=opportunity.exchange_long,
            symbol=opportunity.symbol,
            side=OrderSide.LONG,
            quantity=opportunity.quantity,
            price=None,  # Market order for reliable fills
        )
        short_order = OrderRequest(
            exchange=opportunity.exchange_short,
            symbol=opportunity.symbol,
            side=OrderSide.SHORT,
            quantity=opportunity.quantity,
            price=None,  # Market order for reliable fills
        )

        trade.state = TradeState.PENDING_OPEN

        long_task = asyncio.create_task(long_adapter.place_order(long_order))
        short_task = asyncio.create_task(short_adapter.place_order(short_order))

        results = await asyncio.gather(long_task, short_task, return_exceptions=True)
        long_result, short_result = results[0], results[1]

        long_ok = not isinstance(long_result, Exception)
        short_ok = not isinstance(short_result, Exception)

        if long_ok and short_ok:
            trade.state = TradeState.ACTIVE_HEDGED
            trade.expected_net_bps = opportunity.expected_net_bps

            logger.info(
                "Executed paired orders - now tracking for exit",
                opportunity_id=str(opportunity.opportunity_id),
                long_order_id=long_result.get("id"),
                short_order_id=short_result.get("id"),
            )

            # Register for exit monitoring
            active = ActiveTrade(opportunity, trade)
            self.active_trades.append(active)

            # Start exit monitor if not running
            await self.start_exit_monitor()

            return trade

        trade.state = TradeState.ERROR_RECOVERY

        if not long_ok:
            trade.errors.append(str(long_result))
        if not short_ok:
            trade.errors.append(str(short_result))

        logger.error(
            "Execution failed",
            opportunity_id=str(opportunity.opportunity_id),
            long_ok=long_ok,
            short_ok=short_ok,
        )

        await self._attempt_recovery(
            opportunity,
            long_adapter,
            short_adapter,
            long_result if long_ok else None,
            short_result if short_ok else None,
        )

        return trade

    async def _attempt_recovery(
        self,
        opportunity: OpportunityCandidate,
        long_adapter,
        short_adapter,
        long_result: Optional[dict],
        short_result: Optional[dict],
    ):
        """Best-effort recovery when one leg fails"""
        try:
            if long_result and not short_result:
                close_order = OrderRequest(
                    exchange=opportunity.exchange_long,
                    symbol=opportunity.symbol,
                    side=OrderSide.SHORT,
                    quantity=opportunity.quantity,
                    price=None,
                )
                await long_adapter.place_order(close_order)
                logger.info("Recovery: closed orphaned long leg")

            if short_result and not long_result:
                close_order = OrderRequest(
                    exchange=opportunity.exchange_short,
                    symbol=opportunity.symbol,
                    side=OrderSide.LONG,
                    quantity=opportunity.quantity,
                    price=None,
                )
                await short_adapter.place_order(close_order)
                logger.info("Recovery: closed orphaned short leg")
        except Exception as e:
            logger.error("Recovery failed", error=str(e), opportunity_id=str(opportunity.opportunity_id))

    async def estimate_execution_cost(self, opportunity: OpportunityCandidate) -> Decimal:
        """Estimate execution cost (simple placeholder)"""
        return opportunity.total_fees_bps + opportunity.total_slippage_bps
