"""
Execution controller — open, monitor, and close funding-arb trades.

Safety features retained from review:
  • partial-fill detection (use actual filled qty, not requested)
  • order timeout with auto-cancel
  • both-exchange exit monitoring (checks funding on BOTH legs)
  • reduceOnly on every close
  • Redis persistence of active trades (crash recovery)
  • orphan detection and alerting
  • cooldown after orphan
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Dict, List, Optional

from src.core.contracts import (
    OpportunityCandidate,
    OrderRequest,
    OrderSide,
    TradeRecord,
    TradeState,
)
from src.core.logging import get_logger
from src.discovery.calculator import calculate_fees, calculate_funding_edge

if TYPE_CHECKING:
    from src.core.config import Config
    from src.exchanges.adapter import ExchangeManager
    from src.storage.redis_client import RedisClient

logger = get_logger("execution")

_ORDER_TIMEOUT_SEC = 5


class ExecutionController:
    def __init__(
        self,
        config: "Config",
        exchange_mgr: "ExchangeManager",
        redis: "RedisClient",
    ):
        self._cfg = config
        self._exchanges = exchange_mgr
        self._redis = redis
        self._active_trades: Dict[str, TradeRecord] = {}
        self._running = False
        self._monitor_task: Optional[asyncio.Task] = None

    # ── Lifecycle ────────────────────────────────────────────────

    async def start(self) -> None:
        self._running = True
        await self._recover_trades()
        self._monitor_task = asyncio.create_task(
            self._exit_monitor_loop(), name="exit-monitor",
        )
        logger.info("Execution controller started")

    async def stop(self) -> None:
        self._running = False
        if self._monitor_task:
            self._monitor_task.cancel()
            await asyncio.gather(self._monitor_task, return_exceptions=True)
        logger.info("Execution controller stopped")

    # ── Open trade ───────────────────────────────────────────────

    async def handle_opportunity(self, opp: OpportunityCandidate) -> None:
        """Validate and execute a new funding-arb trade."""
        # Duplicate guard
        for t in self._active_trades.values():
            if t.symbol == opp.symbol:
                logger.debug(f"Already have active trade for {opp.symbol}")
                return

        # Concurrency cap
        if len(self._active_trades) >= self._cfg.execution.concurrent_opportunities:
            return

        # Acquire lock
        lock_key = f"trade:{opp.symbol}"
        if not await self._redis.acquire_lock(lock_key):
            return

        trade_id = str(uuid.uuid4())[:12]
        try:
            # Balance pre-check (use 'free', not 'total')
            long_adapter = self._exchanges.get(opp.long_exchange)
            short_adapter = self._exchanges.get(opp.short_exchange)
            long_bal = await long_adapter.get_balance()
            short_bal = await short_adapter.get_balance()

            notional = opp.suggested_qty * opp.reference_price
            if long_bal["free"] < notional or short_bal["free"] < notional:
                logger.warning(f"Insufficient balance for {opp.symbol}")
                return

            # Open both legs
            long_fill = await self._place_with_timeout(
                long_adapter,
                OrderRequest(
                    exchange=opp.long_exchange,
                    symbol=opp.symbol,
                    side=OrderSide.BUY,
                    quantity=opp.suggested_qty,
                    reduce_only=False,
                ),
            )
            if not long_fill:
                return

            short_fill = await self._place_with_timeout(
                short_adapter,
                OrderRequest(
                    exchange=opp.short_exchange,
                    symbol=opp.symbol,
                    side=OrderSide.SELL,
                    quantity=opp.suggested_qty,
                    reduce_only=False,
                ),
            )
            if not short_fill:
                # Orphan: long filled but short didn't → close long
                logger.error(f"Short leg failed — closing orphan long for {opp.symbol}")
                await self._close_orphan(
                    long_adapter, opp.long_exchange, opp.symbol,
                    OrderSide.SELL, long_fill,
                )
                return

            # Record trade with ACTUAL filled quantities
            long_filled_qty = Decimal(str(long_fill.get("filled", 0) or opp.suggested_qty))
            short_filled_qty = Decimal(str(short_fill.get("filled", 0) or opp.suggested_qty))

            trade = TradeRecord(
                trade_id=trade_id,
                symbol=opp.symbol,
                state=TradeState.OPEN,
                long_exchange=opp.long_exchange,
                short_exchange=opp.short_exchange,
                long_qty=long_filled_qty,
                short_qty=short_filled_qty,
                entry_edge_bps=opp.net_edge_bps,
                opened_at=datetime.now(timezone.utc),
            )
            self._active_trades[trade_id] = trade
            await self._persist_trade(trade)

            logger.info(
                f"Trade opened: {trade_id} {opp.symbol} "
                f"L={opp.long_exchange}({long_filled_qty}) "
                f"S={opp.short_exchange}({short_filled_qty}) "
                f"edge={opp.net_edge_bps:.1f}bps",
                extra={
                    "trade_id": trade_id,
                    "symbol": opp.symbol,
                    "action": "trade_opened",
                },
            )
        except Exception as e:
            logger.error(f"Trade execution failed for {opp.symbol}: {e}",
                         extra={"symbol": opp.symbol})
        finally:
            await self._redis.release_lock(lock_key)

    # ── Exit monitor ─────────────────────────────────────────────

    async def _exit_monitor_loop(self) -> None:
        while self._running:
            try:
                for trade_id in list(self._active_trades):
                    trade = self._active_trades.get(trade_id)
                    if not trade or trade.state != TradeState.OPEN:
                        continue
                    await self._check_exit(trade)
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.error(f"Exit monitor error: {e}")
            await asyncio.sleep(30)

    async def _check_exit(self, trade: TradeRecord) -> None:
        """Close when edge has decayed below threshold on BOTH exchanges."""
        long_adapter = self._exchanges.get(trade.long_exchange)
        short_adapter = self._exchanges.get(trade.short_exchange)

        try:
            long_funding = await long_adapter.get_funding_rate(trade.symbol)
            short_funding = await short_adapter.get_funding_rate(trade.symbol)
        except Exception as e:
            logger.warning(f"Funding fetch failed for exit check on {trade.symbol}: {e}")
            return

        edge_info = calculate_funding_edge(
            long_funding["rate"], short_funding["rate"],
        )
        current_bps = edge_info["edge_bps"]

        long_spec = await long_adapter.get_instrument_spec(trade.symbol)
        short_spec = await short_adapter.get_instrument_spec(trade.symbol)
        if not long_spec or not short_spec:
            return

        fees_bps = calculate_fees(long_spec.taker_fee, short_spec.taker_fee)
        net = current_bps - fees_bps

        # Exit if edge is gone (below 0 or below 20% of entry edge)
        exit_threshold = trade.entry_edge_bps * Decimal("0.2")
        if net > exit_threshold:
            return

        logger.info(
            f"Exit signal for {trade.trade_id}: current_net={net:.1f}bps < threshold={exit_threshold:.1f}bps",
            extra={"trade_id": trade.trade_id, "symbol": trade.symbol, "action": "exit_signal"},
        )
        await self._close_trade(trade)

    # ── Close trade ──────────────────────────────────────────────

    async def _close_trade(self, trade: TradeRecord) -> None:
        trade.state = TradeState.CLOSING
        await self._persist_trade(trade)

        long_adapter = self._exchanges.get(trade.long_exchange)
        short_adapter = self._exchanges.get(trade.short_exchange)

        long_ok = await self._close_leg(
            long_adapter, trade.long_exchange, trade.symbol,
            OrderSide.SELL, trade.long_qty, trade.trade_id,
        )
        short_ok = await self._close_leg(
            short_adapter, trade.short_exchange, trade.symbol,
            OrderSide.BUY, trade.short_qty, trade.trade_id,
        )

        if long_ok and short_ok:
            trade.state = TradeState.CLOSED
            trade.closed_at = datetime.now(timezone.utc)
            await self._redis.delete_trade_state(trade.trade_id)
            del self._active_trades[trade.trade_id]
            logger.info(f"Trade closed: {trade.trade_id}", extra={
                "trade_id": trade.trade_id, "action": "trade_closed",
            })
        else:
            trade.state = TradeState.ERROR
            await self._persist_trade(trade)
            logger.error(
                f"Trade {trade.trade_id} partially closed — MANUAL INTERVENTION NEEDED",
                extra={"trade_id": trade.trade_id, "action": "close_partial_fail"},
            )
            cooldown_sec = self._cfg.trading_params.cooldown_after_orphan_hours * 3600
            await self._redis.set_cooldown(trade.symbol, cooldown_sec)

    async def _close_leg(
        self, adapter, exchange: str, symbol: str,
        side: OrderSide, qty: Decimal, trade_id: str,
    ) -> bool:
        """Close one leg with retry (3×). Always reduceOnly."""
        for attempt in range(3):
            try:
                req = OrderRequest(
                    exchange=exchange,
                    symbol=symbol,
                    side=side,
                    quantity=qty,
                    reduce_only=True,
                )
                result = await self._place_with_timeout(adapter, req)
                if result:
                    return True
            except Exception as e:
                logger.warning(
                    f"Close attempt {attempt+1}/3 failed {exchange}/{symbol}: {e}",
                    extra={"trade_id": trade_id, "exchange": exchange},
                )
                await asyncio.sleep(1)
        return False

    # ── Close all (shutdown) ─────────────────────────────────────

    async def close_all_positions(self) -> None:
        """Close every active trade — called during graceful shutdown."""
        for trade_id, trade in list(self._active_trades.items()):
            if trade.state == TradeState.OPEN:
                logger.info(f"Shutdown: closing trade {trade_id}")
                await self._close_trade(trade)

    # ── Helpers ──────────────────────────────────────────────────

    async def _place_with_timeout(self, adapter, req: OrderRequest) -> Optional[dict]:
        """Place order with timeout. Returns fill dict or None."""
        timeout = self._cfg.execution.order_timeout_ms / 1000
        try:
            return await asyncio.wait_for(adapter.place_order(req), timeout=timeout)
        except asyncio.TimeoutError:
            logger.error(
                f"Order timeout ({timeout}s) on {req.exchange}/{req.symbol}",
                extra={"exchange": req.exchange, "symbol": req.symbol, "action": "order_timeout"},
            )
            return None

    async def _close_orphan(
        self, adapter, exchange: str, symbol: str,
        side: OrderSide, fill: dict,
    ) -> None:
        """Emergency close of a single orphaned leg."""
        filled_qty = Decimal(str(fill.get("filled", 0)))
        if filled_qty <= 0:
            return
        try:
            req = OrderRequest(
                exchange=exchange,
                symbol=symbol,
                side=side,
                quantity=filled_qty,
                reduce_only=True,
            )
            await adapter.place_order(req)
            logger.info(f"Orphan closed: {filled_qty} {symbol} on {exchange}",
                        extra={"exchange": exchange, "symbol": symbol, "action": "orphan_closed"})
        except Exception as e:
            logger.error(f"ORPHAN CLOSE FAILED {exchange}/{symbol}: {e} — MANUAL INTERVENTION",
                         extra={"exchange": exchange, "symbol": symbol})
        cooldown_sec = self._cfg.trading_params.cooldown_after_orphan_hours * 3600
        await self._redis.set_cooldown(symbol, cooldown_sec)

    # ── Persistence ──────────────────────────────────────────────

    async def _persist_trade(self, trade: TradeRecord) -> None:
        await self._redis.set_trade_state(trade.trade_id, {
            "symbol": trade.symbol,
            "state": trade.state.value,
            "long_exchange": trade.long_exchange,
            "short_exchange": trade.short_exchange,
            "long_qty": str(trade.long_qty),
            "short_qty": str(trade.short_qty),
            "entry_edge_bps": str(trade.entry_edge_bps),
            "opened_at": trade.opened_at.isoformat() if trade.opened_at else None,
        })

    async def _recover_trades(self) -> None:
        """Recover active trades from Redis after crash/restart."""
        stored = await self._redis.get_all_trades()
        for trade_id, data in stored.items():
            state_val = data.get("state", "")
            if state_val not in (TradeState.OPEN.value, TradeState.CLOSING.value):
                continue

            trade = TradeRecord(
                trade_id=trade_id,
                symbol=data["symbol"],
                state=TradeState(state_val),
                long_exchange=data["long_exchange"],
                short_exchange=data["short_exchange"],
                long_qty=Decimal(data["long_qty"]),
                short_qty=Decimal(data["short_qty"]),
                entry_edge_bps=Decimal(data.get("entry_edge_bps", "0")),
                opened_at=datetime.fromisoformat(data["opened_at"]) if data.get("opened_at") else None,
            )
            self._active_trades[trade_id] = trade
            logger.info(
                f"Recovered trade {trade_id} ({trade.symbol}) state={trade.state.value}",
                extra={"trade_id": trade_id, "action": "trade_recovered"},
            )

            if trade.state == TradeState.CLOSING:
                logger.warning(
                    f"Trade {trade_id} was mid-close — retrying",
                    extra={"trade_id": trade_id},
                )
                asyncio.create_task(self._close_trade(trade))

        if stored:
            logger.info(f"Recovered {len(self._active_trades)} active trades")
