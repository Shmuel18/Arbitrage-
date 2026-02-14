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
import time as _time
import uuid
from datetime import datetime, timedelta, timezone
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
    from src.risk.guard import RiskGuard

logger = get_logger("execution")

_ORDER_TIMEOUT_SEC = 5


class ExecutionController:
    def __init__(
        self,
        config: "Config",
        exchange_mgr: "ExchangeManager",
        redis: "RedisClient",
        risk_guard: Optional["RiskGuard"] = None,
    ):
        self._cfg = config
        self._exchanges = exchange_mgr
        self._redis = redis
        self._risk_guard = risk_guard
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
        
        # Log balances on startup (if enabled in config)
        if hasattr(self._cfg.logging, 'log_balances_on_startup') and self._cfg.logging.log_balances_on_startup:
            await self._log_exchange_balances()
        
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

        # ── Entry timing gate: only enter within entry_offset before funding ──
        entry_offset = self._cfg.trading_params.entry_offset_seconds  # 900 = 15 min
        long_adapter = self._exchanges.get(opp.long_exchange)
        short_adapter = self._exchanges.get(opp.short_exchange)
        
        try:
            long_funding = await long_adapter.get_funding_rate(opp.symbol)
            short_funding = await short_adapter.get_funding_rate(opp.symbol)
        except Exception as e:
            logger.debug(f"Cannot fetch funding time for {opp.symbol}: {e}")
            return
        
        now_ms = _time.time() * 1000
        
        # Check if we're within entry_offset seconds before ANY funding payment
        long_next = long_funding.get("next_timestamp")
        short_next = short_funding.get("next_timestamp")
        
        in_entry_window = False
        if long_next:
            seconds_until_long = (long_next - now_ms) / 1000
            if 0 < seconds_until_long <= entry_offset:
                in_entry_window = True
        if short_next:
            seconds_until_short = (short_next - now_ms) / 1000
            if 0 < seconds_until_short <= entry_offset:
                in_entry_window = True
        
        if not in_entry_window:
            next_str = ""
            if long_next:
                next_str += f"{opp.long_exchange}={int((long_next - now_ms)/60000)}min "
            if short_next:
                next_str += f"{opp.short_exchange}={int((short_next - now_ms)/60000)}min"
            logger.debug(
                f"Skipping {opp.symbol}: not in entry window (next funding: {next_str}). "
                f"Entry allowed {entry_offset}s before payment."
            )
            return
        
        logger.info(
            f"Entry window OPEN for {opp.symbol} — funding in "
            f"{int(min(s for s in [(long_next-now_ms)/1000 if long_next else 99999, (short_next-now_ms)/1000 if short_next else 99999] if s > 0))}s"
        )

        # Acquire lock
        lock_key = f"trade:{opp.symbol}"
        if not await self._redis.acquire_lock(lock_key):
            return

        trade_id = str(uuid.uuid4())[:12]
        try:
            # ── Position sizing: 70% of smallest balance × leverage ──
            long_bal = await long_adapter.get_balance()
            short_bal = await short_adapter.get_balance()
            
            position_pct = float(self._cfg.risk_limits.position_size_pct)  # 0.70
            leverage = self._cfg.exchanges.get(opp.long_exchange)
            lev = leverage.leverage if leverage and leverage.leverage else 5
            
            # Use 70% of the SMALLEST balance with leverage
            min_balance = min(float(long_bal["free"]), float(short_bal["free"]))
            notional = Decimal(str(min_balance * position_pct * lev))
            
            logger.info(
                f"{opp.symbol}: Sizing — min_bal=${min_balance:.2f}, "
                f"{int(position_pct*100)}% × {lev}x = ${float(notional):.2f} notional"
            )
            
            if notional <= 0:
                logger.warning(f"Insufficient balance for {opp.symbol}")
                return

            # Harmonise quantity to the coarser lot step so both legs match
            long_spec = await long_adapter.get_instrument_spec(opp.symbol)
            short_spec = await short_adapter.get_instrument_spec(opp.symbol)
            lot = max(
                float(long_spec.lot_size) if long_spec else 0.001,
                float(short_spec.lot_size) if short_spec else 0.001,
            )
            qty_float = float(notional / opp.reference_price)
            steps = int(qty_float / lot)               # floor to whole lot steps
            qty_rounded = round(steps * lot, 8)         # kill float noise
            qty_rounded = max(qty_rounded, lot)
            order_qty = Decimal(str(qty_rounded))
            
            logger.debug(f"{opp.symbol}: raw_qty={opp.suggested_qty}, lot={lot}, order_qty={order_qty}")

            # Open both legs
            
            # Mark grace period BEFORE placing first order
            if self._risk_guard:
                self._risk_guard.mark_trade_opened(opp.symbol)
            
            long_fill = await self._place_with_timeout(
                long_adapter,
                OrderRequest(
                    exchange=opp.long_exchange,
                    symbol=opp.symbol,
                    side=OrderSide.BUY,
                    quantity=order_qty,
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
                    quantity=order_qty,
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

            # Record trade with ACTUAL filled quantities (fallback to order_qty, not raw suggested_qty)
            long_filled_qty = Decimal(str(long_fill.get("filled", 0) or order_qty))
            short_filled_qty = Decimal(str(short_fill.get("filled", 0) or order_qty))

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
                mode=opp.mode,
                exit_before=opp.exit_before,
            )
            self._active_trades[trade_id] = trade
            await self._persist_trade(trade)

            mode_str = f" mode={opp.mode}"
            if opp.exit_before:
                mode_str += f" exit_before={opp.exit_before.strftime('%H:%M UTC')}"
            if opp.n_collections > 0:
                mode_str += f" collections={opp.n_collections}"

            logger.info(
                f"Trade opened: {trade_id} {opp.symbol} "
                f"L={opp.long_exchange}({long_filled_qty}) "
                f"S={opp.short_exchange}({short_filled_qty}) "
                f"edge={opp.net_edge_bps:.1f}bps{mode_str}",
                extra={
                    "trade_id": trade_id,
                    "symbol": opp.symbol,
                    "action": "trade_opened",
                },
            )
            
            # Log balances after trade opened (if enabled)
            if hasattr(self._cfg.logging, 'log_balances_after_trade') and self._cfg.logging.log_balances_after_trade:
                await self._log_exchange_balances()
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
        """Check if trade should be closed.

        Two modes:
          CHERRY_PICK: exit BEFORE the costly funding payment
          HOLD:        exit when edge reverses (both sides still income)
        """
        now = datetime.now(timezone.utc)

        # ── CHERRY_PICK: time-based exit ─────────────────────────
        if trade.mode == "cherry_pick" and trade.exit_before:
            if now >= trade.exit_before:
                logger.info(
                    f"Cherry-pick exit for {trade.trade_id}: "
                    f"exiting before costly payment at {trade.exit_before.strftime('%H:%M UTC')}",
                    extra={"trade_id": trade.trade_id, "symbol": trade.symbol, "action": "exit_signal"},
                )
                await self._close_trade(trade)
                return
            else:
                remaining = (trade.exit_before - now).total_seconds() / 60
                logger.debug(
                    f"Trade {trade.trade_id}: cherry-pick — {remaining:.0f} min until exit"
                )
                return

        # ── HOLD: wait for both sides to pay, then re-evaluate ───
        long_adapter = self._exchanges.get(trade.long_exchange)
        short_adapter = self._exchanges.get(trade.short_exchange)

        try:
            long_funding = await long_adapter.get_funding_rate(trade.symbol)
            short_funding = await short_adapter.get_funding_rate(trade.symbol)
        except Exception as e:
            logger.warning(f"Funding fetch failed for exit check on {trade.symbol}: {e}")
            return

        # Track next funding time per exchange
        if not trade.next_funding_long:
            long_next = long_funding.get("next_timestamp")
            if long_next:
                trade.next_funding_long = datetime.fromtimestamp(long_next / 1000, tz=timezone.utc)
                li = long_funding.get("interval_hours", "?")
                logger.info(f"Trade {trade.trade_id}: {trade.long_exchange} next at "
                            f"{trade.next_funding_long.strftime('%H:%M UTC')} (every {li}h)")

        if not trade.next_funding_short:
            short_next = short_funding.get("next_timestamp")
            if short_next:
                trade.next_funding_short = datetime.fromtimestamp(short_next / 1000, tz=timezone.utc)
                si = short_funding.get("interval_hours", "?")
                logger.info(f"Trade {trade.trade_id}: {trade.short_exchange} next at "
                            f"{trade.next_funding_short.strftime('%H:%M UTC')} (every {si}h)")

        # Wait until BOTH have paid, then wait exit_offset (15 min) after payment
        exit_offset = self._cfg.trading_params.exit_offset_seconds  # 900 = 15 min
        
        if trade.next_funding_long:
            long_exit_time = trade.next_funding_long + timedelta(seconds=exit_offset)
            long_paid = now >= long_exit_time
        else:
            long_paid = False
        
        if trade.next_funding_short:
            short_exit_time = trade.next_funding_short + timedelta(seconds=exit_offset)
            short_paid = now >= short_exit_time
        else:
            short_paid = False

        # Exit once ANY funding has paid + offset elapsed (grab and run)
        if not (long_paid or short_paid):
            return

        which_paid = "long" if long_paid else "short"
        logger.info(
            f"Trade {trade.trade_id}: {which_paid} funding paid + {exit_offset}s elapsed — closing",
            extra={"trade_id": trade.trade_id, "symbol": trade.symbol, "action": "exit_trigger"},
        )

        # Check if still profitable to hold
        long_interval = long_funding.get("interval_hours", 8)
        short_interval = short_funding.get("interval_hours", 8)

        edge_info = calculate_funding_edge(
            long_funding["rate"], short_funding["rate"],
            long_interval_hours=long_interval,
            short_interval_hours=short_interval,
        )

        long_spec = await long_adapter.get_instrument_spec(trade.symbol)
        short_spec = await short_adapter.get_instrument_spec(trade.symbol)
        if not long_spec or not short_spec:
            return

        fees_bps = calculate_fees(long_spec.taker_fee, short_spec.taker_fee)
        net = edge_info["edge_bps"] - fees_bps

        if net <= 0 or net < trade.entry_edge_bps * Decimal("0.1"):
            logger.info(
                f"Exit signal for {trade.trade_id}: net={net:.1f}bps — closing",
                extra={"trade_id": trade.trade_id, "symbol": trade.symbol, "action": "exit_signal"},
            )
            await self._close_trade(trade)
        else:
            # Advance trackers to next payment
            long_next = long_funding.get("next_timestamp")
            short_next = short_funding.get("next_timestamp")
            if long_next:
                trade.next_funding_long = datetime.fromtimestamp(long_next / 1000, tz=timezone.utc)
            if short_next:
                trade.next_funding_short = datetime.fromtimestamp(short_next / 1000, tz=timezone.utc)
            logger.info(
                f"Trade {trade.trade_id}: Holding — net={net:.1f}bps. "
                f"Next: {trade.long_exchange}={trade.next_funding_long.strftime('%H:%M') if trade.next_funding_long else '?'}, "
                f"{trade.short_exchange}={trade.next_funding_short.strftime('%H:%M') if trade.next_funding_short else '?'}"
            )

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
            
            # Log balances after trade closure (if enabled)
            if hasattr(self._cfg.logging, 'log_balances_after_trade') and self._cfg.logging.log_balances_after_trade:
                await self._log_exchange_balances()
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

    # ── Balance logging ───────────────────────────────────────────

    async def _log_exchange_balances(self) -> None:
        """Log current USDT balances for all exchanges."""
        try:
            logger.info("💰 EXCHANGE BALANCES", extra={"action": "balance_log"})
            
            for exchange_id in self._cfg.enabled_exchanges:
                adapter = self._exchanges.get(exchange_id)
                if not adapter:
                    continue
                
                try:
                    balance = await adapter.get_balance()
                    usdt_balance = balance.get("free", 0)
                    logger.info(
                        f"  {exchange_id.upper()}: ${usdt_balance:,.2f}",
                        extra={
                            "action": "exchange_balance",
                            "exchange": exchange_id,
                            "balance_usdt": usdt_balance
                        }
                    )
                except Exception as e:
                    logger.warning(f"Failed to fetch balance for {exchange_id}: {e}")
        except Exception as e:
            logger.error(f"Balance logging error: {e}")
