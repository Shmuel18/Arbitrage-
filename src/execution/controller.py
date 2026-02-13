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
from src.exchanges.adapter import ExchangeManager
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
        # Actual filled quantities (may differ from requested due to partial fills/rounding)
        self.long_filled_qty: Optional[Decimal] = None
        self.short_filled_qty: Optional[Decimal] = None


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
                    logger.critical(
                        "Max close attempts reached! ORPHANED POSITION - manual intervention needed!",
                        symbol=opp.symbol,
                        exchange_long=opp.exchange_long,
                        exchange_short=opp.exchange_short,
                        quantity=float(opp.quantity),
                        attempts=active.close_attempts,
                    )
                    # Persist orphan to Redis so it survives restart
                    await self._persist_orphan(active, "max_close_attempts_exceeded")
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
                    await self._log_trade_summary(active)
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

    def _parse_funding_timestamp(self, ts) -> Optional[datetime]:
        """Parse funding timestamp from various formats into naive UTC datetime"""
        if ts is None:
            return None
        if isinstance(ts, (int, float)):
            if ts > 1e12:
                ts = ts / 1000  # ms to sec
            return datetime.utcfromtimestamp(ts)
        if isinstance(ts, str):
            parsed = datetime.fromisoformat(ts.replace('Z', '+00:00'))
            # Convert timezone-aware to naive UTC
            if parsed.tzinfo is not None:
                from datetime import timezone
                parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
            return parsed
        return None

    async def _get_funding_time_for_exchange(self, exchange_id: str, symbol: str) -> Optional[datetime]:
        """Get next funding timestamp from a specific exchange"""
        adapter = self.exchange_manager.get_adapter(exchange_id)
        if not adapter:
            return None

        funding_data = await adapter.get_funding_rate(symbol)
        if not funding_data:
            return None

        # Our adapter returns 'next_timestamp' (mapped from ccxt fundingTimestamp)
        next_funding_ts = funding_data.get('next_timestamp') or funding_data.get('timestamp')

        return self._parse_funding_timestamp(next_funding_ts)

    async def _get_close_time(self, active: ActiveTrade) -> Optional[datetime]:
        """Determine when to close based on next funding timestamp from BOTH exchanges"""
        opp = active.opportunity
        try:
            # Check funding time from BOTH exchanges, close after the LATER one
            times = []
            for exchange_id in [opp.exchange_long, opp.exchange_short]:
                dt = await self._get_funding_time_for_exchange(exchange_id, opp.symbol)
                if dt:
                    times.append(dt)

            if not times:
                return None

            # Close 30 seconds AFTER the later funding payment
            return max(times) + timedelta(seconds=30)

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
        # Use actual filled qty, fallback to original requested qty
        long_close_qty = active.long_filled_qty or opp.quantity
        short_close_qty = active.short_filled_qty or opp.quantity

        close_long = OrderRequest(
            exchange=opp.exchange_long,
            symbol=opp.symbol,
            side=OrderSide.SELL,  # sell to close long
            quantity=long_close_qty,
            reduce_only=True,
        )
        close_short = OrderRequest(
            exchange=opp.exchange_short,
            symbol=opp.symbol,
            side=OrderSide.BUY,  # buy to close short
            quantity=short_close_qty,
            reduce_only=True,
        )

        # Execute both closes simultaneously with timeout
        timeout_sec = self.config.execution.order_timeout_ms / 1000
        long_task = asyncio.create_task(
            long_adapter.place_order(close_long)
        )
        short_task = asyncio.create_task(
            short_adapter.place_order(close_short)
        )

        try:
            results = await asyncio.wait_for(
                asyncio.gather(long_task, short_task, return_exceptions=True),
                timeout=timeout_sec,
            )
        except asyncio.TimeoutError:
            logger.error("Close order timed out!", symbol=opp.symbol, timeout_sec=timeout_sec)
            long_task.cancel()
            short_task.cancel()
            return False

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

        # Partial close - retry the failed leg (always reduceOnly for safety)
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

    async def _log_trade_summary(self, active: ActiveTrade) -> None:
        """Log PnL summary after closing a trade"""
        opp = active.opportunity
        held_secs = (datetime.utcnow() - active.opened_at).total_seconds()
        held_mins = int(held_secs / 60)

        # Fetch current balances from both exchanges
        balance_lines = []
        total_usdt = Decimal('0')
        for eid, adapter in self.exchange_manager.adapters.items():
            try:
                bal = await adapter.get_balance()
                total_usdt += bal['total']
                balance_lines.append(f"    {eid}: {bal['total']:.2f} USDT (free={bal['free']:.2f})")
            except Exception:
                balance_lines.append(f"    {eid}: (failed to fetch)")

        summary = (
            f"\n╔══════════ TRADE SUMMARY ══════════╗"
            f"\n║ Symbol   : {opp.symbol}"
            f"\n║ Long     : {opp.exchange_long}"
            f"\n║ Short    : {opp.exchange_short}"
            f"\n║ Quantity : {float(active.long_filled_qty or opp.quantity):.4f}"
            f"\n║ Expected : {float(opp.expected_net_bps):.1f} bps"
            f"\n║ Held     : {held_mins} min"
            f"\n╠══════════ BALANCES ═══════════════╣"
        )
        for bl in balance_lines:
            summary += f"\n║{bl}"
        summary += (
            f"\n║ Total portfolio: {total_usdt:.2f} USDT"
            f"\n╚══════════════════════════════════╝"
        )
        logger.info(summary)

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
            side=OrderSide.BUY,
            quantity=opportunity.quantity,
        )
        short_order = OrderRequest(
            exchange=opportunity.exchange_short,
            symbol=opportunity.symbol,
            side=OrderSide.SELL,
            quantity=opportunity.quantity,
        )

        trade.state = TradeState.PENDING_OPEN

        long_task = asyncio.create_task(long_adapter.place_order(long_order))
        short_task = asyncio.create_task(short_adapter.place_order(short_order))

        # Wrap entry in timeout
        timeout_sec = self.config.execution.order_timeout_ms / 1000
        try:
            results = await asyncio.wait_for(
                asyncio.gather(long_task, short_task, return_exceptions=True),
                timeout=timeout_sec,
            )
        except asyncio.TimeoutError:
            logger.error("Entry order timed out!", symbol=opportunity.symbol, timeout_sec=timeout_sec)
            long_task.cancel()
            short_task.cancel()
            trade.state = TradeState.ERROR_RECOVERY
            trade.errors.append("Order timeout")
            return trade

        long_result, short_result = results[0], results[1]

        long_ok = not isinstance(long_result, Exception)
        short_ok = not isinstance(short_result, Exception)

        if long_ok and short_ok:
            # A3: Verify fills - check for partial fills
            # Handle None from exchanges that don't immediately report fills
            long_filled_raw = long_result.get('filled')
            short_filled_raw = short_result.get('filled')
            long_filled = Decimal(str(long_filled_raw)) if long_filled_raw is not None else opportunity.quantity
            short_filled = Decimal(str(short_filled_raw)) if short_filled_raw is not None else opportunity.quantity

            if long_filled <= 0 or short_filled <= 0:
                logger.error(
                    "Zero fill detected!",
                    long_filled=float(long_filled),
                    short_filled=float(short_filled),
                )
                trade.state = TradeState.ERROR_RECOVERY
                trade.errors.append(f"Zero fill: long={long_filled}, short={short_filled}")
                await self._attempt_recovery(
                    opportunity, long_adapter, short_adapter,
                    long_result if long_filled > 0 else None,
                    short_result if short_filled > 0 else None,
                )
                return trade

            # Warn if partial fill (not 100%)
            if long_filled < opportunity.quantity or short_filled < opportunity.quantity:
                logger.warning(
                    "Partial fill detected - using actual filled quantities",
                    requested=float(opportunity.quantity),
                    long_filled=float(long_filled),
                    short_filled=float(short_filled),
                )

            trade.state = TradeState.ACTIVE_HEDGED
            trade.expected_net_bps = opportunity.expected_net_bps

            logger.info(
                "Executed paired orders - now tracking for exit",
                opportunity_id=str(opportunity.opportunity_id),
                long_order_id=long_result.get("id"),
                short_order_id=short_result.get("id"),
                long_filled=float(long_filled),
                short_filled=float(short_filled),
            )

            # Register for exit monitoring with actual filled quantities
            active = ActiveTrade(opportunity, trade)
            active.long_filled_qty = long_filled
            active.short_filled_qty = short_filled
            self.active_trades.append(active)

            # Persist to Redis so trades survive restarts
            await self._persist_trade(active)

            # Start exit monitor if not running
            await self.start_exit_monitor()

            return trade

        trade.state = TradeState.ERROR_RECOVERY

        if not long_ok:
            trade.errors.append(str(long_result))
        if not short_ok:
            trade.errors.append(str(short_result))

        # Build descriptive error message
        err_detail = f"symbol={opportunity.symbol} long({opportunity.exchange_long})={'OK' if long_ok else str(long_result)[:200]} short({opportunity.exchange_short})={'OK' if short_ok else str(short_result)[:200]}"
        logger.error(f"Execution failed: {err_detail}")

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
                filled_raw = long_result.get('filled')
                filled = Decimal(str(filled_raw)) if filled_raw is not None else opportunity.quantity
                if filled > 0:
                    close_order = OrderRequest(
                        exchange=opportunity.exchange_long,
                        symbol=opportunity.symbol,
                        side=OrderSide.SELL,
                        quantity=filled,
                        reduce_only=True,
                    )
                    await long_adapter.place_order(close_order)
                    logger.info("Recovery: closed orphaned long leg", filled=float(filled))

            if short_result and not long_result:
                filled_raw = short_result.get('filled')
                filled = Decimal(str(filled_raw)) if filled_raw is not None else opportunity.quantity
                if filled > 0:
                    close_order = OrderRequest(
                        exchange=opportunity.exchange_short,
                        symbol=opportunity.symbol,
                        side=OrderSide.BUY,
                        quantity=filled,
                        reduce_only=True,
                    )
                    await short_adapter.place_order(close_order)
                    logger.info("Recovery: closed orphaned short leg", filled=float(filled))
        except Exception as e:
            logger.critical(
                "Recovery FAILED - orphaned position may exist!",
                error=str(e),
                opportunity_id=str(opportunity.opportunity_id),
                symbol=opportunity.symbol,
            )
            # Persist orphan info to Redis
            if self.redis_client:
                try:
                    await self.redis_client.set_trade_state(
                        opportunity.opportunity_id or "unknown",
                        {
                            "type": "orphan_recovery_failed",
                            "symbol": opportunity.symbol,
                            "exchange_long": opportunity.exchange_long,
                            "exchange_short": opportunity.exchange_short,
                            "quantity": str(opportunity.quantity),
                            "error": str(e),
                            "timestamp": datetime.utcnow().isoformat(),
                        },
                    )
                except Exception:
                    pass

    async def estimate_execution_cost(self, opportunity: OpportunityCandidate) -> Decimal:
        """Estimate execution cost (simple placeholder)"""
        return opportunity.total_fees_bps + opportunity.total_slippage_bps

    # ==================== PERSISTENCE ====================

    async def _persist_trade(self, active: ActiveTrade):
        """Persist active trade to Redis so it survives process restart"""
        if not self.redis_client:
            return
        try:
            opp = active.opportunity
            await self.redis_client.set_trade_state(
                active.trade.trade_id,
                {
                    "type": "active_trade",
                    "symbol": opp.symbol,
                    "exchange_long": opp.exchange_long,
                    "exchange_short": opp.exchange_short,
                    "quantity": str(opp.quantity),
                    "long_filled_qty": str(active.long_filled_qty or opp.quantity),
                    "short_filled_qty": str(active.short_filled_qty or opp.quantity),
                    "opened_at": active.opened_at.isoformat(),
                    "state": active.trade.state.value,
                    "expected_net_bps": str(opp.expected_net_bps),
                },
            )
        except Exception as e:
            logger.warning("Failed to persist trade to Redis", error=str(e))

    async def _persist_orphan(self, active: ActiveTrade, reason: str):
        """Persist orphaned trade info to Redis for manual recovery"""
        if not self.redis_client:
            return
        try:
            opp = active.opportunity
            await self.redis_client.set_trade_state(
                active.trade.trade_id,
                {
                    "type": "orphan",
                    "reason": reason,
                    "symbol": opp.symbol,
                    "exchange_long": opp.exchange_long,
                    "exchange_short": opp.exchange_short,
                    "quantity": str(opp.quantity),
                    "long_filled_qty": str(active.long_filled_qty or opp.quantity),
                    "short_filled_qty": str(active.short_filled_qty or opp.quantity),
                    "opened_at": active.opened_at.isoformat(),
                    "close_attempts": active.close_attempts,
                    "timestamp": datetime.utcnow().isoformat(),
                },
            )
            logger.critical(
                "Orphaned trade persisted to Redis",
                trade_id=str(active.trade.trade_id),
                symbol=opp.symbol,
                reason=reason,
            )
        except Exception as e:
            logger.error("Failed to persist orphan to Redis", error=str(e))

    async def close_all_positions(self):
        """Emergency close all active positions — used during shutdown"""
        if not self.active_trades:
            return

        logger.warning("Closing all active positions before shutdown", count=len(self.active_trades))
        for active in list(self.active_trades):
            try:
                success = await self._close_trade(active)
                if success:
                    logger.info("Shutdown: closed trade", symbol=active.opportunity.symbol)
                else:
                    logger.error("Shutdown: failed to close trade", symbol=active.opportunity.symbol)
                    await self._persist_orphan(active, "shutdown_close_failed")
            except Exception as e:
                logger.error("Shutdown: exception closing trade", symbol=active.opportunity.symbol, error=str(e))
                await self._persist_orphan(active, f"shutdown_exception: {e}")
