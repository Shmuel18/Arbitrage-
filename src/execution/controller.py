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
import json
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
from src.discovery.calculator import calculate_fees, calculate_funding_spread

if TYPE_CHECKING:
    from src.core.config import Config
    from src.exchanges.adapter import ExchangeManager
    from src.storage.redis_client import RedisClient
    from src.risk.guard import RiskGuard
    from src.api.publisher import APIPublisher

logger = get_logger("execution")

_ORDER_TIMEOUT_SEC = 5


class ExecutionController:
    def __init__(
        self,
        config: "Config",
        exchange_mgr: "ExchangeManager",
        redis: "RedisClient",
        risk_guard: Optional["RiskGuard"] = None,
        publisher: Optional["APIPublisher"] = None,
    ):
        self._cfg = config
        self._exchanges = exchange_mgr
        self._redis = redis
        self._risk_guard = risk_guard
        self._publisher = publisher
        self._active_trades: Dict[str, TradeRecord] = {}
        self._running = False
        self._monitor_task: Optional[asyncio.Task] = None
        # Runtime blacklist: maps "symbol:exchange" -> expiry timestamp
        self._blacklist: Dict[str, float] = {}

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

    # ── Blacklist helpers ────────────────────────────────────────

    _BLACKLIST_DURATION_SEC = 6 * 3600  # 6 hours default

    def _add_to_blacklist(self, symbol: str, exchange: str) -> None:
        key = f"{symbol}:{exchange}"
        expiry = _time.time() + self._BLACKLIST_DURATION_SEC
        self._blacklist[key] = expiry
        logger.warning(
            f"⛔ Blacklisted {symbol} on {exchange} for "
            f"{self._BLACKLIST_DURATION_SEC // 3600}h",
            extra={"symbol": symbol, "exchange": exchange, "action": "blacklisted"},
        )

    def _is_blacklisted(self, symbol: str, long_ex: str, short_ex: str) -> bool:
        now = _time.time()
        # Clean expired entries
        expired = [k for k, v in self._blacklist.items() if v < now]
        for k in expired:
            del self._blacklist[k]
            sym, ex = k.rsplit(":", 1)
            logger.info(f"✅ Blacklist expired for {sym} on {ex}")

        for ex in (long_ex, short_ex):
            key = f"{symbol}:{ex}"
            if key in self._blacklist:
                remaining = int((self._blacklist[key] - now) / 60)
                logger.debug(
                    f"Skipping {symbol}: {ex} is blacklisted ({remaining}min left)"
                )
                return True
        return False

    # ── Open trade ───────────────────────────────────────────────

    async def handle_opportunity(self, opp: OpportunityCandidate) -> None:
        """Validate and execute a new funding-arb trade."""
        logger.info(
            f"🔍 [{opp.symbol}] Evaluating opportunity: mode={opp.mode} "
            f"spread={opp.funding_spread_pct:.4f}% net={opp.net_edge_pct:.4f}% "
            f"L={opp.long_exchange} S={opp.short_exchange}"
        )

        # Blacklist guard — skip symbols/exchanges flagged as delisting etc.
        if self._is_blacklisted(opp.symbol, opp.long_exchange, opp.short_exchange):
            return

        # Duplicate guard
        for t in self._active_trades.values():
            if t.symbol == opp.symbol:
                logger.info(f"🔁 Skipping {opp.symbol}: already have active trade")
                return

        # Concurrency cap
        if len(self._active_trades) >= self._cfg.execution.concurrent_opportunities:
            logger.info(
                f"🚫 Skipping {opp.symbol}: concurrency cap reached "
                f"({len(self._active_trades)}/{self._cfg.execution.concurrent_opportunities})"
            )
            return

        # ── Funding spread gate (safety check) ──
        # For HOLD mode: raw funding_spread_pct must meet threshold
        # For CHERRY_PICK: gross_edge_pct (total collections) must meet threshold
        tp = self._cfg.trading_params
        if opp.mode == "cherry_pick":
            if opp.gross_edge_pct < tp.min_funding_spread:
                logger.info(
                    f"📉 Skipping {opp.symbol}: cherry-pick gross {opp.gross_edge_pct:.4f}% "
                    f"< min_funding_spread {tp.min_funding_spread}%"
                )
                return
        else:
            if opp.funding_spread_pct < tp.min_funding_spread:
                logger.info(
                    f"📉 Skipping {opp.symbol}: spread {opp.funding_spread_pct:.4f}% "
                    f"< min_funding_spread {tp.min_funding_spread}%"
                )
                return

        long_adapter = self._exchanges.get(opp.long_exchange)
        short_adapter = self._exchanges.get(opp.short_exchange)

        # ── Entry timing gate: enter only within 15 min before funding ──
        entry_offset = self._cfg.trading_params.entry_offset_seconds
        try:
            long_funding = await long_adapter.get_funding_rate(opp.symbol)
            short_funding = await short_adapter.get_funding_rate(opp.symbol)
        except Exception as e:
            logger.info(f"Cannot fetch funding time for {opp.symbol}: {e} — allowing entry")
            long_funding = {}
            short_funding = {}

        now_ms = _time.time() * 1000
        long_next = long_funding.get("next_timestamp")
        short_next = short_funding.get("next_timestamp")

        if long_next is None and short_next is None:
            logger.info(
                f"⏰ [{opp.symbol}] No funding timestamp — allowing entry"
            )
        else:
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
                logger.info(
                    f"⏳ Skipping {opp.symbol}: not in entry window "
                    f"(next funding: {next_str}). Entry allowed {entry_offset}s before payment."
                )
                return

        logger.info(f"✅ [{opp.symbol}] Passed all gates — proceeding to entry")

        # ── Basis Inversion Guard: check if we're buying dear and selling cheap ──
        try:
            long_ticker = await long_adapter.get_ticker(opp.symbol)
            short_ticker = await short_adapter.get_ticker(opp.symbol)
            raw_ask = long_ticker.get("ask") or opp.reference_price
            raw_bid = short_ticker.get("bid") or opp.reference_price
            long_ask = Decimal(str(raw_ask)) if raw_ask else Decimal(str(opp.reference_price))
            short_bid = Decimal(str(raw_bid)) if raw_bid else Decimal(str(opp.reference_price))
            
            # Basis loss = (ask_long - bid_short) / bid_short * 100%
            if short_bid > 0:
                basis_loss_pct = (long_ask - short_bid) / short_bid * Decimal("100")
            else:
                basis_loss_pct = Decimal("0")
            
            # If basis loss >= net_edge, skip trade (basis inverted)
            if basis_loss_pct >= opp.net_edge_pct:
                logger.warning(
                    f"🚫 [{opp.symbol}] BASIS INVERSION GUARD: "
                    f"L_ask={long_ask} > S_bid={short_bid}, "
                    f"basis_loss={basis_loss_pct:.4f}% ≥ net_edge={opp.net_edge_pct:.4f}% — REJECTED"
                )
                return
            
            logger.debug(
                f"[{opp.symbol}] Basis check OK: L_ask={long_ask}, S_bid={short_bid}, "
                f"basis_loss={basis_loss_pct:.4f}% < net_edge={opp.net_edge_pct:.4f}%"
            )
        except Exception as e:
            logger.warning(f"Cannot fetch tickers for basis check {opp.symbol}: {e}")
            # Don't reject — proceed with caution

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
            # Use the SAME leverage for all exchanges (from config)
            long_exc_cfg = self._cfg.exchanges.get(opp.long_exchange)
            short_exc_cfg = self._cfg.exchanges.get(opp.short_exchange)
            lev = int(long_exc_cfg.leverage if long_exc_cfg and long_exc_cfg.leverage else 5)
            lev_short = int(short_exc_cfg.leverage if short_exc_cfg and short_exc_cfg.leverage else 5)
            if lev != lev_short:
                logger.warning(f"Leverage mismatch: {opp.long_exchange}={lev}x vs {opp.short_exchange}={lev_short}x — using min")
                lev = min(lev, lev_short)
            
            # Use 70% of the SMALLEST balance with leverage
            long_free = float(long_bal["free"])
            short_free = float(short_bal["free"])
            min_balance = min(long_free, short_free)
            notional = Decimal(str(min_balance * position_pct * lev))
            
            logger.info(
                f"{opp.symbol}: Sizing — L={opp.long_exchange}=${long_free:.2f} S={opp.short_exchange}=${short_free:.2f} "
                f"min_bal=${min_balance:.2f} × {int(position_pct*100)}% × {lev}x = ${float(notional):.2f} notional"
            )
            
            if notional <= 0:
                logger.warning(f"Insufficient balance for {opp.symbol}")
                return

            # Harmonise quantity to the coarser lot step so both legs match
            # lot_size is in NATIVE exchange units (contracts) — convert to BASE currency (tokens)
            long_spec = await long_adapter.get_instrument_spec(opp.symbol)
            short_spec = await short_adapter.get_instrument_spec(opp.symbol)
            long_cs = float(long_spec.contract_size) if long_spec and long_spec.contract_size else 1.0
            short_cs = float(short_spec.contract_size) if short_spec and short_spec.contract_size else 1.0
            long_lot_base = (float(long_spec.lot_size) * long_cs) if long_spec else 0.001
            short_lot_base = (float(short_spec.lot_size) * short_cs) if short_spec else 0.001
            lot = max(long_lot_base, short_lot_base)    # coarsest step in base currency
            qty_float = float(notional / opp.reference_price)
            steps = int(qty_float / lot)               # floor to whole lot steps
            qty_rounded = round(steps * lot, 8)         # kill float noise
            qty_rounded = max(qty_rounded, lot)
            order_qty = Decimal(str(qty_rounded))       # always in base currency (tokens)
            
            logger.info(
                f"{opp.symbol}: Qty — notional=${float(notional):.2f} / ${float(opp.reference_price):.4f} = {qty_float:.4f} tokens, "
                f"lot_base={lot} (L:{long_lot_base}/S:{short_lot_base}), "
                f"L_cs={long_cs} S_cs={short_cs}, order_qty={order_qty}"
            )

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

            # ── Sync-Fire: adjust short qty to match long's ACTUAL filled qty ──
            long_actual_filled = Decimal(str(long_fill.get("filled", 0) or order_qty))
            is_partial_fill = long_actual_filled < order_qty
            
            if is_partial_fill:
                logger.warning(
                    f"⚠️ [{opp.symbol}] PARTIAL FILL DETECTED: "
                    f"Long filled {long_actual_filled} / {order_qty} — "
                    f"Sync-Fire: adjusting short order to {long_actual_filled}"
                )
                short_order_qty = long_actual_filled
            else:
                short_order_qty = order_qty

            short_fill = await self._place_with_timeout(
                short_adapter,
                OrderRequest(
                    exchange=opp.short_exchange,
                    symbol=opp.symbol,
                    side=OrderSide.SELL,
                    quantity=short_order_qty,
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
            entry_price_long = self._extract_avg_price(long_fill)
            entry_price_short = self._extract_avg_price(short_fill)
            entry_fees = self._extract_fee(long_fill) + self._extract_fee(short_fill)

            # Log any partial fills and mismatches
            short_partial = short_filled_qty < short_order_qty
            qty_mismatch = long_filled_qty != short_filled_qty
            
            if is_partial_fill or short_partial or qty_mismatch:
                logger.warning(
                    f"📊 [{opp.symbol}] Fill Report: "
                    f"Long={long_filled_qty}/{order_qty} "
                    f"| Short={short_filled_qty}/{short_order_qty} "
                    f"| Mismatch={qty_mismatch} | Fees=${float(entry_fees):.2f}"
                )
                if qty_mismatch:
                    logger.warning(
                        f"🔴 QUANTITY MISMATCH: L={long_filled_qty} != S={short_filled_qty} — "
                        f"Unhedged exposure of {abs(long_filled_qty - short_filled_qty)}!"
                    )

            trade = TradeRecord(
                trade_id=trade_id,
                symbol=opp.symbol,
                state=TradeState.OPEN,
                long_exchange=opp.long_exchange,
                short_exchange=opp.short_exchange,
                long_qty=long_filled_qty,
                short_qty=short_filled_qty,
                entry_edge_pct=opp.net_edge_pct,
                long_funding_rate=opp.long_funding_rate,
                short_funding_rate=opp.short_funding_rate,
                entry_price_long=entry_price_long,
                entry_price_short=entry_price_short,
                fees_paid_total=entry_fees,
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
                f"spread={opp.funding_spread_pct:.4f}% net={opp.net_edge_pct:.4f}%{mode_str}",
                extra={
                    "trade_id": trade_id,
                    "symbol": opp.symbol,
                    "action": "trade_opened",
                },
            )

            immediate_spread = (
                (-opp.long_funding_rate) + opp.short_funding_rate
            ) * Decimal("100")
            entry_msg = (
                f"ENTRY {trade_id} {opp.symbol} | "
                f"BUY {opp.long_exchange} {long_filled_qty} @ {entry_price_long} | "
                f"SELL {opp.short_exchange} {short_filled_qty} @ {entry_price_short} | "
                f"Fees={entry_fees} | "
                f"Spread={immediate_spread:.4f}% (immediate), Net={opp.net_edge_pct:.4f}%"
            )
            logger.info(entry_msg, extra={"trade_id": trade_id, "symbol": opp.symbol, "action": "trade_entry"})
            if self._publisher:
                await self._publisher.publish_log("INFO", entry_msg)
            
            # Log balances after trade opened (if enabled)
            if hasattr(self._cfg.logging, 'log_balances_after_trade') and self._cfg.logging.log_balances_after_trade:
                await self._log_exchange_balances()
        except Exception as e:
            err_str = str(e).lower()
            # Detect exchange-level delisting / restricted errors
            if any(kw in err_str for kw in [
                "delisting", "delist", "30228",     # Bybit delisting
                "symbol is not available",            # Binance
                "contract is being settled",           # OKX
                "reduce-only", "reduce only",         # generic restrict
            ]):
                self._add_to_blacklist(opp.symbol, opp.long_exchange)
                self._add_to_blacklist(opp.symbol, opp.short_exchange)
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

        # ── Display current spread & time until next payment ──────
        long_interval = long_funding.get("interval_hours", 8)
        short_interval = short_funding.get("interval_hours", 8)
        spread_info = calculate_funding_spread(
            long_funding["rate"], short_funding["rate"],
            long_interval_hours=long_interval,
            short_interval_hours=short_interval,
        )
        current_spread = spread_info["funding_spread_pct"]
        
        long_until = None
        short_until = None
        if trade.next_funding_long:
            long_until = int((trade.next_funding_long - now).total_seconds() / 60)
        if trade.next_funding_short:
            short_until = int((trade.next_funding_short - now).total_seconds() / 60)
        
        long_str = f"{long_until}min" if long_until is not None else "?"
        short_str = f"{short_until}min" if short_until is not None else "?"
        
        logger.info(
            f"🔔 {trade.symbol}: Current Spread = {float(current_spread):.4f}% | "
            f"{trade.long_exchange} in {long_str} | {trade.short_exchange} in {short_str}",
            extra={"trade_id": trade.trade_id, "symbol": trade.symbol, "action": "spread_update"},
        )

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

        # Check if still profitable to hold (funding spread)
        long_spec = await long_adapter.get_instrument_spec(trade.symbol)
        short_spec = await short_adapter.get_instrument_spec(trade.symbol)
        if not long_spec or not short_spec:
            return

        fees_pct = calculate_fees(long_spec.taker_fee, short_spec.taker_fee)
        net = spread_info["funding_spread_pct"] - fees_pct

        if net <= 0 or net < trade.entry_edge_pct * Decimal("0.1"):
            logger.info(
                f"Exit signal for {trade.trade_id}: net={net:.4f}% — closing",
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
            # How long have we been holding?
            hold_min = 0
            if trade.opened_at:
                hold_min = int((now - trade.opened_at).total_seconds() / 60)
            logger.info(
                f"Trade {trade.trade_id}: ✅ HOLDING — still profitable! "
                f"net={net:.4f}% (entry was {trade.entry_edge_pct:.4f}%) | "
                f"holding for {hold_min}min | "
                f"Next payment: {trade.long_exchange}={trade.next_funding_long.strftime('%H:%M') if trade.next_funding_long else '?'}, "
                f"{trade.short_exchange}={trade.next_funding_short.strftime('%H:%M') if trade.next_funding_short else '?'}"
            )

    # ── Close trade ──────────────────────────────────────────────

    async def _close_trade(self, trade: TradeRecord) -> None:
        trade.state = TradeState.CLOSING
        await self._persist_trade(trade)

        long_adapter = self._exchanges.get(trade.long_exchange)
        short_adapter = self._exchanges.get(trade.short_exchange)

        long_fill = await self._close_leg(
            long_adapter, trade.long_exchange, trade.symbol,
            OrderSide.SELL, trade.long_qty, trade.trade_id,
        )
        short_fill = await self._close_leg(
            short_adapter, trade.short_exchange, trade.symbol,
            OrderSide.BUY, trade.short_qty, trade.trade_id,
        )

        if long_fill and short_fill:
            trade.state = TradeState.CLOSED
            trade.closed_at = datetime.now(timezone.utc)
            trade.exit_price_long = self._extract_avg_price(long_fill)
            trade.exit_price_short = self._extract_avg_price(short_fill)
            close_fees = self._extract_fee(long_fill) + self._extract_fee(short_fill)
            total_fees = (trade.fees_paid_total or Decimal("0")) + close_fees
            trade.fees_paid_total = total_fees
            if trade.funding_paid_total is None and trade.funding_received_total is None:
                paid, received = self._estimate_funding_totals(trade)
                trade.funding_paid_total = paid
                trade.funding_received_total = received
            await self._redis.delete_trade_state(trade.trade_id)
            del self._active_trades[trade.trade_id]

            # ── Detailed trade summary ────────────────────────
            entry_notional_long = (trade.entry_price_long or Decimal("0")) * trade.long_qty
            entry_notional_short = (trade.entry_price_short or Decimal("0")) * trade.short_qty
            exit_notional_long = (trade.exit_price_long or Decimal("0")) * trade.long_qty
            exit_notional_short = (trade.exit_price_short or Decimal("0")) * trade.short_qty
            # Long PnL: exit - entry (bought low, sold high)
            long_pnl = exit_notional_long - entry_notional_long
            # Short PnL: entry - exit (sold high, bought low)
            short_pnl = entry_notional_short - exit_notional_short
            price_pnl = long_pnl + short_pnl
            funding_income = trade.funding_received_total or Decimal("0")
            funding_cost = trade.funding_paid_total or Decimal("0")
            funding_net = funding_income - funding_cost
            total_pnl = price_pnl + funding_net - total_fees
            invested = max(entry_notional_long, entry_notional_short)
            hold_minutes = Decimal("0")
            if trade.opened_at and trade.closed_at:
                hold_minutes = Decimal(str((trade.closed_at - trade.opened_at).total_seconds() / 60))

            logger.info(
                f"\n{'='*60}\n"
                f"  📊 TRADE SUMMARY — {trade.trade_id}\n"
                f"  Symbol:     {trade.symbol}\n"
                f"  Mode:       {trade.mode}\n"
                f"  Duration:   {float(hold_minutes):.0f} min\n"
                f"  Long:       {trade.long_exchange} qty={trade.long_qty} "
                f"entry=${float(trade.entry_price_long or 0):.4f} exit=${float(trade.exit_price_long or 0):.4f}\n"
                f"  Short:      {trade.short_exchange} qty={trade.short_qty} "
                f"entry=${float(trade.entry_price_short or 0):.4f} exit=${float(trade.exit_price_short or 0):.4f}\n"
                f"  Invested:   ${float(invested):.2f} (notional per leg)\n"
                f"  Price PnL:  ${float(price_pnl):.4f}\n"
                f"  Funding:    +${float(funding_income):.4f} -${float(funding_cost):.4f} = ${float(funding_net):.4f}\n"
                f"  Fees:       ${float(total_fees):.4f}\n"
                f"  ────────────────────────────────\n"
                f"  NET PROFIT: ${float(total_pnl):.4f}\n"
                f"{'='*60}",
                extra={
                    "trade_id": trade.trade_id,
                    "action": "trade_closed",
                    "data": {
                        "symbol": trade.symbol,
                        "invested": float(invested),
                        "price_pnl": float(price_pnl),
                        "funding_net": float(funding_net),
                        "fees": float(total_fees),
                        "net_profit": float(total_pnl),
                        "hold_minutes": float(hold_minutes),
                    }
                },
            )

            # ── Publish PnL data point to Redis for frontend chart ──
            try:
                import json as _json
                pnl_value = float(total_pnl)
                ts = datetime.utcnow().timestamp()
                await self._redis._client.zadd(
                    "trinity:pnl:timeseries",
                    {str(pnl_value): ts},
                )
            except Exception as pnl_err:
                logger.debug(f"Failed to publish PnL data: {pnl_err}")

            trade_data = {
                "id": trade.trade_id,
                "symbol": trade.symbol,
                "long_exchange": trade.long_exchange,
                "short_exchange": trade.short_exchange,
                "long_qty": str(trade.long_qty),
                "short_qty": str(trade.short_qty),
                "entry_price_long": str(trade.entry_price_long) if trade.entry_price_long is not None else None,
                "entry_price_short": str(trade.entry_price_short) if trade.entry_price_short is not None else None,
                "exit_price_long": str(trade.exit_price_long) if trade.exit_price_long is not None else None,
                "exit_price_short": str(trade.exit_price_short) if trade.exit_price_short is not None else None,
                "fees_paid_total": str(trade.fees_paid_total) if trade.fees_paid_total is not None else None,
                "funding_received_total": str(trade.funding_received_total) if trade.funding_received_total is not None else None,
                "funding_paid_total": str(trade.funding_paid_total) if trade.funding_paid_total is not None else None,
                "long_funding_rate": str(trade.long_funding_rate) if trade.long_funding_rate is not None else None,
                "short_funding_rate": str(trade.short_funding_rate) if trade.short_funding_rate is not None else None,
                "opened_at": trade.opened_at.isoformat() if trade.opened_at else None,
                "closed_at": trade.closed_at.isoformat() if trade.closed_at else None,
                "status": trade.state.value,
                "total_pnl": float(total_pnl),
                "price_pnl": float(price_pnl),
                "funding_net": float(funding_net),
                "invested": float(invested),
                "hold_minutes": float(hold_minutes),
            }
            await self._redis.zadd(
                "trinity:trades:history",
                {json.dumps(trade_data): datetime.utcnow().timestamp()},
            )
            
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
    ) -> Optional[dict]:
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
                    return result
            except Exception as e:
                logger.warning(
                    f"Close attempt {attempt+1}/3 failed {exchange}/{symbol}: {e}",
                    extra={"trade_id": trade_id, "exchange": exchange},
                )
                await asyncio.sleep(1)
        return None

    @staticmethod
    def _extract_avg_price(order: dict) -> Optional[Decimal]:
        for key in ("average", "avg_price", "price", "avgPrice"):
            val = order.get(key)
            if val is not None:
                try:
                    return Decimal(str(val))
                except Exception:
                    continue
        return None

    @staticmethod
    def _extract_fee(order: dict) -> Decimal:
        total = Decimal("0")
        fee = order.get("fee")
        if isinstance(fee, dict) and fee.get("cost") is not None:
            try:
                total += Decimal(str(fee.get("cost")))
            except Exception:
                pass
        fees = order.get("fees")
        if isinstance(fees, list):
            for f in fees:
                if isinstance(f, dict) and f.get("cost") is not None:
                    try:
                        total += Decimal(str(f.get("cost")))
                    except Exception:
                        continue
        return total

    @staticmethod
    def _estimate_funding_totals(trade: TradeRecord) -> tuple[Decimal, Decimal]:
        """Estimate funding paid/received from entry rates and notional.

        Note: this is an estimate, not the actual exchange credit.
        """
        if not trade.entry_price_long or not trade.entry_price_short:
            return Decimal("0"), Decimal("0")
        long_rate = trade.long_funding_rate or Decimal("0")
        short_rate = trade.short_funding_rate or Decimal("0")
        notional_long = trade.entry_price_long * trade.long_qty
        notional_short = trade.entry_price_short * trade.short_qty

        paid = Decimal("0")
        received = Decimal("0")

        if long_rate >= 0:
            paid += notional_long * long_rate
        else:
            received += notional_long * abs(long_rate)

        if short_rate >= 0:
            received += notional_short * short_rate
        else:
            paid += notional_short * abs(short_rate)

        return paid, received

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
        except Exception as e:
            err_str = str(e).lower()
            # Detect delisting / restricted errors and blacklist
            if any(kw in err_str for kw in [
                "delisting", "delist", "30228",
                "symbol is not available",
                "contract is being settled",
                "reduce-only", "reduce only",
            ]):
                self._add_to_blacklist(req.symbol, req.exchange)
                logger.warning(
                    f"Blacklisted {req.symbol} on {req.exchange} (delisting/restricted): {e}",
                    extra={"exchange": req.exchange, "symbol": req.symbol, "action": "blacklisted"},
                )
            else:
                logger.error(
                    f"Order failed on {req.exchange}/{req.symbol}: {e}",
                    extra={"exchange": req.exchange, "symbol": req.symbol},
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
            "entry_edge_pct": str(trade.entry_edge_pct),
            "long_funding_rate": str(trade.long_funding_rate) if trade.long_funding_rate is not None else None,
            "short_funding_rate": str(trade.short_funding_rate) if trade.short_funding_rate is not None else None,
            "entry_price_long": str(trade.entry_price_long) if trade.entry_price_long is not None else None,
            "entry_price_short": str(trade.entry_price_short) if trade.entry_price_short is not None else None,
            "fees_paid_total": str(trade.fees_paid_total) if trade.fees_paid_total is not None else None,
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
                entry_edge_pct=Decimal(data.get("entry_edge_pct", data.get("entry_edge_bps", "0"))),
                long_funding_rate=Decimal(data["long_funding_rate"]) if data.get("long_funding_rate") else None,
                short_funding_rate=Decimal(data["short_funding_rate"]) if data.get("short_funding_rate") else None,
                entry_price_long=Decimal(data["entry_price_long"]) if data.get("entry_price_long") else None,
                entry_price_short=Decimal(data["entry_price_short"]) if data.get("entry_price_short") else None,
                fees_paid_total=Decimal(data["fees_paid_total"]) if data.get("fees_paid_total") else None,
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
