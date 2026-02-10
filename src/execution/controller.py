"""
Execution Controller
Responsible for placing paired orders across exchanges
"""

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

        try:
            long_result = await long_adapter.place_order(long_order)
            short_result = await short_adapter.place_order(short_order)

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

        except Exception as e:
            trade.state = TradeState.ERROR_RECOVERY
            trade.errors.append(str(e))
            logger.error("Execution failed", error=str(e), opportunity_id=str(opportunity.opportunity_id))
            return trade

    async def estimate_execution_cost(self, opportunity: OpportunityCandidate) -> Decimal:
        """Estimate execution cost (simple placeholder)"""
        return opportunity.total_fees_bps + opportunity.total_slippage_bps
