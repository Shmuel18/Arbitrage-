"""
Entry order execution mixin — extracted from _entry_mixin.py.
Contains _EntryOrdersMixin with _execute_entry_orders().

Do NOT import this module directly; _EntryMixin inherits from it,
and ExecutionController inherits from _EntryMixin.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from src.core.contracts import (
    OpportunityCandidate,
    OrderRequest,
    OrderSide,
    TradeRecord,
)
from src.core.logging import get_logger
from src.execution import helpers as _h

if TYPE_CHECKING:
    pass  # all attribute access via self (mixin pattern)

logger = get_logger("execution")

_ONE: Decimal = Decimal("1")
_FALLBACK_LOT: Decimal = Decimal("0.001")  # last-resort lot step when spec is missing


class _EntryOrdersMixin:
    """Order-placement helpers for trade entry — inherited by _EntryMixin."""

    async def _execute_entry_orders(
        self,
        opp: OpportunityCandidate,
        long_adapter,
        short_adapter,
    ) -> Optional[dict]:
        """Execute sizing, order placement, fill verification, and delta correction.

        Returns None if entry should be aborted; otherwise returns a dict with
        all fill data needed to construct a TradeRecord:
          order_qty, long_filled_qty, short_filled_qty,
          entry_price_long, entry_price_short, entry_fees,
          long_spec, short_spec, entry_basis_pct
        """
        # ── Position sizing ──────────────────────────────────────
        sizing = await self._sizer.compute(opp, long_adapter, short_adapter)
        if sizing is None:
            return None
        order_qty, notional, long_spec, short_spec = sizing

        # ── Pre-apply trading settings on BOTH exchanges CONCURRENTLY ──
        await asyncio.gather(
            long_adapter.ensure_trading_settings(opp.symbol),
            short_adapter.ensure_trading_settings(opp.symbol),
        )
        # ── Pre-entry order book depth + VWAP check (P0-2) ──────────────
        # Placed HERE (after settings, immediately before the first order) to
        # minimise the adverse-selection window: scanner snapshot → settings
        # → FRESH L1 snapshot → first order. The prior location (before sizing
        # and settings) left a 400ms–1.4s gap during which price could move.
        if not await self._check_pre_entry_liquidity(opp, long_adapter, short_adapter, order_qty):
            return None
        # NOTE: grace period (mark_trade_opened) is set AFTER both legs fill
        # — see below the "🔓 Trade FULLY OPEN" log. Setting it here (before any
        # orders) created a 60-second delta-skip window while long was open
        # and short had not yet been placed (P0-2).

        # ── Place long order ─────────────────────────────────────
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
            # asyncio.wait_for may cancel the coroutine but the order could have filled
            try:
                _long_positions = await long_adapter.get_positions(opp.symbol)
                _long_pos = next(
                    (p for p in _long_positions if p.side == OrderSide.BUY), None,
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
            return None

        # ── Zero-fill guard (long) ────────────────────────────────
        long_raw_filled = float(long_fill.get("filled", 0))
        if long_raw_filled <= 0:
            logger.error(
                f"❌ [{opp.symbol}] Long ZERO-FILL on {opp.long_exchange}: "
                f"order accepted but nothing executed (filled={long_raw_filled}). "
                f"Aborting entry.",
                extra={"symbol": opp.symbol, "exchange": opp.long_exchange, "action": "zero_fill"},
            )
            await self._redis.set_cooldown(opp.symbol, 300)
            # P2: Zero-fill is exchange-specific (the long exchange rejected the order);
            # route cooldown prevents retry on this pair, symbol stays open for other routes.
            await self._redis.set_route_cooldown(
                opp.symbol, opp.long_exchange, opp.short_exchange, 300, reason="zero_fill_long",
            )
            return None

        long_adapter.update_taker_fee_from_fill(opp.symbol, long_fill)

        # ── Sync-Fire: adjust short qty to long's ACTUAL filled qty ──
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

        # ── Place short order ─────────────────────────────────────
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
            # Check if short actually filled on exchange despite timeout
            try:
                _short_positions = await short_adapter.get_positions(opp.symbol)
                _short_pos = next(
                    (p for p in _short_positions if p.side == OrderSide.SELL), None,
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
                logger.error(f"Short leg failed — closing orphan long for {opp.symbol}")
                await self._close_orphan(
                    long_adapter, opp.long_exchange, opp.symbol,
                    OrderSide.SELL, long_fill, long_actual_filled,
                )
                # Journal the ghost trade so it appears in history
                _orphan_price = _h.extract_avg_price(long_fill) or opp.reference_price
                _orphan_notional = float(_orphan_price * long_actual_filled) if _orphan_price else 0.0
                _orphan_fee_est = float(long_actual_filled * _orphan_price * Decimal("0.001")) if _orphan_price else 0.0
                self._journal.event(
                    "ghost_trade",
                    symbol=opp.symbol,
                    long_exchange=opp.long_exchange,
                    short_exchange=opp.short_exchange,
                    reason="short_leg_failed",
                    qty=float(long_actual_filled),
                    notional=_orphan_notional,
                    estimated_loss=_orphan_fee_est * 2,
                )
                return None

        # ── Zero-fill guard (short) ───────────────────────────────
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
            return None

        short_adapter.update_taker_fee_from_fill(opp.symbol, short_fill)

        short_actual_filled = Decimal(str(short_fill["filled"]))
        logger.info(
            f"🔓 Trade FULLY OPEN {opp.symbol}: "
            f"LONG({opp.long_exchange})={long_actual_filled} | "
            f"SHORT({opp.short_exchange})={short_actual_filled} — "
            f"Expecting delta=0 in next position fetch"
        )
        # P0-2: Mark grace period only AFTER both legs are confirmed hedged.
        # Activating the grace period before the long order (old location) left
        # the risk guard blind for up to 60 s while a naked long was open (short
        # might still fail or time out). Bilateral hedge is proven at this point.
        if self._risk_guard:
            self._risk_guard.mark_trade_opened(opp.symbol)
            logger.info(
                f"✅ [{opp.symbol}] Grace period activated (60s delta skip) "
                f"— bilateral hedge confirmed"
            )

        # ── Extract fill quantities and prices ────────────────────
        long_filled_qty = Decimal(str(long_fill["filled"]))
        short_filled_qty = Decimal(str(short_fill["filled"]))
        entry_price_long = _h.extract_avg_price(long_fill)
        entry_price_short = _h.extract_avg_price(short_fill)

        # Fallback: if exchange didn't return avg price, use ticker
        if entry_price_long is None:
            try:
                t = await long_adapter.get_ticker(opp.symbol)
                entry_price_long = Decimal(str(t.get("last", 0)))
                logger.info(f"[{opp.symbol}] Long entry price from ticker: {entry_price_long}")
            except Exception as _e:
                logger.debug(f"[{opp.symbol}] Long ticker fallback failed: {_e} — using reference_price")
                entry_price_long = opp.reference_price
        if entry_price_short is None:
            try:
                t = await short_adapter.get_ticker(opp.symbol)
                entry_price_short = Decimal(str(t.get("last", 0)))
                logger.info(f"[{opp.symbol}] Short entry price from ticker: {entry_price_short}")
            except Exception as _e:
                logger.debug(f"[{opp.symbol}] Short ticker fallback failed: {_e} — using reference_price")
                entry_price_short = opp.reference_price

        # Refresh specs (in case cache was stale before)
        long_spec = await long_adapter.get_instrument_spec(opp.symbol)
        short_spec = await short_adapter.get_instrument_spec(opp.symbol)

        # ── Reconcile entry fees from actual trade data ──────────
        _long_oid = long_fill.get("id") if long_fill else None
        _short_oid = short_fill.get("id") if short_fill else None
        _entry_details: list = [None, None]
        _entry_tasks = []
        _entry_indices: list[int] = []

        if _long_oid:
            _entry_tasks.append(
                long_adapter.fetch_fill_details_from_trades(opp.symbol, _long_oid)
            )
            _entry_indices.append(0)
        if _short_oid:
            _entry_tasks.append(
                short_adapter.fetch_fill_details_from_trades(opp.symbol, _short_oid)
            )
            _entry_indices.append(1)

        if _entry_tasks:
            _entry_results = await asyncio.gather(*_entry_tasks, return_exceptions=True)
            for idx, res in zip(_entry_indices, _entry_results):
                if isinstance(res, dict):
                    _entry_details[idx] = res

        if _entry_details[0] and _entry_details[0]["total_fee"] > 0:
            entry_fee_long = _entry_details[0]["total_fee"]
        else:
            entry_fee_long = _h.extract_fee(long_fill, long_spec.taker_fee)
        if _entry_details[1] and _entry_details[1]["total_fee"] > 0:
            entry_fee_short = _entry_details[1]["total_fee"]
        else:
            entry_fee_short = _h.extract_fee(short_fill, short_spec.taker_fee)

        entry_fees = entry_fee_long + entry_fee_short

        # ── Entry price basis ─────────────────────────────────────
        if entry_price_long and entry_price_short and entry_price_short > 0:
            entry_basis_pct = (entry_price_long - entry_price_short) / entry_price_short * Decimal("100")
        else:
            entry_basis_pct = Decimal("0")

        # ── Log partial fill / mismatch summary ──────────────────
        short_partial = short_filled_qty < short_order_qty
        qty_mismatch = long_filled_qty != short_filled_qty
        if is_partial_fill or short_partial or qty_mismatch:
            logger.warning(
                f"📊 [{opp.symbol}] Fill Report: "
                f"Long={long_filled_qty}/{order_qty} "
                f"| Short={short_filled_qty}/{short_order_qty} "
                f"| Mismatch={qty_mismatch} | Fees=${float(entry_fees):.2f}"
            )

        # ── Delta correction: fix unhedged qty from partial fills ──
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
                    # P0-1: Never use `else excess` fallback — a zero-fill on the trim
                    # is a real failure.  Set trimmed=0 so P0-3 residual check aborts.
                    _trim_raw = float(trim_fill.get("filled", 0))
                    trimmed = Decimal(str(_trim_raw))
                    if trimmed > 0:
                        long_filled_qty -= trimmed
                        entry_fees += _h.extract_fee(trim_fill, long_spec.taker_fee)
                        logger.info(
                            f"✅ Delta corrected: trimmed {trimmed} on {opp.long_exchange}, "
                            f"L={long_filled_qty} S={short_filled_qty} now balanced"
                        )
                    else:
                        logger.error(
                            f"❌ DELTA CORRECTION ZERO-FILL for {opp.symbol} — "
                            f"trim order accepted but filled=0 (unhedged {excess} on "
                            f"{opp.long_exchange}) — P0-3 residual check will abort"
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
                    # P0-1: Never use `else excess` fallback — a zero-fill on the trim
                    # is a real failure.  Set trimmed=0 so P0-3 residual check aborts.
                    _trim_raw = float(trim_fill.get("filled", 0))
                    trimmed = Decimal(str(_trim_raw))
                    if trimmed > 0:
                        short_filled_qty -= trimmed
                        entry_fees += _h.extract_fee(trim_fill, short_spec.taker_fee)
                        logger.info(
                            f"✅ Delta corrected: trimmed {trimmed} on {opp.short_exchange}, "
                            f"L={long_filled_qty} S={short_filled_qty} now balanced"
                        )
                    else:
                        logger.error(
                            f"❌ DELTA CORRECTION ZERO-FILL for {opp.symbol} — "
                            f"trim order accepted but filled=0 (unhedged {excess} on "
                            f"{opp.short_exchange}) — P0-3 residual check will abort"
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

        # ── P0-3: Verify delta correction result ─────────────────────────────
        # A partial-fill on the correction order itself can leave a residual
        # imbalance.  If it exceeds half a lot step we cannot safely register
        # the trade — an unhedged position would sail through the 60-second
        # grace period undetected by the risk guard.
        _long_cs = Decimal(str(long_spec.contract_size)) if long_spec and long_spec.contract_size else _ONE
        _short_cs = Decimal(str(short_spec.contract_size)) if short_spec and short_spec.contract_size else _ONE
        _long_lot_base = Decimal(str(long_spec.lot_size)) * _long_cs if long_spec else _FALLBACK_LOT
        _short_lot_base = Decimal(str(short_spec.lot_size)) * _short_cs if short_spec else _FALLBACK_LOT
        _lot = max(_long_lot_base, _short_lot_base)
        post_correction_residual = abs(long_filled_qty - short_filled_qty)
        if post_correction_residual > _lot * Decimal("0.5"):
            logger.error(
                f"❌ [{opp.symbol}] Residual delta {post_correction_residual} after correction "
                f"(L={long_filled_qty} S={short_filled_qty}, threshold={_lot * Decimal('0.5')}) — "
                f"aborting trade registration and emergency-closing both legs",
                extra={"symbol": opp.symbol, "action": "residual_delta_abort"},
            )
            # Emergency close: unwind whichever leg is larger
            _failed: list = []  # initialise before try so the cooldown calc always has a value
            try:
                close_tasks = []
                if long_filled_qty > 0:
                    close_tasks.append(
                        self._place_with_timeout(
                            long_adapter,
                            OrderRequest(
                                exchange=opp.long_exchange,
                                symbol=opp.symbol,
                                side=OrderSide.SELL,
                                quantity=long_filled_qty,
                                reduce_only=True,
                            ),
                        )
                    )
                if short_filled_qty > 0:
                    close_tasks.append(
                        self._place_with_timeout(
                            short_adapter,
                            OrderRequest(
                                exchange=opp.short_exchange,
                                symbol=opp.symbol,
                                side=OrderSide.BUY,
                                quantity=short_filled_qty,
                                reduce_only=True,
                            ),
                        )
                    )
                if close_tasks:
                    _close_results = await asyncio.gather(*close_tasks, return_exceptions=True)
                    _failed = [r for r in _close_results if isinstance(r, Exception) or r is None]
                    if _failed:
                        logger.error(
                            f"❌ [{opp.symbol}] Emergency unwind FAILED ({len(_failed)} legs) — "
                            f"MANUAL INTERVENTION REQUIRED"
                        )
                    else:
                        logger.info(
                            f"✅ [{opp.symbol}] Emergency unwind after residual delta: both legs closed"
                        )
            except Exception as _unwind_err:
                logger.error(
                    f"❌ [{opp.symbol}] Emergency unwind ERROR: {_unwind_err} — "
                    f"MANUAL INTERVENTION REQUIRED"
                )
                _failed = [True]  # treat exception as failure for cooldown below

            # P1-3: Apply a much longer cooldown when the emergency close itself
            # failed — the position may still be open on the exchange with no
            # trade record in _active_trades.  24 h forces manual review;
            # 300 s is only appropriate when the unwind confirmed success.
            _cooldown_secs = 86400 if _failed else 300
            await self._redis.set_cooldown(opp.symbol, _cooldown_secs)
            # P2: Also set route cooldown with same duration.
            # The 86400 s (24 h) case means positions may still be open on this route —
            # block re-entry on this specific pair until manual review.
            await self._redis.set_route_cooldown(
                opp.symbol, opp.long_exchange, opp.short_exchange,
                _cooldown_secs,
                reason="residual_delta_unwind_failed" if _failed else "residual_delta",
            )
            return None

        # If after correction both legs are zero, abort trade
        if long_filled_qty <= 0 or short_filled_qty <= 0:
            logger.error(f"❌ [{opp.symbol}] No viable position after fills — aborting trade")
            return None

        return {
            "order_qty": order_qty,
            "long_filled_qty": long_filled_qty,
            "short_filled_qty": short_filled_qty,
            "entry_price_long": entry_price_long,
            "entry_price_short": entry_price_short,
            "entry_fees": entry_fees,
            "long_spec": long_spec,
            "short_spec": short_spec,
            "entry_basis_pct": entry_basis_pct,
        }
