"""
Execution controller mixin — methods extracted from controller.py.
Do NOT import this module directly; use ExecutionController from controller.py.
"""
from __future__ import annotations

import asyncio
import time as _time
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from src.core.contracts import (
    OpportunityCandidate,
    OrderRequest,
    OrderSide,
    TradeMode,
    TradeRecord,
    TradeState,
)
from src.core.logging import get_logger
from src.execution import helpers as _h

if TYPE_CHECKING:
    pass  # all attribute access via self (mixin pattern)

logger = get_logger("execution")


class _EntryMixin:
    async def handle_opportunity(self, opp: OpportunityCandidate) -> None:
        """Validate and execute a new funding-arb trade."""
        logger.info(
            f"🔍 [{opp.symbol}] Evaluating opportunity: mode={opp.mode} "
            f"spread={opp.immediate_spread_pct:.4f}% net={opp.net_edge_pct:.4f}% "
            f"L={opp.long_exchange} S={opp.short_exchange}"
        )

        # Blacklist guard — skip symbols/exchanges flagged as delisting etc.
        if self._blacklist.is_blacklisted(opp.symbol, opp.long_exchange, opp.short_exchange):
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

        # ── TOCTOU guard: claim the symbol slot BEFORE any await ──────────────
        # This must be the FIRST in-memory check so that concurrent coroutines
        # cannot both pass the duplicate/exchange checks before either one
        # reaches the Redis lock.  The try/finally below guarantees cleanup.
        if opp.symbol in self._symbols_entering:
            logger.info(f"🔒 Skipping {opp.symbol}: entry already in progress")
            return
        self._symbols_entering.add(opp.symbol)
        try:
            return await self._handle_opportunity_inner(opp)
        finally:
            self._symbols_entering.discard(opp.symbol)

    async def _handle_opportunity_inner(self, opp: OpportunityCandidate) -> None:
        """Inner implementation — called only after the TOCTOU guard is held."""
        _t0_mono = _time.monotonic()  # execution latency tracking

        # Duplicate guard — O(1) via maintained set
        if opp.symbol in self._active_symbols:
            logger.info(f"🔁 Skipping {opp.symbol}: already have active trade")
            return

        # Concurrency cap
        if len(self._active_trades) >= self._cfg.execution.concurrent_opportunities:
            logger.info(
                f"🚫 Skipping {opp.symbol}: concurrency cap reached "
                f"({len(self._active_trades)}/{self._cfg.execution.concurrent_opportunities})"
            )
            return

        # Exchange-in-use guard — O(1) via maintained set
        for ex in (opp.long_exchange, opp.short_exchange):
            if ex in self._busy_exchanges:
                logger.info(
                    f"🔒 Skipping {opp.symbol}: {ex} already in use by another trade"
                )
                return

        # ── Funding spread gate (safety check) ──
        # net_edge_pct = imminent payment spread minus ALL costs (fees + buffers).
        # This is the scanner's authoritative signal — no 8h normalization.
        tp = self._cfg.trading_params
        if opp.mode == TradeMode.CHERRY_PICK:
            if opp.net_edge_pct < tp.min_funding_spread:
                logger.info(
                    f"📉 Skipping {opp.symbol}: cherry-pick net {opp.net_edge_pct:.4f}% "
                    f"< min_funding_spread {tp.min_funding_spread}% (gross={opp.gross_edge_pct:.4f}%)"
                )
                return
        else:
            if opp.net_edge_pct < tp.min_funding_spread:
                logger.info(
                    f"📉 Skipping {opp.symbol}: net {opp.net_edge_pct:.4f}% "
                    f"< min_funding_spread {tp.min_funding_spread}% (gross={opp.gross_edge_pct:.4f}%)"
                )
                return

        long_adapter = self._exchanges.get(opp.long_exchange)
        short_adapter = self._exchanges.get(opp.short_exchange)

        # ── Tier-based Entry timing gate ─────────────────────────────
        # All tiers (TOP/MEDIUM/BAD): only within entry_offset_seconds of funding payment
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

        # Tier-based timing decision
        tier = opp.entry_tier
        tier_emoji = {"top": "🏆", "medium": "📊", "bad": "⚠️"}.get(tier or "", "")

        # ── Minimum time-to-funding guard ────────────────────────────────
        # Never enter if funding is < 120 seconds away. At that point the
        # order placement + settlement overhead risks straddling the payment
        # boundary, causing negative or missed funding.
        _MIN_ENTRY_SECS_BEFORE_FUNDING = 120

        if tier in ("top", "medium", "bad"):
            # All tiers: require entry within entry_offset_seconds window before funding
            if primary_next_ms is None:
                logger.info(
                    f"⏳ Skipping {opp.symbol}: {tier.upper()} tier but no funding timestamp"
                )
                return
            # Defense-in-depth: re-read live funding timestamp to catch stale opp data
            _live_funding = long_adapter.get_funding_rate_cached(opp.symbol) if long_adapter else None
            _live_short_funding = short_adapter.get_funding_rate_cached(opp.symbol) if short_adapter else None
            _live_next_ms: float | None = None
            if primary_side == "long" and _live_funding and _live_funding.get("next_timestamp"):
                _live_next_ms = _live_funding["next_timestamp"]
            elif primary_side == "short" and _live_short_funding and _live_short_funding.get("next_timestamp"):
                _live_next_ms = _live_short_funding["next_timestamp"]
            if _live_next_ms is not None and _live_next_ms != primary_next_ms:
                logger.info(
                    f"🔄 [{opp.symbol}] Funding timestamp changed since scan: "
                    f"opp={primary_next_ms} → live={_live_next_ms}. Using live value."
                )
                primary_next_ms = _live_next_ms
            seconds_until = (primary_next_ms - now_ms) / 1000
            if not (_MIN_ENTRY_SECS_BEFORE_FUNDING < seconds_until <= entry_offset):
                if seconds_until <= _MIN_ENTRY_SECS_BEFORE_FUNDING and seconds_until > 0:
                    logger.info(
                        f"⏳ Skipping {opp.symbol}: {tier_emoji} {tier.upper()} tier — "
                        f"funding too close ({int(seconds_until)}s < {_MIN_ENTRY_SECS_BEFORE_FUNDING}s minimum)"
                    )
                else:
                    logger.info(
                        f"⏳ Skipping {opp.symbol}: {tier_emoji} {tier.upper()} tier — "
                        f"not in {entry_offset}s window. Next funding in {int(seconds_until/60)}min."
                    )
                return
            logger.info(
                f"{tier_emoji} [{opp.symbol}] {tier.upper()} tier — "
                f"entering in {entry_offset}s window "
                f"(price_spread={float(opp.price_spread_pct):+.4f}%, "
                f"funding in {int(seconds_until/60)}min)"
            )
        else:
            # No qualifying tier — fall back to original timing gate
            if primary_next_ms is None:
                logger.info(
                    f"⏳ Skipping {opp.symbol}: no funding timestamp available from scanner"
                )
                return
            # Defense-in-depth: re-read live funding timestamp
            _live_funding = long_adapter.get_funding_rate_cached(opp.symbol) if long_adapter else None
            _live_short_funding = short_adapter.get_funding_rate_cached(opp.symbol) if short_adapter else None
            _live_next_ms_nt: float | None = None
            if primary_side == "long" and _live_funding and _live_funding.get("next_timestamp"):
                _live_next_ms_nt = _live_funding["next_timestamp"]
            elif primary_side == "short" and _live_short_funding and _live_short_funding.get("next_timestamp"):
                _live_next_ms_nt = _live_short_funding["next_timestamp"]
            if _live_next_ms_nt is not None and _live_next_ms_nt != primary_next_ms:
                logger.info(
                    f"🔄 [{opp.symbol}] Funding timestamp changed since scan: "
                    f"opp={primary_next_ms} → live={_live_next_ms_nt}. Using live value."
                )
                primary_next_ms = _live_next_ms_nt
            seconds_until = (primary_next_ms - now_ms) / 1000
            if not (_MIN_ENTRY_SECS_BEFORE_FUNDING < seconds_until <= entry_offset):
                if seconds_until <= _MIN_ENTRY_SECS_BEFORE_FUNDING and seconds_until > 0:
                    logger.info(
                        f"⏳ Skipping {opp.symbol}: no tier — "
                        f"funding too close ({int(seconds_until)}s < {_MIN_ENTRY_SECS_BEFORE_FUNDING}s minimum)"
                    )
                else:
                    logger.info(
                        f"⏳ Skipping {opp.symbol}: no tier, not in entry window. "
                        f"Next funding in {int(seconds_until/60)}min."
                    )
                return

        logger.info(f"✅ [{opp.symbol}] Passed all gates — proceeding to entry")
        # NOTE: Basis Inversion Guard removed — the exit guard already ensures we exit
        # at entry_basis or better, so the entry ask/bid spread is neutral on round-trip.
        # Any bid-ask spread cost is already covered by fees_pct + slippage_buffer_pct.

        # ── Rate direction re-verification ─────────────────────────────────
        # Re-read live cached rates right before placing orders.
        # If the income direction has flipped since the scanner evaluated,
        # abort entry to avoid paying funding instead of receiving.
        _verify_long = long_adapter.get_funding_rate_cached(opp.symbol) if long_adapter else None
        _verify_short = short_adapter.get_funding_rate_cached(opp.symbol) if short_adapter else None
        if _verify_long and _verify_short:
            _vl_rate = Decimal(str(_verify_long["rate"]))
            _vs_rate = Decimal(str(_verify_short["rate"]))
            # At entry: long_rate < 0 means long receives, short_rate > 0 means short receives
            _orig_long_income = opp.long_funding_rate < 0
            _orig_short_income = opp.short_funding_rate > 0
            _now_long_income = _vl_rate < 0
            _now_short_income = _vs_rate > 0
            if _orig_long_income != _now_long_income or _orig_short_income != _now_short_income:
                logger.warning(
                    f"🚫 [{opp.symbol}] Rate direction FLIPPED since scan — aborting entry! "
                    f"Scan: L={float(opp.long_funding_rate)*100:+.4f}% S={float(opp.short_funding_rate)*100:+.4f}% "
                    f"→ Now: L={float(_vl_rate)*100:+.4f}% S={float(_vs_rate)*100:+.4f}%",
                    extra={"symbol": opp.symbol, "action": "rate_flip_abort"},
                )
                return

        # Acquire lock
        lock_key = f"trade:{opp.symbol}"
        if not await self._redis.acquire_lock(lock_key):
            return

        trade_id = str(uuid.uuid4())[:12]
        try:
            # ── Position sizing ──────────────────────────────────
            sizing = await self._sizer.compute(opp, long_adapter, short_adapter)
            if sizing is None:
                return
            order_qty, notional, long_spec, short_spec = sizing

            # Open both legs

            # Pre-apply trading settings on BOTH exchanges CONCURRENTLY.
            # ensure_trading_settings (margin mode, leverage, position mode) can take
            # 6-8s on slow exchanges (kucoin). Running them in parallel saves up to
            # 8 seconds of entry latency.
            await asyncio.gather(
                long_adapter.ensure_trading_settings(opp.symbol),
                short_adapter.ensure_trading_settings(opp.symbol),
            )

            # Mark grace period BEFORE placing first order
            if self._risk_guard:
                self._risk_guard.mark_trade_opened(opp.symbol)
                logger.info(f"✅ Grace period activated for {opp.symbol} (60s delta skip)")
            
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
                # ── Timeout safety: check if order actually filled on exchange ──
                # asyncio.wait_for cancels the Python coroutine but the HTTP
                # request may have already been sent and the order filled.
                try:
                    _long_positions = await long_adapter.get_positions(opp.symbol)
                    _long_pos = next(
                        (p for p in _long_positions if p.side == OrderSide.BUY),
                        None,
                    )
                except Exception as _lpe:
                    logger.warning(
                        f"[{opp.symbol}] Position check on {opp.long_exchange} "
                        f"after timeout failed: {_lpe}",
                    )
                    _long_pos = None

                if _long_pos and _long_pos.quantity > 0:
                    logger.warning(
                        f"⚠️ [{opp.symbol}] Long order FILLED despite timeout on "
                        f"{opp.long_exchange}: qty={_long_pos.quantity} — "
                        f"closing orphan immediately",
                    )
                    _synth_fill = {
                        "filled": float(_long_pos.quantity),
                        "average": float(_long_pos.entry_price),
                    }
                    await self._close_orphan(
                        long_adapter, opp.long_exchange, opp.symbol,
                        OrderSide.SELL, _synth_fill, _long_pos.quantity,
                    )
                return

            # ── Zero-fill guard: catch orders accepted but not executed ──
            long_raw_filled = float(long_fill.get("filled", 0))
            if long_raw_filled <= 0:
                logger.error(
                    f"❌ [{opp.symbol}] Long ZERO-FILL on {opp.long_exchange}: "
                    f"order accepted but nothing executed (filled={long_raw_filled}). "
                    f"Aborting entry.",
                    extra={"symbol": opp.symbol, "exchange": opp.long_exchange, "action": "zero_fill"},
                )
                await self._redis.set_cooldown(opp.symbol, 300)  # 5 min cooldown
                return

            # Update cached taker_fee from actual fill (real account rate)
            long_adapter.update_taker_fee_from_fill(opp.symbol, long_fill)

            # ── Sync-Fire: adjust short qty to match long's ACTUAL filled qty ──
            long_actual_filled = Decimal(str(long_fill["filled"]))
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
                # ── Timeout safety: check if short actually filled on exchange ──
                # The order may have been submitted and filled even though
                # asyncio.wait_for cancelled the Python coroutine.
                try:
                    _short_positions = await short_adapter.get_positions(opp.symbol)
                    _short_pos = next(
                        (p for p in _short_positions if p.side == OrderSide.SELL),
                        None,
                    )
                except Exception as _spe:
                    logger.warning(
                        f"[{opp.symbol}] Position check on {opp.short_exchange} "
                        f"after timeout failed: {_spe}",
                    )
                    _short_pos = None

                if _short_pos and _short_pos.quantity > 0:
                    # Order DID fill — construct synthetic fill and register trade
                    logger.warning(
                        f"⚠️ [{opp.symbol}] Short order FILLED despite timeout on "
                        f"{opp.short_exchange}: qty={_short_pos.quantity} "
                        f"price={_short_pos.entry_price} — registering trade",
                    )
                    short_fill = {
                        "filled": float(_short_pos.quantity),
                        "average": float(_short_pos.entry_price),
                        "fee": {"cost": None},
                        "_recovered_from_position": True,
                    }
                    # Fall through to trade registration below
                else:
                    # Order truly didn't fill — close orphan long
                    logger.error(
                        f"Short leg failed — closing orphan long for {opp.symbol}"
                    )
                    await self._close_orphan(
                        long_adapter, opp.long_exchange, opp.symbol,
                        OrderSide.SELL, long_fill, long_actual_filled,
                    )
                    return

            # ── Zero-fill guard for short leg ──
            short_raw_filled = float(short_fill.get("filled", 0))
            if short_raw_filled <= 0:
                logger.error(
                    f"❌ [{opp.symbol}] Short ZERO-FILL on {opp.short_exchange}: "
                    f"order accepted but nothing executed (filled={short_raw_filled}). "
                    f"Closing orphan long.",
                    extra={"symbol": opp.symbol, "exchange": opp.short_exchange, "action": "zero_fill"},
                )
                await self._close_orphan(
                    long_adapter, opp.long_exchange, opp.symbol,
                    OrderSide.SELL, long_fill, long_actual_filled,
                )
                return

            # Update cached taker_fee from actual fill (real account rate)
            short_adapter.update_taker_fee_from_fill(opp.symbol, short_fill)

            short_actual_filled = Decimal(str(short_fill["filled"]))
            
            logger.info(
                f"🔓 Trade FULLY OPEN {opp.symbol}: "
                f"LONG({opp.long_exchange})={long_actual_filled} | "
                f"SHORT({opp.short_exchange})={short_actual_filled} — "
                f"Expecting delta=0 in next position fetch"
            )            # Record trade with ACTUAL filled quantities (validated > 0 above)
            long_filled_qty = Decimal(str(long_fill["filled"]))
            short_filled_qty = Decimal(str(short_fill["filled"]))
            entry_price_long = _h.extract_avg_price(long_fill)
            entry_price_short = _h.extract_avg_price(short_fill)

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

            long_spec = await long_adapter.get_instrument_spec(opp.symbol)
            short_spec = await short_adapter.get_instrument_spec(opp.symbol)

            # ── Reconcile entry fees from actual trade data ──────────
            # createOrder response may lack fee data — fetch from trades API
            # for exchange-accurate fee totals.
            _long_oid = long_fill.get("id") if long_fill else None
            _short_oid = short_fill.get("id") if short_fill else None
            _entry_details: list = [None, None]  # [long, short]
            _entry_tasks = []
            _entry_indices: list[int] = []

            if _long_oid:
                _entry_tasks.append(
                    long_adapter.fetch_fill_details_from_trades(
                        opp.symbol, _long_oid,
                    )
                )
                _entry_indices.append(0)
            if _short_oid:
                _entry_tasks.append(
                    short_adapter.fetch_fill_details_from_trades(
                        opp.symbol, _short_oid,
                    )
                )
                _entry_indices.append(1)

            if _entry_tasks:
                _entry_results = await asyncio.gather(
                    *_entry_tasks, return_exceptions=True,
                )
                for idx, res in zip(_entry_indices, _entry_results):
                    if isinstance(res, dict):
                        _entry_details[idx] = res

            # Use actual fees from trades API when available, else estimate
            if _entry_details[0] and _entry_details[0]["total_fee"] > 0:
                entry_fee_long = _entry_details[0]["total_fee"]
            else:
                entry_fee_long = _h.extract_fee(long_fill, long_spec.taker_fee)
            if _entry_details[1] and _entry_details[1]["total_fee"] > 0:
                entry_fee_short = _entry_details[1]["total_fee"]
            else:
                entry_fee_short = _h.extract_fee(short_fill, short_spec.taker_fee)

            entry_fees = entry_fee_long + entry_fee_short

            # Entry price basis: (long_price − short_price) / short_price × 100
            # Positive = long was more expensive than short at entry.
            # This becomes the break-even threshold for exit: exiting at the same
            # spread means zero price loss.
            if entry_price_long and entry_price_short and entry_price_short > 0:
                entry_basis_pct = (entry_price_long - entry_price_short) / entry_price_short * Decimal("100")
            else:
                entry_basis_pct = Decimal("0")

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
                        _trim_raw = float(trim_fill.get("filled", 0))
                        trimmed = Decimal(str(_trim_raw)) if _trim_raw > 0 else excess
                        long_filled_qty -= trimmed
                        trim_fee = _h.extract_fee(trim_fill, long_spec.taker_fee)
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
                        _trim_raw = float(trim_fill.get("filled", 0))
                        trimmed = Decimal(str(_trim_raw)) if _trim_raw > 0 else excess
                        short_filled_qty -= trimmed
                        trim_fee = _h.extract_fee(trim_fill, short_spec.taker_fee)
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
                entry_edge_pct=opp.net_edge_pct,
                long_funding_rate=opp.long_funding_rate,
                short_funding_rate=opp.short_funding_rate,
                entry_price_long=entry_price_long,
                entry_price_short=entry_price_short,
                entry_basis_pct=entry_basis_pct,
                fees_paid_total=entry_fees,
                long_taker_fee=long_spec.taker_fee,
                short_taker_fee=short_spec.taker_fee,
                opened_at=datetime.now(timezone.utc),
                mode=opp.mode,
                exit_before=opp.exit_before,
                entry_tier=opp.entry_tier,
                price_spread_pct=opp.price_spread_pct,
            )
            self._register_trade(trade)
            await self._persist_trade(trade)

            mode_str = f" mode={opp.mode}"
            tier_str = f" tier={opp.entry_tier.upper()}" if opp.entry_tier else ""
            price_spread_str = f" price_spread={float(opp.price_spread_pct):+.4f}%" if opp.price_spread_pct else ""
            if opp.exit_before:
                mode_str += f" exit_before={opp.exit_before.strftime('%H:%M UTC')}"
            if opp.n_collections > 0:
                mode_str += f" collections={opp.n_collections}"

            logger.info(
                f"Trade opened: {trade_id} {opp.symbol} "
                f"L={opp.long_exchange}({long_filled_qty}) "
                f"S={opp.short_exchange}({short_filled_qty}) "
                f"spread={opp.immediate_spread_pct:.4f}% net={opp.net_edge_pct:.4f}%{mode_str}{tier_str}{price_spread_str}",
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
            if opp.mode == TradeMode.CHERRY_PICK:
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
                f"  Tier:      {(opp.entry_tier or 'N/A').upper()} {tier_emoji}\n"
                f"  Reason:    {entry_reason}\n"
                f"  LONG:      {opp.long_exchange} qty={long_filled_qty} @ ${float(entry_price_long or 0):.6f} "
                    f"| funding={lr_pct:+.4f}%\n"
                f"  SHORT:     {opp.short_exchange} qty={short_filled_qty} @ ${float(entry_price_short or 0):.6f} "
                    f"| funding={sr_pct:+.4f}%\n"
                f"  Price Spread: {float(opp.price_spread_pct):+.4f}% "
                    f"({'favorable ✅' if opp.price_spread_pct > 0 else 'adverse ⚠️' if opp.price_spread_pct < 0 else 'neutral'})\n"
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
                spread_pct=opp.immediate_spread_pct, net_pct=opp.net_edge_pct,
                exit_before=opp.exit_before, n_collections=opp.n_collections,
                notional=entry_notional,
                entry_reason=entry_reason,
                exec_latency_ms=_exec_latency_ms,
            )

            # Log balances after trade opened (if enabled)
            if self._cfg.logging.log_balances_after_trade:
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
                self._blacklist.add(opp.symbol, opp.long_exchange)
                self._blacklist.add(opp.symbol, opp.short_exchange)
            logger.error(f"Trade execution failed for {opp.symbol}: {e}",
                         extra={"symbol": opp.symbol})
        finally:
            await self._redis.release_lock(lock_key)

    # ── Exit monitor ─────────────────────────────────────────────

