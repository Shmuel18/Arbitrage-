"""
Execution Controller
Responsible for placing paired orders across exchanges
"""

import asyncio
from decimal import Decimal
from typing import Optional

from src.core.config import get_config
from src.core.contracts import OrderRequest, OrderSide, OpportunityCandidate, TradeRecord, TradeState
from src.core.logging import get_logger
from src.exchanges.base import ExchangeManager
from src.storage.redis_client import RedisClient

logger = get_logger("execution_controller")


class ExecutionController:
    """Executes opportunities across exchanges"""

    def __init__(self, exchange_manager: ExchangeManager, redis_client: Optional[RedisClient] = None):
        self.config = get_config()
        self.exchange_manager = exchange_manager
        self.redis_client = redis_client

    async def execute_opportunity(self, opportunity: OpportunityCandidate) -> TradeRecord:
        """Execute an opportunity (paired long/short)"""
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
            price=opportunity.long_entry_price,
        )
        short_order = OrderRequest(
            exchange=opportunity.exchange_short,
            symbol=opportunity.symbol,
            side=OrderSide.SHORT,
            quantity=opportunity.quantity,
            price=opportunity.short_entry_price,
        )

        trade.state = TradeState.PENDING_OPEN

        long_task = asyncio.create_task(long_adapter.place_order(long_order))
        short_task = asyncio.create_task(short_adapter.place_order(short_order))

        results = await asyncio.gather(long_task, short_task, return_exceptions=True)
        long_result, short_result = results[0], results[1]

        long_ok = not isinstance(long_result, Exception)
        short_ok = not isinstance(short_result, Exception)

        if long_ok and short_ok:
            trade.state = TradeState.OPEN_PARTIAL
            trade.expected_net_bps = opportunity.expected_net_bps

            logger.info(
                "Executed paired orders",
                opportunity_id=str(opportunity.opportunity_id),
                long_order_id=long_result.get("id"),
                short_order_id=short_result.get("id"),
            )

            trade.state = TradeState.ACTIVE_HEDGED
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
                order_id = long_result.get("id")
                if order_id:
                    await long_adapter.cancel_order(opportunity.symbol, order_id)
            if short_result and not long_result:
                order_id = short_result.get("id")
                if order_id:
                    await short_adapter.cancel_order(opportunity.symbol, order_id)
        except Exception as e:
            logger.error("Recovery failed", error=str(e), opportunity_id=str(opportunity.opportunity_id))

    async def estimate_execution_cost(self, opportunity: OpportunityCandidate) -> Decimal:
        """Estimate execution cost (simple placeholder)"""
        return opportunity.total_fees_bps + opportunity.total_slippage_bps
