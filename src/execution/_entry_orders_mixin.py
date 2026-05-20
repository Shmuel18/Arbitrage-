"""
Entry order-execution mixin — order-placement helpers split out of
_entry_mixin.py to keep each file under the size limit.

Do NOT import this module directly; ``_EntryMixin`` inherits from
``_EntryOrdersMixin``, and ``ExecutionController`` inherits from ``_EntryMixin``.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from src.core.contracts import OrderRequest, OrderSide
from src.core.logging import get_logger
from src.execution import helpers as _h

if TYPE_CHECKING:
    from src.core.contracts import OpportunityCandidate

logger = get_logger("execution")

_ONE: Decimal = Decimal("1")
_FALLBACK_LOT: Decimal = Decimal("0.001")  # last-resort lot step when spec is missing
_ENTRY_LIQUIDITY_TIMEOUT_SEC: float = 3.0  # bound the pre-entry order-book depth check


class _EntryOrdersMixin:
    """Order-placement helpers for trade entry — inherited by _EntryMixin."""

    async def _check_pre_entry_liquidity(
        self,
        opp: "OpportunityCandidate",
        long_adapter,
        short_adapter,
        order_qty: Decimal,
    ) -> bool:
        """Fresh order-book depth gate, run immediately before placing orders.

        Returns False (skip entry) if either leg's live book cannot absorb
        ``order_qty`` in one shot, or if the depth fetch errors/times out.
        Prevention at entry is cheaper than a post-fill emergency unwind (2× fees).
        The long leg is a BUY (walks asks); the short leg is a SELL (walks bids).
        """
        try:
            results = await asyncio.wait_for(
                asyncio.gather(
                    long_adapter.get_vwap_and_depth(opp.symbol, order_qty, side="buy"),
                    short_adapter.get_vwap_and_depth(opp.symbol, order_qty, side="sell"),
                    return_exceptions=True,
                ),
                timeout=_ENTRY_LIQUIDITY_TIMEOUT_SEC,
            )
        except asyncio.TimeoutError:
            logger.warning(
                f"⏱️ [{opp.symbol}] Pre-entry depth check timed out "
                f"({_ENTRY_LIQUIDITY_TIMEOUT_SEC}s) — skipping entry (fail-closed)",
                extra={"symbol": opp.symbol, "action": "pre_entry_liquidity_timeout"},
            )
            return False

        for r in results:
            if isinstance(r, Exception):
                logger.warning(
                    f"🚫 [{opp.symbol}] Pre-entry depth check failed ({r}) — "
                    f"skipping entry (fail-closed)",
                    extra={"symbol": opp.symbol, "action": "pre_entry_liquidity_error"},
                )
                return False

        (_, long_ok), (_, short_ok) = results
        if not (long_ok and short_ok):
            logger.warning(
                f"🚫 [{opp.symbol}] Insufficient book depth for qty={order_qty} "
                f"(long_book_ok={long_ok}, short_book_ok={short_ok}) — skipping entry",
                extra={"symbol": opp.symbol, "action": "pre_entry_liquidity_thin"},
            )
            return False
        return True

    async def _abort_on_residual_delta(
        self,
        opp: "OpportunityCandidate",
        long_adapter,
        short_adapter,
        long_filled_qty: Decimal,
        short_filled_qty: Decimal,
        long_spec,
        short_spec,
        tp,
    ) -> bool:
        """Emergency-close both legs if the delta correction left a residual
        imbalance larger than half a lot step, and set a cooldown.

        Returns True if the trade was aborted (caller must stop), else False.
        An unhedged residual would otherwise pass the 60s risk-guard grace
        period undetected.
        """
        _long_cs = Decimal(str(long_spec.contract_size)) if long_spec and long_spec.contract_size else _ONE
        _short_cs = Decimal(str(short_spec.contract_size)) if short_spec and short_spec.contract_size else _ONE
        _long_lot_base = Decimal(str(long_spec.lot_size)) * _long_cs if long_spec else _FALLBACK_LOT
        _short_lot_base = Decimal(str(short_spec.lot_size)) * _short_cs if short_spec else _FALLBACK_LOT
        _lot = max(_long_lot_base, _short_lot_base)
        post_correction_residual = abs(long_filled_qty - short_filled_qty)
        if post_correction_residual <= _lot * Decimal("0.5"):
            return False

        logger.error(
            f"❌ [{opp.symbol}] Residual delta {post_correction_residual} after correction "
            f"(L={long_filled_qty} S={short_filled_qty}, threshold={_lot * Decimal('0.5')}) — "
            f"aborting trade registration and emergency-closing both legs",
            extra={"symbol": opp.symbol, "action": "residual_delta_abort"},
        )
        _failed: list = []
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
            _failed = [True]

        # 24h cooldown when the unwind itself failed — a position may still be
        # open on the exchange with no trade record, so force manual review.
        _cooldown_secs = 86400 if _failed else tp.cooldown_after_close_seconds
        await self._redis.set_cooldown(opp.symbol, _cooldown_secs)
        await self._redis.set_route_cooldown(
            opp.symbol, opp.long_exchange, opp.short_exchange,
            _cooldown_secs,
            reason="residual_delta_unwind_failed" if _failed else "residual_delta",
        )
        return True

    async def _reject_on_adverse_basis(
        self,
        opp: "OpportunityCandidate",
        long_adapter,
        short_adapter,
        long_filled_qty: Decimal,
        short_filled_qty: Decimal,
        entry_basis_pct: Decimal,
        trade_id: str,
        tp,
    ) -> bool:
        """Post-fill basis sanity check. Thin books drift in the seconds between
        the scan classification (bid/ask) and the fill. If the realized entry
        basis is adverse beyond ``max_entry_basis_spread_pct``, close both legs
        before registering and enter cooldown. Returns True if rejected.

        Convention: entry_basis_pct = (long - short)/short — positive = adverse
        (long paid more than short received). Only adverse drift is rejected;
        favorable (negative) fills are kept.
        """
        _max_basis = tp.max_entry_basis_spread_pct
        if not (_max_basis > 0 and entry_basis_pct > _max_basis):
            return False

        logger.error(
            f"🚨 [{opp.symbol}] Post-fill basis check FAILED: "
            f"actual_basis={float(entry_basis_pct):+.4f}% > "
            f"max={float(_max_basis):.4f}% "
            f"(scan-classified tier={opp.entry_tier}, "
            f"scan price_spread={float(opp.price_spread_pct):+.4f}%) — "
            f"closing both legs reduce-only and entering cooldown.",
            extra={"trade_id": trade_id, "symbol": opp.symbol,
                   "action": "post_fill_basis_abort",
                   "entry_basis_pct": float(entry_basis_pct),
                   "max_allowed_pct": float(_max_basis)},
        )
        await asyncio.gather(
            self._close_orphan(
                long_adapter, opp.long_exchange, opp.symbol,
                OrderSide.SELL,
                {"filled": float(long_filled_qty)},
                long_filled_qty,
            ),
            self._close_orphan(
                short_adapter, opp.short_exchange, opp.symbol,
                OrderSide.BUY,
                {"filled": float(short_filled_qty)},
                short_filled_qty,
            ),
            return_exceptions=True,
        )
        await self._redis.set_cooldown(
            opp.symbol, tp.cooldown_after_close_seconds,
        )
        if self._publisher:
            try:
                await self._publisher.publish_alert(
                    (
                        f"🚨 Post-fill basis abort: {opp.symbol} "
                        f"basis={float(entry_basis_pct):+.4f}% > "
                        f"max={float(_max_basis):.4f}% — both legs closed."
                    ),
                    severity="warning",
                    alert_type="post_fill_basis_abort",
                    symbol=opp.symbol,
                    payload={
                        "trade_id": trade_id,
                        "scan_tier": opp.entry_tier,
                        "scan_price_spread_pct": float(opp.price_spread_pct),
                        "actual_basis_pct": float(entry_basis_pct),
                        "max_allowed_pct": float(_max_basis),
                    },
                )
            except Exception as _alert_exc:
                logger.debug(
                    f"[{opp.symbol}] post-fill abort alert failed: {_alert_exc}",
                )
        return True

    async def _reconcile_and_correct_fills(
        self,
        opp: "OpportunityCandidate",
        long_adapter,
        short_adapter,
        long_fill: dict,
        short_fill: dict,
        long_filled_qty: Decimal,
        short_filled_qty: Decimal,
        order_qty: Decimal,
        short_order_qty: Decimal,
        is_partial_fill: bool,
        entry_price_long: Optional[Decimal],
        entry_price_short: Optional[Decimal],
        long_spec,
        short_spec,
    ) -> tuple:
        """Reconcile entry fees from the trades API, compute the entry basis, and
        correct any leg imbalance from partial fills (trim the larger leg).

        Returns ``(long_filled_qty, short_filled_qty, entry_fees, entry_basis_pct)``
        with the possibly-trimmed quantities and accumulated fees.
        """
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

        # ── Delta correction: fix unhedged exposure from a partial fill ──
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
                    # Never assume the trim filled `excess` — a zero/partial fill
                    # is a real failure. Use the ACTUAL filled qty so the P0-3
                    # residual check below catches a still-unhedged position.
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
                            f"trim accepted but filled=0 (unhedged {excess} on "
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
                    # Never assume the trim filled `excess` — a zero/partial fill
                    # is a real failure. Use the ACTUAL filled qty so the P0-3
                    # residual check below catches a still-unhedged position.
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
                            f"trim accepted but filled=0 (unhedged {excess} on "
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

        return long_filled_qty, short_filled_qty, entry_fees, entry_basis_pct
