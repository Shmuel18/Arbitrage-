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
    Position,
    TradeRecord,
    TradeState,
)
from src.core.logging import get_logger
from src.core.journal import get_journal
from src.discovery.calculator import calculate_fees, calculate_funding_spread

if TYPE_CHECKING:
    from src.core.config import Config
    from src.exchanges.adapter import ExchangeManager
    from src.storage.redis_client import RedisClient
    from src.risk.guard import RiskGuard
    from src.api.publisher import APIPublisher

logger = get_logger("execution")

_ORDER_TIMEOUT_SEC = 10


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
        # Track consecutive order-timeout failures: "symbol:exchange" -> count
        self._timeout_streak: Dict[str, int] = {}
        # In-memory guard: symbols currently mid-entry (prevents same-symbol retry within same scan batch)
        self._symbols_entering: set[str] = set()
        # Upgrade cooldown: symbol -> expiry timestamp (prevents re-entry after upgrade exit)
        self._upgrade_cooldown: Dict[str, float] = {}
        # Trade journal for persistent audit trail
        self._journal = get_journal()

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
        _t0_mono = _time.monotonic()  # execution latency tracking
        logger.info(
            f"🔍 [{opp.symbol}] Evaluating opportunity: mode={opp.mode} "
            f"spread={opp.funding_spread_pct:.4f}% net={opp.net_edge_pct:.4f}% "
            f"L={opp.long_exchange} S={opp.short_exchange}"
        )

        # Blacklist guard — skip symbols/exchanges flagged as delisting etc.
        if self._is_blacklisted(opp.symbol, opp.long_exchange, opp.short_exchange):
            return

        # Cooldown guard — skip symbols recently failed (orphan / timeout)
        if await self._redis.is_cooled_down(opp.symbol):
            logger.info(f"❄️ Skipping {opp.symbol}: symbol is in cooldown")
            return

        # Upgrade cooldown guard — prevent rapid re-entry after upgrade exit
        upgrade_expiry = self._upgrade_cooldown.get(opp.symbol)
        if upgrade_expiry is not None:
            if _time.time() < upgrade_expiry:
                remaining = int(upgrade_expiry - _time.time())
                logger.info(
                    f"⬆️ Skipping {opp.symbol}: upgrade cooldown active ({remaining}s left)"
                )
                return
            else:
                del self._upgrade_cooldown[opp.symbol]

        # In-memory entry lock — prevent same-symbol retry within same scan batch
        if opp.symbol in self._symbols_entering:
            logger.info(f"🔒 Skipping {opp.symbol}: entry already in progress")
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

        # Exchange-in-use guard — each exchange can only be in ONE trade at a time
        busy_exchanges: set[str] = set()
        for t in self._active_trades.values():
            busy_exchanges.add(t.long_exchange)
            busy_exchanges.add(t.short_exchange)
        for ex in (opp.long_exchange, opp.short_exchange):
            if ex in busy_exchanges:
                logger.info(
                    f"🔒 Skipping {opp.symbol}: {ex} already in use by another trade"
                )
                return

        # ── Funding spread gate (safety check) ──
        # For HOLD mode: raw funding_spread_pct must meet threshold
        # For CHERRY_PICK: gross_edge_pct (total collections) must meet threshold
        tp = self._cfg.trading_params
        if opp.mode == "cherry_pick":
            if opp.immediate_net_pct < tp.min_funding_spread:
                logger.info(
                    f"📉 Skipping {opp.symbol}: cherry-pick net {opp.immediate_net_pct:.4f}% "
                    f"< min_funding_spread {tp.min_funding_spread}% (gross={opp.gross_edge_pct:.4f}%)"
                )
                return
        else:
            if opp.immediate_net_pct < tp.min_funding_spread:
                logger.info(
                    f"📉 Skipping {opp.symbol}: net {opp.immediate_net_pct:.4f}% "
                    f"< min_funding_spread {tp.min_funding_spread}% (gross={opp.gross_edge_pct:.4f}%)"
                )
                return

        long_adapter = self._exchanges.get(opp.long_exchange)
        short_adapter = self._exchanges.get(opp.short_exchange)

        # ── Entry timing gate: PRIMARY CONTRIBUTOR must be within 15 min ──
        # Use next_funding_ms from scanner (no REST call needed)
        entry_offset = self._cfg.trading_params.entry_offset_seconds
        now_ms = _time.time() * 1000

        # Determine primary contributor from rates already in opportunity (no REST call)
        long_rate = opp.long_funding_rate
        short_rate = opp.short_funding_rate
        long_contribution = abs(long_rate) if long_rate < 0 else Decimal("0")
        short_contribution = abs(short_rate) if short_rate > 0 else Decimal("0")
        
        if long_contribution > short_contribution:
            primary_side = "long"
            primary_exchange = opp.long_exchange
            primary_contribution = long_contribution
        else:
            primary_side = "short"
            primary_exchange = opp.short_exchange
            primary_contribution = short_contribution

        # Use next_funding_ms from scanner
        primary_next_ms = opp.next_funding_ms
        if primary_next_ms is None:
            logger.info(
                f"⏳ Skipping {opp.symbol}: no funding timestamp available from scanner"
            )
            return
        else:
            seconds_until = (primary_next_ms - now_ms) / 1000
            if not (0 < seconds_until <= entry_offset):
                logger.info(
                    f"⏳ Skipping {opp.symbol}: primary contributor ({primary_side} {primary_exchange}, "
                    f"contributes {float(primary_contribution)*100:.4f}%) not in entry window. "
                    f"Next funding in {int(seconds_until/60)}min. Entry allowed ≤{entry_offset}s before payment."
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
                self._journal.basis_rejection(
                    opp.symbol, opp.long_exchange, opp.short_exchange,
                    basis_loss=basis_loss_pct, net_edge=opp.net_edge_pct,
                    long_ask=long_ask, short_bid=short_bid,
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
        self._symbols_entering.add(opp.symbol)
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

            # Pre-apply trading settings on BOTH exchanges OUTSIDE the order timeout.
            # ensure_trading_settings (margin mode, leverage, position mode) can take
            # 6-8s on slow exchanges (kucoin). Doing it inside _place_with_timeout
            # ate most of the 10s order timeout, leaving <2s for the actual order.
            await long_adapter.ensure_trading_settings(opp.symbol)
            await short_adapter.ensure_trading_settings(opp.symbol)

            # Mark grace period BEFORE placing first order
            if self._risk_guard:
                self._risk_guard.mark_trade_opened(opp.symbol)
                logger.info(f"✅ Grace period activated for {opp.symbol} (30s delta skip)")
            
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

            # Update cached taker_fee from actual fill (real account rate)
            long_adapter.update_taker_fee_from_fill(opp.symbol, long_fill)

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
                    OrderSide.SELL, long_fill, order_qty,
                )
                return

            # Update cached taker_fee from actual fill (real account rate)
            short_adapter.update_taker_fee_from_fill(opp.symbol, short_fill)

            short_actual_filled = Decimal(str(short_fill.get("filled", 0) or short_order_qty))
            
            logger.info(
                f"🔓 Trade FULLY OPEN {opp.symbol}: "
                f"LONG({opp.long_exchange})={long_actual_filled} | "
                f"SHORT({opp.short_exchange})={short_actual_filled} — "
                f"Expecting delta=0 in next position fetch"
            )            # Record trade with ACTUAL filled quantities (fallback to order_qty, not raw suggested_qty)
            long_filled_qty = Decimal(str(long_fill.get("filled", 0) or order_qty))
            short_filled_qty = Decimal(str(short_fill.get("filled", 0) or order_qty))
            entry_price_long = self._extract_avg_price(long_fill)
            entry_price_short = self._extract_avg_price(short_fill)

            # ── Fallback: if exchange didn't return avg price, use ticker ──
            if entry_price_long is None:
                try:
                    t = await long_adapter.get_ticker(opp.symbol)
                    entry_price_long = Decimal(str(t.get("last", 0)))
                    logger.info(f"[{opp.symbol}] Long entry price from ticker: {entry_price_long}")
                except Exception:
                    entry_price_long = opp.reference_price  # last resort
            if entry_price_short is None:
                try:
                    t = await short_adapter.get_ticker(opp.symbol)
                    entry_price_short = Decimal(str(t.get("last", 0)))
                    logger.info(f"[{opp.symbol}] Short entry price from ticker: {entry_price_short}")
                except Exception:
                    entry_price_short = opp.reference_price  # last resort

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

            # ── Delta correction: fix unhedged exposure from short partial fill ──
            if qty_mismatch and long_filled_qty > short_filled_qty:
                excess = long_filled_qty - short_filled_qty
                logger.warning(
                    f"🔴 DELTA CORRECTION: L={long_filled_qty} > S={short_filled_qty} — "
                    f"trimming {excess} on {opp.long_exchange} (reduceOnly)"
                )
                try:
                    trim_req = OrderRequest(
                        exchange=opp.long_exchange,
                        symbol=opp.symbol,
                        side=OrderSide.SELL,
                        quantity=excess,
                        reduce_only=True,
                    )
                    trim_fill = await self._place_with_timeout(long_adapter, trim_req)
                    if trim_fill:
                        trimmed = Decimal(str(trim_fill.get("filled", 0) or excess))
                        long_filled_qty -= trimmed
                        trim_fee = self._extract_fee(trim_fill)
                        entry_fees += trim_fee
                        logger.info(
                            f"✅ Delta corrected: trimmed {trimmed} on {opp.long_exchange}, "
                            f"L={long_filled_qty} S={short_filled_qty} now balanced"
                        )
                    else:
                        logger.error(
                            f"❌ DELTA CORRECTION FAILED for {opp.symbol} — "
                            f"unhedged {excess} on {opp.long_exchange}! MANUAL CHECK REQUIRED"
                        )
                except Exception as e:
                    logger.error(
                        f"❌ DELTA CORRECTION ERROR for {opp.symbol}: {e} — "
                        f"unhedged {excess} on {opp.long_exchange}! MANUAL CHECK REQUIRED"
                    )
            elif qty_mismatch and short_filled_qty > long_filled_qty:
                excess = short_filled_qty - long_filled_qty
                logger.warning(
                    f"🔴 DELTA CORRECTION: S={short_filled_qty} > L={long_filled_qty} — "
                    f"trimming {excess} on {opp.short_exchange} (reduceOnly)"
                )
                try:
                    trim_req = OrderRequest(
                        exchange=opp.short_exchange,
                        symbol=opp.symbol,
                        side=OrderSide.BUY,
                        quantity=excess,
                        reduce_only=True,
                    )
                    trim_fill = await self._place_with_timeout(short_adapter, trim_req)
                    if trim_fill:
                        trimmed = Decimal(str(trim_fill.get("filled", 0) or excess))
                        short_filled_qty -= trimmed
                        trim_fee = self._extract_fee(trim_fill)
                        entry_fees += trim_fee
                        logger.info(
                            f"✅ Delta corrected: trimmed {trimmed} on {opp.short_exchange}, "
                            f"L={long_filled_qty} S={short_filled_qty} now balanced"
                        )
                    else:
                        logger.error(
                            f"❌ DELTA CORRECTION FAILED for {opp.symbol} — "
                            f"unhedged {excess} on {opp.short_exchange}! MANUAL CHECK REQUIRED"
                        )
                except Exception as e:
                    logger.error(
                        f"❌ DELTA CORRECTION ERROR for {opp.symbol}: {e} — "
                        f"unhedged {excess} on {opp.short_exchange}! MANUAL CHECK REQUIRED"
                    )

            # If after correction both legs are zero, abort trade
            if long_filled_qty <= 0 or short_filled_qty <= 0:
                logger.error(
                    f"❌ [{opp.symbol}] No viable position after fills — aborting trade"
                )
                return

            trade = TradeRecord(
                trade_id=trade_id,
                symbol=opp.symbol,
                state=TradeState.OPEN,
                long_exchange=opp.long_exchange,
                short_exchange=opp.short_exchange,
                long_qty=long_filled_qty,
                short_qty=short_filled_qty,
                entry_edge_pct=opp.immediate_net_pct,
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

            # ── Build clear ENTRY REASON ──
            lr_pct = float(opp.long_funding_rate) * 100
            sr_pct = float(opp.short_funding_rate) * 100
            # Income: long side earns when rate < 0 (shorts pay longs),
            #         short side earns when rate > 0 (longs pay shorts)
            income_parts = []
            cost_parts = []
            if opp.long_funding_rate < 0:
                income_parts.append(f"{opp.long_exchange}(long) receives {abs(lr_pct):.4f}%")
            else:
                cost_parts.append(f"{opp.long_exchange}(long) pays {lr_pct:.4f}%")
            if opp.short_funding_rate > 0:
                income_parts.append(f"{opp.short_exchange}(short) receives {sr_pct:.4f}%")
            else:
                cost_parts.append(f"{opp.short_exchange}(short) pays {abs(sr_pct):.4f}%")
            income_str = ", ".join(income_parts) if income_parts else "none"
            cost_str = ", ".join(cost_parts) if cost_parts else "none"

            entry_reason = (
                f"{opp.mode.upper()}: spread={float(immediate_spread):.4f}% net={float(opp.net_edge_pct):.4f}% | "
                f"Income: {income_str} | Cost: {cost_str}"
            )
            if opp.mode == "cherry_pick":
                entry_reason += f" | collections={opp.n_collections}"
                if opp.exit_before:
                    entry_reason += f" exit_before={opp.exit_before.strftime('%H:%M UTC')}"

            entry_notional = float(entry_price_long * long_filled_qty) if entry_price_long else 0

            # ── Execution latency ──
            _exec_latency_ms = int((_time.monotonic() - _t0_mono) * 1000)

            entry_msg = (
                f"\n{'='*60}\n"
                f"  🟢 TRADE ENTRY — {trade_id}\n"
                f"  Symbol:    {opp.symbol}\n"
                f"  Mode:      {opp.mode}\n"
                f"  Reason:    {entry_reason}\n"
                f"  LONG:      {opp.long_exchange} qty={long_filled_qty} @ ${float(entry_price_long or 0):.6f} "
                    f"| funding={lr_pct:+.4f}%\n"
                f"  SHORT:     {opp.short_exchange} qty={short_filled_qty} @ ${float(entry_price_short or 0):.6f} "
                    f"| funding={sr_pct:+.4f}%\n"
                f"  Notional:  ${entry_notional:.2f} per leg\n"
                f"  Spread:    {float(immediate_spread):.4f}% (immediate)\n"
                f"  Net edge:  {float(opp.net_edge_pct):.4f}% (after fees)\n"
                f"  Fees:      ${float(entry_fees):.4f}\n"
                f"  Latency:   {_exec_latency_ms}ms (discovery → filled)\n"
                f"{'='*60}"
            )
            logger.info(entry_msg, extra={"trade_id": trade_id, "symbol": opp.symbol, "action": "trade_entry"})
            if self._publisher:
                await self._publisher.publish_log("INFO", entry_msg)

            # ── Journal: record trade open ──
            self._journal.trade_opened(
                trade_id=trade_id, symbol=opp.symbol, mode=opp.mode,
                long_exchange=opp.long_exchange, short_exchange=opp.short_exchange,
                long_qty=long_filled_qty, short_qty=short_filled_qty,
                entry_price_long=entry_price_long, entry_price_short=entry_price_short,
                long_funding_rate=opp.long_funding_rate, short_funding_rate=opp.short_funding_rate,
                spread_pct=opp.funding_spread_pct, net_pct=opp.net_edge_pct,
                exit_before=opp.exit_before, n_collections=opp.n_collections,
                notional=entry_notional,
                entry_reason=entry_reason,
                exec_latency_ms=_exec_latency_ms,
            )

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
            self._symbols_entering.discard(opp.symbol)
            await self._redis.release_lock(lock_key)

    # ── Exit monitor ─────────────────────────────────────────────

    async def _exit_monitor_loop(self) -> None:
        reconcile_counter = 0
        balance_snapshot_counter = 0  # snapshot every 60 cycles (30min)
        while self._running:
            try:
                # ── Position reconciliation every ~2 min (4 × 30s) ──
                reconcile_counter += 1
                if reconcile_counter >= 4:
                    reconcile_counter = 0
                    await self._reconcile_positions()

                # ── Balance snapshot every ~30 min (60 × 30s) ──
                balance_snapshot_counter += 1
                if balance_snapshot_counter >= 60:
                    balance_snapshot_counter = 0
                    await self._journal_balance_snapshot()

                for trade_id in list(self._active_trades):
                    trade = self._active_trades.get(trade_id)
                    if not trade or trade.state != TradeState.OPEN:
                        continue
                    # Check for upgrade BEFORE normal exit check
                    upgraded = await self._check_upgrade(trade)
                    if upgraded:
                        continue  # trade was closed, skip exit check
                    await self._check_exit(trade)
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.error(f"Exit monitor error: {e}")
            await asyncio.sleep(30)

    async def _check_upgrade(self, trade: TradeRecord) -> bool:
        """Check if a significantly better opportunity exists.

        Reads qualified opportunities from Redis. If one has
        immediate_spread >= current_spread + upgrade_spread_delta
        AND is in the 15-min entry window → close current trade
        so the scanner can pick up the better one on next cycle.

        Returns True if the trade was closed for upgrade.
        """
        upgrade_delta = getattr(
            self._cfg.trading_params, 'upgrade_spread_delta', Decimal("0.5")
        )
        if upgrade_delta <= 0:
            return False

        # Get current trade's spread from cache (no REST call)
        long_adapter = self._exchanges.get(trade.long_exchange)
        short_adapter = self._exchanges.get(trade.short_exchange)
        try:
            long_funding = long_adapter.get_funding_rate_cached(trade.symbol)
            short_funding = short_adapter.get_funding_rate_cached(trade.symbol)
            if not long_funding or not short_funding:
                return False
        except Exception:
            return False

        long_interval = long_funding.get("interval_hours", 8)
        short_interval = short_funding.get("interval_hours", 8)
        spread_info = calculate_funding_spread(
            long_funding["rate"], short_funding["rate"],
            long_interval_hours=long_interval,
            short_interval_hours=short_interval,
        )
        current_spread = spread_info["funding_spread_pct"]
        current_immediate = spread_info["immediate_spread_pct"]

        # Read latest opportunities from Redis
        try:
            raw = await self._redis.get("trinity:opportunities")
            if not raw:
                return False
            data = json.loads(raw)
            candidates = data.get("opportunities", [])
        except Exception as e:
            logger.debug(f"Upgrade check: cannot read opportunities: {e}")
            return False

        entry_offset = self._cfg.trading_params.entry_offset_seconds
        now_ms = _time.time() * 1000
        threshold_cross = current_spread + upgrade_delta       # cross-symbol (8h-norm)
        threshold_same = current_immediate + upgrade_delta     # same-symbol pair switch

        for cand in candidates:
            if not cand.get("qualified", False):
                continue

            cand_symbol = cand.get("symbol", "")
            cand_long = cand.get("long_exchange", "")
            cand_short = cand.get("short_exchange", "")
            cand_spread = Decimal(str(cand.get("immediate_spread_pct", 0)))
            same_symbol = cand_symbol == trade.symbol

            if same_symbol:
                # Same symbol — only upgrade if the exchange pair is DIFFERENT
                if cand_long == trade.long_exchange and cand_short == trade.short_exchange:
                    continue
                # Ensure candidate's exchanges aren't busy with OTHER trades
                other_busy: set[str] = set()
                for t in self._active_trades.values():
                    if t.trade_id != trade.trade_id:
                        other_busy.add(t.long_exchange)
                        other_busy.add(t.short_exchange)
                if cand_long in other_busy or cand_short in other_busy:
                    continue
                # Compare immediate spreads (same symbol = same time horizon)
                if cand_spread < threshold_same:
                    continue
            else:
                if cand_spread < threshold_cross:
                    continue

            # Must be in the entry window
            next_ms = cand.get("next_funding_ms")
            if next_ms is None:
                continue
            seconds_until = (next_ms - now_ms) / 1000
            if not (0 < seconds_until <= entry_offset):
                continue

            # Found a significantly better opportunity — upgrade!
            hold_min = 0
            if trade.opened_at:
                hold_min = int(
                    (datetime.now(timezone.utc) - trade.opened_at).total_seconds() / 60
                )
            upgrade_type = "PAIR SWITCH" if same_symbol else "UPGRADE"
            cur_display = float(current_immediate) if same_symbol else float(current_spread)
            logger.info(
                f"⬆️  {upgrade_type}: closing {trade.symbol} on "
                f"{trade.long_exchange}↔{trade.short_exchange} (spread {cur_display:.4f}%) "
                f"→ {cand_symbol} on {cand_long}↔{cand_short} (spread {float(cand_spread):.4f}%) — "
                f"delta {float(cand_spread - (current_immediate if same_symbol else current_spread)):.4f}% "
                f"≥ {float(upgrade_delta):.2f}% (held {hold_min}min)",
                extra={
                    "trade_id": trade.trade_id,
                    "symbol": trade.symbol,
                    "action": "upgrade_exit",
                    "upgrade_to": cand_symbol,
                    "upgrade_pair": f"{cand_long}_{cand_short}",
                },
            )
            # Re-arm grace period BEFORE closing to prevent risk guard
            # from seeing transient unhedged positions during the switch
            if self._risk_guard:
                self._risk_guard.mark_trade_opened(trade.symbol)
                if cand_symbol != trade.symbol:
                    self._risk_guard.mark_trade_opened(cand_symbol)
                logger.info(f"✅ Grace period re-armed for {upgrade_type} on {trade.symbol}")
            await self._close_trade(trade)
            # Set upgrade cooldown so the closed symbol doesn't immediately re-enter
            cooldown_sec = getattr(
                self._cfg.trading_params, 'upgrade_cooldown_seconds', 300
            )
            self._upgrade_cooldown[trade.symbol] = _time.time() + cooldown_sec
            logger.info(
                f"⬆️ Upgrade cooldown set for {trade.symbol}: {cooldown_sec}s",
                extra={"symbol": trade.symbol, "action": "upgrade_cooldown_set"},
            )
            return True

        return False

    async def _check_exit(self, trade: TradeRecord) -> None:
        """Check if trade should be closed.

        Two modes:
          CHERRY_PICK: exit BEFORE the costly funding payment
          HOLD:        exit when edge reverses (both sides still income)
        """
        now = datetime.now(timezone.utc)

        # ── CHERRY_PICK: hard stop before costly payment ─────────
        if trade.mode == "cherry_pick" and trade.exit_before:
            if now >= trade.exit_before:
                logger.info(
                    f"Cherry-pick hard exit for {trade.trade_id}: "
                    f"exiting before costly payment at {trade.exit_before.strftime('%H:%M UTC')}",
                    extra={"trade_id": trade.trade_id, "symbol": trade.symbol, "action": "exit_signal"},
                )
                await self._close_trade(trade)
                return
            # Don't return — fall through to spread check below (same as HOLD)

        # ── HOLD: use cached rates (no REST call) ─────────────────
        long_adapter = self._exchanges.get(trade.long_exchange)
        short_adapter = self._exchanges.get(trade.short_exchange)

        long_funding = long_adapter.get_funding_rate_cached(trade.symbol)
        short_funding = short_adapter.get_funding_rate_cached(trade.symbol)
        if not long_funding or not short_funding:
            logger.debug(f"No cached funding for {trade.symbol} — skipping exit check")
            return

        # Track next funding time per exchange (update when stale)
        # _funding_paid_* flags indicate we already collected this cycle's payment
        # and are in continuous hold-or-exit monitoring. Don't advance trackers
        # until we explicitly decide to HOLD for the next cycle.
        #
        # IMPORTANT: When old tracker value < now (funding time has passed),
        # only update if the new candidate is ALSO in the past (stale correction).
        # If candidate is in the future, the funding was just PAID — don't advance
        # yet, so the exit_offset check below can fire and trigger hold/exit.
        long_next_ts = long_funding.get("next_timestamp")
        if long_next_ts:
            candidate_long = datetime.fromtimestamp(long_next_ts / 1000, tz=timezone.utc)
            if not trade.next_funding_long or (
                trade.next_funding_long < now
                and not getattr(trade, '_funding_paid_long', False)
                and candidate_long <= now  # only correct stale data, don't jump to future
            ):
                trade.next_funding_long = candidate_long
                li = long_funding.get("interval_hours", "?")
                logger.info(f"Trade {trade.trade_id}: {trade.long_exchange} next at "
                            f"{trade.next_funding_long.strftime('%H:%M UTC')} (every {li}h)")

        short_next_ts = short_funding.get("next_timestamp")
        if short_next_ts:
            candidate_short = datetime.fromtimestamp(short_next_ts / 1000, tz=timezone.utc)
            if not trade.next_funding_short or (
                trade.next_funding_short < now
                and not getattr(trade, '_funding_paid_short', False)
                and candidate_short <= now  # only correct stale data, don't jump to future
            ):
                trade.next_funding_short = candidate_short
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
        immediate_spread = spread_info["immediate_spread_pct"]
        
        long_until = None
        short_until = None
        if trade.next_funding_long:
            long_until = int((trade.next_funding_long - now).total_seconds() / 60)
        if trade.next_funding_short:
            short_until = int((trade.next_funding_short - now).total_seconds() / 60)
        
        long_str = f"{long_until}min" if long_until is not None else "?"
        short_str = f"{short_until}min" if short_until is not None else "?"
        # If funding already paid, show next funding from API instead
        if long_until is not None and long_until < 0 and long_next_ts:
            api_long = datetime.fromtimestamp(long_next_ts / 1000, tz=timezone.utc)
            api_long_min = int((api_long - now).total_seconds() / 60)
            long_str = f"PAID (next {api_long_min}min)"
        if short_until is not None and short_until < 0 and short_next_ts:
            api_short = datetime.fromtimestamp(short_next_ts / 1000, tz=timezone.utc)
            api_short_min = int((api_short - now).total_seconds() / 60)
            short_str = f"PAID (next {api_short_min}min)"
        
        logger.info(
            f"🔔 {trade.symbol}: Immediate Spread = {float(immediate_spread):.4f}% "
            f"(norm={float(current_spread):.4f}%) | "
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

        # Mark that this cycle's funding has been collected —
        # prevents tracker auto-advance so we keep checking every 30s.
        if long_paid:
            trade._funding_paid_long = True
        if short_paid:
            trade._funding_paid_short = True

        which_paid = "long" if long_paid else "short"
        # Log first detection only (avoid spamming every 30s)
        if not getattr(trade, '_exit_check_active', False):
            trade._exit_check_active = True
            logger.info(
                f"Trade {trade.trade_id}: {which_paid} funding paid + {exit_offset}s elapsed — evaluating hold/exit",
                extra={"trade_id": trade.trade_id, "symbol": trade.symbol, "action": "exit_trigger"},
            )
            # ── Per-payment tracking ──────────────────────────────
            _lr = long_funding.get('rate') if long_paid else None
            _sr = short_funding.get('rate') if short_paid else None
            _long_usd = float((trade.entry_price_long or Decimal('0')) * trade.long_qty * abs(Decimal(str(_lr)))) if _lr else None
            _short_usd = float((trade.entry_price_short or Decimal('0')) * trade.short_qty * abs(Decimal(str(_sr)))) if _sr else None
            _net_usd = (_long_usd or 0) + (_short_usd or 0)

            trade.funding_collections += 1
            trade.funding_collected_usd += Decimal(str(_net_usd))

            # Journal: log individual funding payment detection
            if long_paid and _lr:
                self._journal.funding_detected(
                    trade.trade_id, trade.symbol, trade.long_exchange, 'long',
                    rate=_lr, estimated_payment=_long_usd,
                )
            if short_paid and _sr:
                self._journal.funding_detected(
                    trade.trade_id, trade.symbol, trade.short_exchange, 'short',
                    rate=_sr, estimated_payment=_short_usd,
                )

            # Journal: log this collection cycle with full detail
            self._journal.funding_collected(
                trade.trade_id, trade.symbol,
                collection_num=trade.funding_collections,
                long_exchange=trade.long_exchange,
                short_exchange=trade.short_exchange,
                long_rate=_lr,
                short_rate=_sr,
                long_payment_usd=_long_usd,
                short_payment_usd=_short_usd,
                net_payment_usd=_net_usd,
                cumulative_usd=float(trade.funding_collected_usd),
                immediate_spread=float(immediate_spread),
            )
            logger.info(
                f"💰 [{trade.symbol}] Funding collection #{trade.funding_collections}: "
                f"~${_net_usd:.4f} this cycle | cumulative ~${float(trade.funding_collected_usd):.4f}",
                extra={"trade_id": trade.trade_id, "symbol": trade.symbol, "action": "funding_collected"},
            )
            # Mark snapshot timer start
            trade._funding_paid_at = now

        # Check if still profitable to hold (funding spread)
        quick_cycle = getattr(self._cfg.trading_params, 'quick_cycle', False)
        hold_min = 0
        if trade.opened_at:
            hold_min = int((now - trade.opened_at).total_seconds() / 60)

        if quick_cycle:
            # ── Hold-or-Exit: check if IMMEDIATE spread (actual next payment)
            #    meets threshold — NOT the normalized spread ──
            hold_min_spread = getattr(
                self._cfg.trading_params, 'hold_min_spread', Decimal("0.5")
            )

            # Deduct exit fees (close 2 legs) to get net hold value
            _long_adp = self._exchanges.get(trade.long_exchange)
            _short_adp = self._exchanges.get(trade.short_exchange)
            _lf = _long_adp._instrument_cache.get(trade.symbol) if _long_adp else None
            _sf = _short_adp._instrument_cache.get(trade.symbol) if _short_adp else None
            _exit_fee_pct = (
                ((_lf.taker_fee if _lf else Decimal("0.0006")) +
                 (_sf.taker_fee if _sf else Decimal("0.0006"))) * 2 * Decimal("100")
            )
            immediate_spread_net = immediate_spread - _exit_fee_pct

            # ── Live price basis at hold/exit decision ────────────
            # At exit: selling long, buying back short.
            # Favorable basis = long_price >= short_price (sell expensive, buy back cheap).
            _l_price = Decimal("0")
            _s_price = Decimal("0")
            _adverse_exit_basis = Decimal("0")
            _basis_favorable = None  # None = unknown (prices unavailable)
            try:
                _l_ticker = await long_adapter.get_ticker(trade.symbol)
                _s_ticker = await short_adapter.get_ticker(trade.symbol)
                _l_price = Decimal(str(_l_ticker.get("last") or _l_ticker.get("close") or 0))
                _s_price = Decimal(str(_s_ticker.get("last") or _s_ticker.get("close") or 0))
                if _l_price > 0 and _s_price > 0:
                    raw_exit_basis = (_s_price - _l_price) / _l_price * Decimal("100")
                    _adverse_exit_basis = max(raw_exit_basis, Decimal("0"))
                    _basis_favorable = _l_price >= _s_price
                    if _adverse_exit_basis > Decimal("0"):
                        immediate_spread_net -= _adverse_exit_basis
                        logger.debug(
                            f"[{trade.symbol}] Adverse exit basis: "
                            f"long({trade.long_exchange})={_l_price} < short({trade.short_exchange})={_s_price} "
                            f"→ −{float(_adverse_exit_basis):.4f}% from hold spread"
                        )
            except Exception as _eb:
                logger.debug(f"[{trade.symbol}] Exit basis check failed: {_eb}")

            if immediate_spread_net >= hold_min_spread:
                # Net spread still good — but check if next funding is too far away.
                # No point holding capital for hours when we could redeploy it.
                hold_max_wait = getattr(
                    self._cfg.trading_params, 'hold_max_wait_seconds', 3600
                )
                if hold_max_wait > 0:
                    long_next = long_funding.get("next_timestamp")
                    short_next = short_funding.get("next_timestamp")
                    # Find the NEAREST next funding across both sides
                    next_funding_candidates = []
                    if long_next:
                        next_funding_candidates.append(long_next / 1000)
                    if short_next:
                        next_funding_candidates.append(short_next / 1000)
                    if next_funding_candidates:
                        nearest_sec = min(next_funding_candidates) - now.timestamp()
                        if nearest_sec > hold_max_wait:
                            nearest_min = int(nearest_sec / 60)
                            logger.info(
                                f"🔄 Trade {trade.trade_id}: EXIT — spread {float(immediate_spread):.4f}% "
                                f"≥ {float(hold_min_spread):.2f}% BUT next funding in {nearest_min}min "
                                f"> max wait {hold_max_wait // 60}min — freeing capital (held {hold_min}min)",
                                extra={
                                    "trade_id": trade.trade_id,
                                    "symbol": trade.symbol,
                                    "action": "hold_max_wait_exit",
                                },
                            )
                            trade._exit_reason = f'max_wait_{nearest_min}min'
                            self._journal.exit_decision(
                                trade.trade_id, trade.symbol,
                                reason=f'max_wait (next funding {nearest_min}min > {hold_max_wait//60}min)',
                                immediate_spread=immediate_spread, hold_min=hold_min,
                            )
                            await self._close_trade(trade)
                            return

                # Cherry-pick: if the costly payment (exit_before) is within
                # hold_max_wait, there is no room for another profitable cycle —
                # exit now instead of holding toward the costly payment.
                if trade.mode == "cherry_pick" and trade.exit_before:
                    secs_until_cost = (trade.exit_before - now).total_seconds()
                    if secs_until_cost <= hold_max_wait:
                        cost_min = int(secs_until_cost / 60)
                        logger.info(
                            f"🍒 Trade {trade.trade_id}: EXIT — cherry_pick costly payment in "
                            f"{cost_min}min ≤ max_wait {hold_max_wait // 60}min — "
                            f"no room for next cycle (held {hold_min}min)",
                            extra={
                                "trade_id": trade.trade_id,
                                "symbol": trade.symbol,
                                "action": "cherry_pick_cost_exit",
                            },
                        )
                        trade._exit_reason = f'cherry_pick_cost_in_{cost_min}min'
                        self._journal.exit_decision(
                            trade.trade_id, trade.symbol,
                            reason=f'cherry_pick costly payment in {cost_min}min ≤ {hold_max_wait // 60}min wait',
                            immediate_spread=immediate_spread, hold_min=hold_min,
                        )
                        await self._close_trade(trade)
                        return

                # Still within acceptable wait time — keep holding.
                # Log HOLD decision periodically (every 5 min) to avoid spam.
                # Do NOT advance trackers — keep gate open so we check every 30s.
                if not getattr(trade, '_hold_logged_until', None) or trade._hold_logged_until < now:
                    # Show next funding from API (for display only)
                    _long_next = long_funding.get("next_timestamp")
                    _short_next = short_funding.get("next_timestamp")
                    next_long_str = datetime.fromtimestamp(
                        _long_next / 1000, tz=timezone.utc
                    ).strftime('%H:%M') if _long_next else '?'
                    next_short_str = datetime.fromtimestamp(
                        _short_next / 1000, tz=timezone.utc
                    ).strftime('%H:%M') if _short_next else '?'
                    # Calculate time until next funding for display
                    _nearest_min = '?'
                    _candidates = []
                    if _long_next:
                        _candidates.append(_long_next / 1000)
                    if _short_next:
                        _candidates.append(_short_next / 1000)
                    if _candidates:
                        _nearest_min = f"{int((min(_candidates) - now.timestamp()) / 60)}min"
                    trade._hold_logged_until = now + timedelta(minutes=5)
                    logger.info(
                        f"🔄 Trade {trade.trade_id}: HOLD — immediate spread {float(immediate_spread):.4f}% "
                        f"≥ {float(hold_min_spread):.2f}% threshold (held {hold_min}min) | "
                        f"Next funding in {_nearest_min} — "
                        f"{trade.long_exchange}={next_long_str}, "
                        f"{trade.short_exchange}={next_short_str}",
                        extra={
                            "trade_id": trade.trade_id,
                            "symbol": trade.symbol,
                            "action": "hold_after_payment",
                        },
                    )
                    self._journal.hold_decision(
                        trade.trade_id, trade.symbol,
                        immediate_spread=immediate_spread,
                        next_funding_min=_nearest_min,
                    )
                    # ── 5-min position snapshot (price + spread + unrealized PnL) ──
                    _min_since = int((now - trade._funding_paid_at).total_seconds() / 60) if getattr(trade, '_funding_paid_at', None) else hold_min
                    try:
                        _l_ticker = await long_adapter.get_ticker(trade.symbol)
                        _s_ticker = await short_adapter.get_ticker(trade.symbol)
                        _l_price = Decimal(str(_l_ticker.get("last", 0)))
                        _s_price = Decimal(str(_s_ticker.get("last", 0)))
                        # Unrealized price PnL: long gains when price rises, short loses and vice-versa
                        _long_pnl_usd = float((_l_price - (trade.entry_price_long or _l_price)) * trade.long_qty)
                        _short_pnl_usd = float(((trade.entry_price_short or _s_price) - _s_price) * trade.short_qty)
                        _price_pnl_usd = _long_pnl_usd + _short_pnl_usd
                        self._journal.position_snapshot(
                            trade.trade_id, trade.symbol,
                            minutes_since_funding=_min_since,
                            long_exchange=trade.long_exchange,
                            short_exchange=trade.short_exchange,
                            long_price=float(_l_price),
                            short_price=float(_s_price),
                            immediate_spread=float(immediate_spread),
                            long_pnl_usd=_long_pnl_usd,
                            short_pnl_usd=_short_pnl_usd,
                            price_pnl_usd=_price_pnl_usd,
                            funding_collected_usd=float(trade.funding_collected_usd),
                        )
                    except Exception as _snap_err:
                        logger.debug(f"Snapshot fetch failed for {trade.symbol}: {_snap_err}")
                return
            else:
                # Spread dropped below threshold.
                # Wait for favorable price basis before exiting (max 20 min).
                _wait_max_sec = 20 * 60
                _wait_start = getattr(trade, '_exit_wait_start', None)
                _waited_sec = (now - _wait_start).total_seconds() if _wait_start else 0

                if _basis_favorable is True or _basis_favorable is None or _waited_sec >= _wait_max_sec:
                    # Exit now: basis is favorable OR 20-min timeout reached
                    if not _basis_favorable and _waited_sec >= _wait_max_sec:
                        _reason = f'spread_low_basis_timeout_{int(_waited_sec / 60)}min'
                        logger.info(
                            f"⏱ Trade {trade.trade_id}: EXIT (forced — {int(_waited_sec / 60)}min wait, basis still adverse "
                            f"{trade.long_exchange}={_l_price}/{trade.short_exchange}={_s_price}) "
                            f"| spread {float(immediate_spread):.4f}% (held {hold_min}min)",
                            extra={"trade_id": trade.trade_id, "symbol": trade.symbol, "action": "basis_wait_timeout_exit"},
                        )
                    else:
                        _reason = f'spread_low_{float(immediate_spread):.4f}pct_basis_ok'
                        logger.info(
                            f"🔄 Trade {trade.trade_id}: EXIT — spread {float(immediate_spread):.4f}% "
                            f"< {float(hold_min_spread):.2f}% threshold, basis favorable "
                            f"({trade.long_exchange}={_l_price}/{trade.short_exchange}={_s_price}) "
                            f"(held {hold_min}min)",
                            extra={"trade_id": trade.trade_id, "symbol": trade.symbol, "action": "quick_cycle_exit"},
                        )
                    trade._exit_reason = _reason
                    trade._exit_wait_start = None
                    self._journal.exit_decision(
                        trade.trade_id, trade.symbol,
                        reason=_reason,
                        immediate_spread=immediate_spread, hold_min=hold_min,
                    )
                    await self._close_trade(trade)
                else:
                    # Basis adverse — start or continue waiting
                    if _wait_start is None:
                        trade._exit_wait_start = now
                        logger.info(
                            f"⏳ Trade {trade.trade_id}: WAITING FOR FAVORABLE BASIS (max 20min) — "
                            f"spread {float(immediate_spread):.4f}% below threshold but "
                            f"adverse basis {float(_adverse_exit_basis):.4f}% "
                            f"({trade.long_exchange}={_l_price} < {trade.short_exchange}={_s_price})",
                            extra={"trade_id": trade.trade_id, "symbol": trade.symbol, "action": "basis_wait_start"},
                        )
                    else:
                        logger.debug(
                            f"⏳ Trade {trade.trade_id}: still waiting for favorable basis "
                            f"({int(_waited_sec / 60)}min / 20min) — "
                            f"adverse {float(_adverse_exit_basis):.4f}%"
                        )
                    return  # check again next cycle
                return

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

    # ── Position reconciliation (detect manual closes) ──────────

    async def _reconcile_positions(self) -> None:
        """Detect trades that were manually closed on the exchange.

        For each active OPEN trade, fetch real positions from both exchanges.
        - Both legs gone   -> fully manually closed -> clean up state
        - One leg gone     -> partial manual close  -> close remaining leg
        - Both legs exist  -> normal, do nothing
        """
        if not self._active_trades:
            return

        # Collect exchanges that have active trades
        exchanges_needed: set[str] = set()
        for trade in self._active_trades.values():
            if trade.state == TradeState.OPEN:
                exchanges_needed.add(trade.long_exchange)
                exchanges_needed.add(trade.short_exchange)

        if not exchanges_needed:
            return

        # One REST call per exchange to get all positions
        exchange_positions: Dict[str, List[Position]] = {}
        for exch_id in exchanges_needed:
            adapter = self._exchanges.get(exch_id)
            if not adapter:
                continue
            try:
                positions = await adapter.get_positions()
                exchange_positions[exch_id] = positions
            except Exception as e:
                logger.warning(
                    f"Reconcile: failed to fetch positions from {exch_id}: {e}",
                    extra={"exchange": exch_id, "action": "reconcile_error"},
                )
                # Don't act on incomplete data — skip this cycle entirely
                return

        # Check each active trade against real positions
        for trade_id in list(self._active_trades):
            trade = self._active_trades.get(trade_id)
            if not trade or trade.state != TradeState.OPEN:
                continue

            long_positions = exchange_positions.get(trade.long_exchange, [])
            short_positions = exchange_positions.get(trade.short_exchange, [])

            long_exists = any(
                p.symbol == trade.symbol and p.side == OrderSide.BUY
                for p in long_positions
            )
            short_exists = any(
                p.symbol == trade.symbol and p.side == OrderSide.SELL
                for p in short_positions
            )

            if long_exists and short_exists:
                continue  # both legs intact

            if not long_exists and not short_exists:
                # ── Fully manually closed ─────────────────────────
                logger.warning(
                    f"MANUAL CLOSE DETECTED: Trade {trade.trade_id} ({trade.symbol}) -- "
                    f"no positions on {trade.long_exchange} or {trade.short_exchange}. "
                    f"Removing from active trades.",
                    extra={
                        "trade_id": trade.trade_id,
                        "symbol": trade.symbol,
                        "action": "manual_close_detected",
                    },
                )
                trade.state = TradeState.CLOSED
                trade.closed_at = datetime.now(timezone.utc)
                await self._record_manual_close(trade)
                await self._redis.delete_trade_state(trade.trade_id)
                del self._active_trades[trade.trade_id]

            elif not long_exists:
                # ── Long leg gone, short remains ──────────────────
                logger.warning(
                    f"PARTIAL MANUAL CLOSE: Trade {trade.trade_id} ({trade.symbol}) -- "
                    f"long on {trade.long_exchange} GONE. "
                    f"Closing remaining short on {trade.short_exchange}.",
                    extra={
                        "trade_id": trade.trade_id,
                        "symbol": trade.symbol,
                        "action": "partial_manual_close",
                    },
                )
                short_adapter = self._exchanges.get(trade.short_exchange)
                if short_adapter:
                    await self._close_orphan(
                        short_adapter, trade.short_exchange, trade.symbol,
                        OrderSide.BUY, {"filled": float(trade.short_qty)},
                    )
                trade.state = TradeState.CLOSED
                trade.closed_at = datetime.now(timezone.utc)
                await self._record_manual_close(trade)
                await self._redis.delete_trade_state(trade.trade_id)
                del self._active_trades[trade.trade_id]

            else:
                # ── Short leg gone, long remains ──────────────────
                logger.warning(
                    f"PARTIAL MANUAL CLOSE: Trade {trade.trade_id} ({trade.symbol}) -- "
                    f"short on {trade.short_exchange} GONE. "
                    f"Closing remaining long on {trade.long_exchange}.",
                    extra={
                        "trade_id": trade.trade_id,
                        "symbol": trade.symbol,
                        "action": "partial_manual_close",
                    },
                )
                long_adapter = self._exchanges.get(trade.long_exchange)
                if long_adapter:
                    await self._close_orphan(
                        long_adapter, trade.long_exchange, trade.symbol,
                        OrderSide.SELL, {"filled": float(trade.long_qty)},
                    )
                trade.state = TradeState.CLOSED
                trade.closed_at = datetime.now(timezone.utc)
                await self._record_manual_close(trade)
                await self._redis.delete_trade_state(trade.trade_id)
                del self._active_trades[trade.trade_id]

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

            # ── Fallback: if exchange didn't return avg price, use ticker ──
            if trade.exit_price_long is None and long_adapter:
                try:
                    t = await long_adapter.get_ticker(trade.symbol)
                    trade.exit_price_long = Decimal(str(t.get("last", 0)))
                    logger.info(f"[{trade.symbol}] Long exit price from ticker: {trade.exit_price_long}")
                except Exception:
                    pass
            if trade.exit_price_short is None and short_adapter:
                try:
                    t = await short_adapter.get_ticker(trade.symbol)
                    trade.exit_price_short = Decimal(str(t.get("last", 0)))
                    logger.info(f"[{trade.symbol}] Short exit price from ticker: {trade.exit_price_short}")
                except Exception:
                    pass

            close_fees = self._extract_fee(long_fill) + self._extract_fee(short_fill)
            total_fees = (trade.fees_paid_total or Decimal("0")) + close_fees
            trade.fees_paid_total = total_fees
            if trade.funding_paid_total is None and trade.funding_received_total is None:
                if trade.funding_collected_usd and trade.funding_collected_usd > 0:
                    # Use actual accumulated collection total — multi-payment aware
                    trade.funding_received_total = trade.funding_collected_usd
                    trade.funding_paid_total = Decimal("0")
                else:
                    # Fallback: estimate from entry rates (single-payment approximation)
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
            profit_pct = (total_pnl / invested * Decimal("100")) if invested > 0 else Decimal("0")
            hold_minutes = Decimal("0")
            if trade.opened_at and trade.closed_at:
                hold_minutes = Decimal(str((trade.closed_at - trade.opened_at).total_seconds() / 60))

            # ── Fetch current funding rates at exit for comparison ──
            exit_funding_long_rate = None
            exit_funding_short_rate = None
            try:
                if long_adapter:
                    _lf = long_adapter.get_funding_rate_cached(trade.symbol)
                    exit_funding_long_rate = _lf.get("rate") if _lf else None
                if short_adapter:
                    _sf = short_adapter.get_funding_rate_cached(trade.symbol)
                    exit_funding_short_rate = _sf.get("rate") if _sf else None
            except Exception:
                pass  # best-effort

            _exit_reason = getattr(trade, '_exit_reason', 'spread_below_threshold')
            entry_lr = float(trade.long_funding_rate or 0) * 100
            entry_sr = float(trade.short_funding_rate or 0) * 100
            exit_lr = float(exit_funding_long_rate or 0) * 100 if exit_funding_long_rate else None
            exit_sr = float(exit_funding_short_rate or 0) * 100 if exit_funding_short_rate else None

            # Build funding rates comparison string
            funding_rates_str = f"  At entry:  {trade.long_exchange}={entry_lr:+.4f}%  {trade.short_exchange}={entry_sr:+.4f}%\n"
            if exit_lr is not None and exit_sr is not None:
                funding_rates_str += f"  At exit:   {trade.long_exchange}={exit_lr:+.4f}%  {trade.short_exchange}={exit_sr:+.4f}%"
            else:
                funding_rates_str += f"  At exit:   (rates unavailable)"

            logger.info(
                f"\n{'='*60}\n"
                f"  📊 TRADE CLOSED — {trade.trade_id}\n"
                f"  Symbol:     {trade.symbol}\n"
                f"  Mode:       {trade.mode}\n"
                f"  Duration:   {float(hold_minutes):.0f} min\n"
                f"  Exit reason: {_exit_reason}\n"
                f"  ────────── PER-LEG BREAKDOWN ──────────\n"
                f"  LONG  {trade.long_exchange}:\n"
                f"    qty={trade.long_qty}  entry=${float(trade.entry_price_long or 0):.6f}  exit=${float(trade.exit_price_long or 0):.6f}\n"
                f"    PnL: ${float(long_pnl):.4f}  (notional {float(entry_notional_long):.2f} → {float(exit_notional_long):.2f})\n"
                f"  SHORT {trade.short_exchange}:\n"
                f"    qty={trade.short_qty}  entry=${float(trade.entry_price_short or 0):.6f}  exit=${float(trade.exit_price_short or 0):.6f}\n"
                f"    PnL: ${float(short_pnl):.4f}  (notional {float(entry_notional_short):.2f} → {float(exit_notional_short):.2f})\n"
                f"  ────────── FUNDING RATES ──────────\n"
                f"{funding_rates_str}\n"
                f"  ────────── TOTALS ──────────\n"
                f"  Price PnL:  ${float(price_pnl):.4f}  (long=${float(long_pnl):.4f} + short=${float(short_pnl):.4f})\n"
                f"  Funding:    +${float(funding_income):.4f} income  -${float(funding_cost):.4f} cost  = ${float(funding_net):.4f} net\n"
                f"  Fees:       -${float(total_fees):.4f}\n"
                f"  Invested:   ${float(invested):.2f}\n"
                f"  ────────────────────────────────\n"
                f"  NET PROFIT: ${float(total_pnl):.4f}  ({float(profit_pct):.3f}%)\n"
                f"{'='*60}",
                extra={
                    "trade_id": trade.trade_id,
                    "action": "trade_closed",
                    "data": {
                        "symbol": trade.symbol,
                        "invested": float(invested),
                        "long_pnl": float(long_pnl),
                        "short_pnl": float(short_pnl),
                        "price_pnl": float(price_pnl),
                        "funding_income": float(funding_income),
                        "funding_cost": float(funding_cost),
                        "funding_net": float(funding_net),
                        "fees": float(total_fees),
                        "net_profit": float(total_pnl),
                        "profit_pct": float(profit_pct),
                        "hold_minutes": float(hold_minutes),
                    }
                },
            )

            # ── Journal: record trade close ──
            self._journal.trade_closed(
                trade_id=trade.trade_id, symbol=trade.symbol, mode=trade.mode,
                duration_min=float(hold_minutes),
                entry_price_long=trade.entry_price_long,
                entry_price_short=trade.entry_price_short,
                exit_price_long=trade.exit_price_long,
                exit_price_short=trade.exit_price_short,
                long_pnl=long_pnl, short_pnl=short_pnl,
                price_pnl=price_pnl, funding_income=funding_income,
                funding_cost=funding_cost, funding_net=funding_net,
                fees=total_fees, net_profit=total_pnl,
                profit_pct=profit_pct, invested=invested,
                exit_reason=_exit_reason,
                entry_funding_long=trade.long_funding_rate,
                entry_funding_short=trade.short_funding_rate,
                exit_funding_long=exit_funding_long_rate,
                exit_funding_short=exit_funding_short_rate,
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
                "mode": trade.mode,
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
                "entry_edge_pct": str(trade.entry_edge_pct) if trade.entry_edge_pct is not None else None,
                "total_pnl": float(total_pnl),
                "price_pnl": float(price_pnl),
                "funding_net": float(funding_net),
                "invested": float(invested),
                "hold_minutes": float(hold_minutes),
                "exit_reason": _exit_reason,
                "funding_collections": trade.funding_collections,
                "funding_collected_usd": str(trade.funding_collected_usd),
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
        """Extract fee cost in USDT from a CCXT order dict.

        Some exchanges return fees in the base currency (e.g. CYBER) rather than USDT.
        When that happens we convert using the order's average fill price so the
        total_fees figure is always denominated in USDT.
        """
        avg_price = Decimal("0")
        try:
            p = order.get("average") or order.get("price") or order.get("avgPrice") or 0
            if p:
                avg_price = Decimal(str(p))
        except Exception:
            pass

        def _cost_to_usdt(f: dict) -> Decimal:
            try:
                cost = Decimal(str(f.get("cost", 0) or 0))
                currency = (f.get("currency") or "").upper()
                # If fee currency is quote (USDT / BUSD / USDC) or unknown, use as-is
                if not currency or currency in ("USDT", "BUSD", "USDC", "USD"):
                    return cost
                # Fee is in base asset — convert to USDT using fill price
                if avg_price > 0:
                    return cost * avg_price
                return cost  # fallback: can't convert, treat as-is
            except Exception:
                return Decimal("0")

        total = Decimal("0")
        fee = order.get("fee")
        if isinstance(fee, dict) and fee.get("cost") is not None:
            total += _cost_to_usdt(fee)
        fees = order.get("fees")
        if isinstance(fees, list):
            for f in fees:
                if isinstance(f, dict) and f.get("cost") is not None:
                    total += _cost_to_usdt(f)
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

    async def _record_manual_close(self, trade: TradeRecord) -> None:
        """Save a manually-closed trade to Redis history with best-effort PnL."""
        try:
            now = datetime.now(timezone.utc)
            trade.closed_at = trade.closed_at or now

            # Try to get exit prices from current mark prices (best-effort, no await)
            long_adapter = self._exchanges.get(trade.long_exchange)
            short_adapter = self._exchanges.get(trade.short_exchange)
            if trade.exit_price_long is None and long_adapter:
                mp = long_adapter.get_mark_price(trade.symbol)
                if mp:
                    trade.exit_price_long = Decimal(str(mp))
            if trade.exit_price_short is None and short_adapter:
                mp = short_adapter.get_mark_price(trade.symbol)
                if mp:
                    trade.exit_price_short = Decimal(str(mp))

            # Fallback: use entry prices (PnL reported as 0 price movement)
            exit_long = trade.exit_price_long or trade.entry_price_long or Decimal("0")
            exit_short = trade.exit_price_short or trade.entry_price_short or Decimal("0")

            entry_notional_long = (trade.entry_price_long or Decimal("0")) * trade.long_qty
            entry_notional_short = (trade.entry_price_short or Decimal("0")) * trade.short_qty
            exit_notional_long = exit_long * trade.long_qty
            exit_notional_short = exit_short * trade.short_qty
            long_pnl = exit_notional_long - entry_notional_long
            short_pnl = entry_notional_short - exit_notional_short
            price_pnl = long_pnl + short_pnl

            if trade.funding_collected_usd and trade.funding_collected_usd > 0:
                funding_net = trade.funding_collected_usd
            else:
                paid, received = self._estimate_funding_totals(trade)
                funding_net = received - paid

            total_fees = trade.fees_paid_total or Decimal("0")
            total_pnl = price_pnl + funding_net - total_fees
            invested = max(entry_notional_long, entry_notional_short)
            profit_pct = (total_pnl / invested * Decimal("100")) if invested > 0 else Decimal("0")
            hold_minutes = Decimal("0")
            if trade.opened_at and trade.closed_at:
                hold_minutes = Decimal(str((trade.closed_at - trade.opened_at).total_seconds() / 60))

            trade_data = {
                "id": trade.trade_id,
                "symbol": trade.symbol,
                "mode": trade.mode,
                "long_exchange": trade.long_exchange,
                "short_exchange": trade.short_exchange,
                "long_qty": str(trade.long_qty),
                "short_qty": str(trade.short_qty),
                "entry_price_long": str(trade.entry_price_long) if trade.entry_price_long is not None else None,
                "entry_price_short": str(trade.entry_price_short) if trade.entry_price_short is not None else None,
                "exit_price_long": str(exit_long),
                "exit_price_short": str(exit_short),
                "fees_paid_total": str(total_fees),
                "funding_received_total": str(max(funding_net, Decimal("0"))),
                "funding_paid_total": str(max(-funding_net, Decimal("0"))),
                "long_funding_rate": str(trade.long_funding_rate) if trade.long_funding_rate is not None else None,
                "short_funding_rate": str(trade.short_funding_rate) if trade.short_funding_rate is not None else None,
                "opened_at": trade.opened_at.isoformat() if trade.opened_at else None,
                "closed_at": trade.closed_at.isoformat() if trade.closed_at else None,
                "status": "CLOSED",
                "entry_edge_pct": str(trade.entry_edge_pct) if trade.entry_edge_pct is not None else None,
                "total_pnl": float(total_pnl),
                "price_pnl": float(price_pnl),
                "funding_net": float(funding_net),
                "invested": float(invested),
                "hold_minutes": float(hold_minutes),
                "exit_reason": "manual_close",
                "funding_collections": trade.funding_collections,
                "funding_collected_usd": str(trade.funding_collected_usd),
            }
            await self._redis.zadd(
                "trinity:trades:history",
                {json.dumps(trade_data): datetime.utcnow().timestamp()},
            )
            logger.info(
                f"📋 Manual close recorded: {trade.trade_id} ({trade.symbol}) "
                f"PnL=${float(total_pnl):.4f} (held {float(hold_minutes):.0f}min)",
                extra={"trade_id": trade.trade_id, "action": "manual_close_recorded"},
            )
        except Exception as e:
            logger.error(f"Failed to record manual close for {trade.trade_id}: {e}")

    async def close_all_positions(self) -> None:
        """Close every active trade — called during graceful shutdown."""
        for trade_id, trade in list(self._active_trades.items()):
            if trade.state == TradeState.OPEN:
                logger.info(f"Shutdown: closing trade {trade_id}")
                await self._close_trade(trade)

    # ── Helpers ──────────────────────────────────────────────────

    _TIMEOUT_COOLDOWN_SEC = 600        # 10 min cooldown after first order timeout
    _TIMEOUT_BLACKLIST_THRESHOLD = 2     # blacklist after N consecutive timeouts

    async def _place_with_timeout(self, adapter, req: OrderRequest) -> Optional[dict]:
        """Place order with timeout. Returns fill dict or None."""
        timeout = self._cfg.execution.order_timeout_ms / 1000
        streak_key = f"{req.symbol}:{req.exchange}"
        try:
            result = await asyncio.wait_for(adapter.place_order(req), timeout=timeout)
            # Success — reset streak counter
            self._timeout_streak.pop(streak_key, None)
            return result
        except asyncio.TimeoutError:
            count = self._timeout_streak.get(streak_key, 0) + 1
            self._timeout_streak[streak_key] = count
            logger.error(
                f"Order timeout ({timeout}s) on {req.exchange}/{req.symbol} "
                f"(streak {count}/{self._TIMEOUT_BLACKLIST_THRESHOLD})",
                extra={"exchange": req.exchange, "symbol": req.symbol, "action": "order_timeout"},
            )
            if count >= self._TIMEOUT_BLACKLIST_THRESHOLD:
                self._add_to_blacklist(req.symbol, req.exchange)
                logger.warning(
                    f"⛔ {req.symbol} blacklisted on {req.exchange} after "
                    f"{count} consecutive timeouts",
                )
                self._timeout_streak.pop(streak_key, None)
            else:
                # Short cooldown to stop immediate retry
                await self._redis.set_cooldown(req.symbol, self._TIMEOUT_COOLDOWN_SEC)
                logger.warning(
                    f"⏸️ {req.symbol} cooldown {self._TIMEOUT_COOLDOWN_SEC}s after timeout "
                    f"on {req.exchange}",
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
        side: OrderSide, fill: dict, fallback_qty: Optional[Decimal] = None,
    ) -> None:
        """Emergency close of a single orphaned leg."""
        filled_qty = Decimal(str(fill.get("filled", 0)))
        if filled_qty <= 0:
            if fallback_qty and fallback_qty > 0:
                logger.warning(
                    f"⚠️ Orphan fill reported 0 — using fallback qty {fallback_qty} for {symbol} on {exchange}"
                )
                filled_qty = fallback_qty
            else:
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
            "mode": trade.mode,
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
            "funding_collections": trade.funding_collections,
            "funding_collected_usd": str(trade.funding_collected_usd),
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
                mode=data.get("mode", "hold"),
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
                funding_collections=int(data.get("funding_collections", 0)),
                funding_collected_usd=Decimal(data["funding_collected_usd"]) if data.get("funding_collected_usd") else Decimal("0"),
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

    async def _journal_balance_snapshot(self) -> None:
        """Record a balance snapshot to the trade journal (every ~30min)."""
        try:
            balances = {}
            total = 0.0
            for exchange_id in self._cfg.enabled_exchanges:
                adapter = self._exchanges.get(exchange_id)
                if not adapter:
                    continue
                try:
                    bal = await adapter.get_balance()
                    usdt = float(bal.get("free", 0))
                    balances[exchange_id] = usdt
                    total += usdt
                except Exception:
                    balances[exchange_id] = None
            self._journal.balance_snapshot(balances, total=total)
        except Exception as e:
            logger.debug(f"Balance snapshot error: {e}")
