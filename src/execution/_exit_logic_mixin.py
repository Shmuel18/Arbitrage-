"""Exit-decision mixin -- basis-recovery exit strategy.

Exit rules (in priority order):
  1. LIQUIDATION SAFETY: if either side approaches liquidation -> exit immediately
  2. PROFIT TARGET:      profit_target_pct on notional -> exit (always, even before funding)
  3. CHERRY_PICK HARD:   exit BEFORE costly funding payment
  4. BASIS RECOVERY:     after funding is collected, exit when the cross-exchange
                         price basis (long-short)/short returns to entry level or
                         better (within tolerance).
  5. BASIS HARD STOP:    if basis doesn't recover within basis_recovery_timeout_minutes
                         (default 30min), exit immediately.
  6. TIME-BASED:         if exit_timeout_hours after funding payment and no basis
                         recovery, check if NEXT IMMINENT funding qualifies.

IMPORTANT: The bot always evaluates only the NEXT upcoming funding payment.
Both at entry (scanner) AND while holding (exit logic), the decision is based
solely on the imminent payment within max_entry_window_minutes.

Do NOT import this module directly; _MonitorMixin inherits from it,
and ExecutionController inherits from _MonitorMixin.
"""
from __future__ import annotations

import asyncio
import time as _time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Dict, Optional

from src.core.contracts import ExitReason, TradeMode, TradeRecord
from src.core.logging import get_logger
from src.execution._exit_computations_mixin import _ExitComputationsMixin

_ZERO = Decimal("0")

if TYPE_CHECKING:
    pass  # all attribute access via self (mixin pattern)

logger = get_logger("execution")


def _funding_ts_to_dt(ts: float) -> "datetime":
    """Convert an exchange funding timestamp (ms or s) to a UTC-aware datetime.

    P2-3 / P3-1: Some exchanges deliver epoch-seconds (~1.7×10⁹) instead of
    epoch-milliseconds (~1.7×10¹²).  Normalise to ms before converting so
    that monitor-cycle comparisons produce correct (future) datetimes.
    """
    ms = ts * 1000 if ts < 1e12 else ts
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


class _ExitLogicMixin(_ExitComputationsMixin):
    """Hold-or-exit decision logic — tier-aware profit-target strategy."""

    async def _check_exit(self, trade: TradeRecord) -> None:
        """Check if trade should be closed.

        Strategy (funding arb + price arb tiers):
          1. Liquidation safety: exit if margin ratio drops too low
          2. Cherry-pick hard stop: exit before costly payment
          3. Profit target: exit at profit_target_pct (0.7%) on notional
          4. Basis recovery: exit when price spread returns to entry level
          5. Basis hard stop: exit after timeout if no recovery
          6. Time-based: if next funding qualifies → stay, else → exit
        """
        # P0-1: Mark that _check_exit is active (persists until explicitly reset
        # in the "stay next cycle" branch or until _check_upgrade in the following
        # cycle reads it). This allows _check_upgrade to distinguish "funding just
        # fired, not yet processed" (False) from "already recorded this payment
        # in a prior cycle" (True), preventing upgrades before PnL is accounted.
        trade._exit_check_active = True

        now = datetime.now(timezone.utc)
        tp = self._cfg.trading_params

        # ── Get adapters ─────────────────────────────────────────
        long_adapter = self._exchanges.get(trade.long_exchange)
        short_adapter = self._exchanges.get(trade.short_exchange)
        if not long_adapter or not short_adapter:
            logger.warning(f"Missing adapter for {trade.symbol}, skipping exit check")
            return

        # ── 1. LIQUIDATION CHECK ─────────────────────────────────
        liquidation_exit = await self._check_liquidation_risk(trade, long_adapter, short_adapter)
        if liquidation_exit:
            return

        # ── MIN HOLD GUARD ───────────────────────────────────────
        # Prevents immediate exit caused by stale exchange timestamps setting
        # next_funding_long to a past value → long_paid=True on first cycle.
        hold_sec = (now - trade.opened_at).total_seconds() if trade.opened_at else 0
        if hold_sec < tp.min_hold_seconds:
            return

        # ── 2. CHERRY_PICK HARD STOP ─────────────────────────────
        if trade.mode == TradeMode.CHERRY_PICK and trade.exit_before:
            if now >= trade.exit_before:
                logger.info(
                    f"🍒 Cherry-pick hard exit for {trade.trade_id}: "
                    f"exiting before costly payment at {trade.exit_before.strftime('%H:%M UTC')}",
                    extra={"trade_id": trade.trade_id, "symbol": trade.symbol, "action": "exit_signal"},
                )
                trade._exit_reason = "cherry_hard_stop"
                await self._close_trade(trade)
                return

        # ── Calculate current P&L on notional ────────────────────
        pnl_info = await self._calculate_current_pnl(trade, long_adapter, short_adapter)
        if pnl_info is None:
            return  # couldn't calculate, skip this cycle

        total_pnl_pct = pnl_info["total_pnl_pct"]
        price_pnl_pct = pnl_info["price_pnl_pct"]
        funding_pnl_pct = pnl_info["funding_pnl_pct"]
        fees_pct_val = pnl_info["fees_pct"]
        l_price = pnl_info["long_price"]
        s_price = pnl_info["short_price"]

        # ── Track funding payment status ─────────────────────────
        long_funding = long_adapter.get_funding_rate_cached(trade.symbol)
        short_funding = short_adapter.get_funding_rate_cached(trade.symbol)

        if long_funding:
            long_next_ts = long_funding.get("next_timestamp")
            if long_next_ts:
                candidate = _funding_ts_to_dt(long_next_ts)
                if not trade.next_funding_long:
                    # At initialization: only accept FUTURE timestamps.
                    # Stale exchanges sometimes return an already-past timestamp
                    # immediately after a funding event; accepting it would cause
                    # long_paid=True on the very first monitor cycle → immediate exit.
                    if candidate > now:
                        trade.next_funding_long = candidate
                elif (
                    trade.next_funding_long < now and not trade._funding_paid_long
                    and candidate > trade.next_funding_long
                ):
                    # Advance to a strictly newer timestamp only.
                    trade.next_funding_long = candidate

        if short_funding:
            short_next_ts = short_funding.get("next_timestamp")
            if short_next_ts:
                candidate = _funding_ts_to_dt(short_next_ts)
                if not trade.next_funding_short:
                    # At initialization: only accept FUTURE timestamps (same reason as above).
                    if candidate > now:
                        trade.next_funding_short = candidate
                elif (
                    trade.next_funding_short < now and not trade._funding_paid_short
                    and candidate > trade.next_funding_short
                ):
                    # Advance to a strictly newer timestamp only.
                    trade.next_funding_short = candidate

        exit_offset = tp.exit_offset_seconds
        long_paid = False
        short_paid = False
        if trade.next_funding_long:
            long_exit_time = trade.next_funding_long + timedelta(seconds=exit_offset)
            long_paid = now >= long_exit_time
        if trade.next_funding_short:
            short_exit_time = trade.next_funding_short + timedelta(seconds=exit_offset)
            short_paid = now >= short_exit_time

        long_just_paid = long_paid and not trade._funding_paid_long
        short_just_paid = short_paid and not trade._funding_paid_short

        # ── Handle funding settlement (delegate to computation mixin) ─
        closed = await self._process_funding_settlement(
            trade, long_adapter, short_adapter,
            long_just_paid, short_just_paid, total_pnl_pct, now,
        )
        if closed:
            return

        # ── Display current status ───────────────────────────────
        hold_min = int((now - trade.opened_at).total_seconds() / 60) if trade.opened_at else 0
        tier_tag = f" [{trade.entry_tier.upper()}]" if trade.entry_tier else ""

        if not trade._hold_logged_until or trade._hold_logged_until < now:
            trade._hold_logged_until = now + timedelta(minutes=5)
            logger.info(
                f"📊 {trade.symbol}{tier_tag}: PnL={float(total_pnl_pct):+.4f}% "
                f"(price={float(price_pnl_pct):+.4f}% funding={float(funding_pnl_pct):+.4f}% "
                f"fees=-{float(fees_pct_val):.4f}%) | "
                f"held {hold_min}min | collections={trade.funding_collections} | "
                f"target={float(tp.profit_target_pct)}%",
                extra={"trade_id": trade.trade_id, "symbol": trade.symbol, "action": "pnl_status"},
            )
            try:
                _long_pnl_usd = float((l_price - (trade.entry_price_long or l_price)) * trade.long_qty)
                _short_pnl_usd = float(((trade.entry_price_short or s_price) - s_price) * trade.short_qty)
                _price_pnl_usd = _long_pnl_usd + _short_pnl_usd
                _min_since = int((now - trade._funding_paid_at).total_seconds() / 60) if trade._funding_paid_at else hold_min
                self._journal.position_snapshot(
                    trade.trade_id, trade.symbol,
                    minutes_since_funding=_min_since,
                    long_exchange=trade.long_exchange,
                    short_exchange=trade.short_exchange,
                    long_price=float(l_price),
                    short_price=float(s_price),
                    immediate_spread=float(total_pnl_pct),
                    long_pnl_usd=_long_pnl_usd,
                    short_pnl_usd=_short_pnl_usd,
                    price_pnl_usd=_price_pnl_usd,
                    funding_collected_usd=float(trade.funding_collected_usd),
                )
            except Exception as _snap_err:
                logger.debug(f"Snapshot failed for {trade.symbol}: {_snap_err}")

        # ── 3. PROFIT TARGET CHECK ───────────────────────────────
        profit_target = tp.profit_target_pct
        adjusted_pnl = total_pnl_pct - tp.exit_slippage_buffer_pct
        if adjusted_pnl >= profit_target:
            # P1-1: Block profit-target exit when funding payment is imminent.
            # A price pump just before funding would cause us to exit and miss
            # the income payment we opened specifically to capture.
            _PROFIT_TARGET_FUNDING_LOCK_MIN: float = 3.0
            _mins_to_next_income: float | None = None
            if trade.next_funding_long and not long_paid:
                _mf = (trade.next_funding_long - now).total_seconds() / 60
                if _mf > 0:
                    _mins_to_next_income = _mf
            if trade.next_funding_short and not short_paid:
                _mf = (trade.next_funding_short - now).total_seconds() / 60
                if _mf > 0 and (_mins_to_next_income is None or _mf < _mins_to_next_income):
                    _mins_to_next_income = _mf

            if _mins_to_next_income is not None and _mins_to_next_income < _PROFIT_TARGET_FUNDING_LOCK_MIN:
                logger.info(
                    f"[{trade.symbol}] Profit target hit ({float(adjusted_pnl):+.4f}%) "
                    f"but funding in {_mins_to_next_income:.1f}min < lock={_PROFIT_TARGET_FUNDING_LOCK_MIN}min "
                    f"— holding to capture income payment",
                    extra={"trade_id": trade.trade_id, "symbol": trade.symbol, "action": "profit_target_funding_lock"},
                )
            else:
                _reason = f"profit_target_{float(total_pnl_pct):.4f}pct"
                logger.info(
                    f"🎯 Trade {trade.trade_id}{tier_tag}: PROFIT TARGET HIT! "
                    f"PnL={float(total_pnl_pct):+.4f}% (adj={float(adjusted_pnl):+.4f}%) "
                    f">= {float(profit_target)}% target "
                    f"(price={float(price_pnl_pct):+.4f}% funding={float(funding_pnl_pct):+.4f}% "
                    f"slippage_buf=-{float(tp.exit_slippage_buffer_pct):.4f}%) — "
                    f"exiting after {hold_min}min",
                    extra={"trade_id": trade.trade_id, "symbol": trade.symbol, "action": "profit_target_exit"},
                )
                trade._exit_reason = _reason
                self._journal.exit_decision(
                    trade.trade_id, trade.symbol,
                    reason=_reason,
                    immediate_spread=Decimal(str(total_pnl_pct)),
                    hold_min=hold_min,
                )
                await self._close_trade(trade)
                return

        # ── 4. BASIS RECOVERY EXIT (after funding) ──────────────
        if not (long_paid or short_paid):
            return

        _entry_basis = trade.entry_basis_pct if trade.entry_basis_pct is not None else _ZERO
        _current_basis = _ZERO
        if l_price > 0 and s_price > 0:
            _current_basis = (l_price - s_price) / s_price * Decimal("100")

        _tolerance = tp.basis_recovery_tolerance_pct
        _basis_favorable = _current_basis >= (_entry_basis - _tolerance)

        if _basis_favorable:
            _reason = f"basis_recovery_{float(_current_basis):+.4f}pct"
            logger.info(
                f"✅ Trade {trade.trade_id}{tier_tag}: BASIS RECOVERED! "
                f"entry_basis={float(_entry_basis):+.4f}% → current={float(_current_basis):+.4f}% "
                f"(recovered ✔, tolerance={float(_tolerance):.2f}%) | "
                f"PnL={float(total_pnl_pct):+.4f}% — exiting after {hold_min}min",
                extra={"trade_id": trade.trade_id, "symbol": trade.symbol, "action": "basis_recovery_exit"},
            )
            trade._exit_reason = _reason
            self._journal.exit_decision(
                trade.trade_id, trade.symbol,
                reason=_reason,
                immediate_spread=Decimal(str(total_pnl_pct)),
                hold_min=hold_min,
            )
            await self._close_trade(trade)
            return

        # ── 5. BASIS HARD STOP (30min timeout) ───────────────────
        basis_timeout_min = float(tp.basis_recovery_timeout_minutes)  # P2-2: direct field access
        time_since_funding_min = 0.0
        if trade._funding_paid_at:
            time_since_funding_min = (now - trade._funding_paid_at).total_seconds() / 60

        if time_since_funding_min >= basis_timeout_min:
            next_cycle_ok = await self._next_funding_qualifies(trade, long_adapter, short_adapter)

            if next_cycle_ok:
                logger.info(
                    f"🔄 Trade {trade.trade_id}{tier_tag}: basis timeout {basis_timeout_min:.0f}min reached "
                    f"(basis: entry={float(_entry_basis):+.4f}% current={float(_current_basis):+.4f}%), "
                    f"BUT next funding qualifies — staying (collections={trade.funding_collections})",
                    extra={"trade_id": trade.trade_id, "symbol": trade.symbol, "action": "stay_next_cycle"},
                )
                # Reset flags for next funding cycle
                trade._exit_check_active = False
                trade._funding_paid_long = False
                trade._funding_paid_short = False
                trade._funding_paid_at = None
                trade._hold_logged_until = None

                # Advance funding trackers to next cycle.
                # P1-2: Only accept timestamps strictly in the future.
                # A stale cache returning an already-past timestamp would set
                # next_funding_long to the past → long_paid=True on the very
                # next monitor cycle → double-fire of _process_funding_settlement
                # with Δ=0 (no new exchange payment), inflating collection counter.
                if long_funding:
                    _ln = long_funding.get("next_timestamp")
                    if _ln:
                        _ln_dt = _funding_ts_to_dt(_ln)
                        if _ln_dt > now:
                            trade.next_funding_long = _ln_dt
                        else:
                            logger.warning(
                                f"[{trade.symbol}] Stale long next_timestamp in cache "
                                f"({_ln_dt.strftime('%H:%M:%S')} <= now) — "
                                f"not advancing tracker to avoid double-fire",
                                extra={"trade_id": trade.trade_id},
                            )
                if short_funding:
                    _sn = short_funding.get("next_timestamp")
                    if _sn:
                        _sn_dt = _funding_ts_to_dt(_sn)
                        if _sn_dt > now:
                            trade.next_funding_short = _sn_dt
                        else:
                            logger.warning(
                                f"[{trade.symbol}] Stale short next_timestamp in cache "
                                f"({_sn_dt.strftime('%H:%M:%S')} <= now) — "
                                f"not advancing tracker to avoid double-fire",
                                extra={"trade_id": trade.trade_id},
                            )

                # Update stored rates for next cycle
                if long_funding and "rate" in long_funding:
                    trade.long_funding_rate = Decimal(str(long_funding["rate"]))
                if short_funding and "rate" in short_funding:
                    trade.short_funding_rate = Decimal(str(short_funding["rate"]))
                return
            else:
                _reason = f"basis_hard_stop_{basis_timeout_min:.0f}min"
                logger.info(
                    f"⏱️ Trade {trade.trade_id}{tier_tag}: BASIS HARD STOP — "
                    f"{basis_timeout_min:.0f}min since funding, basis NOT recovered "
                    f"(entry={float(_entry_basis):+.4f}% current={float(_current_basis):+.4f}%) "
                    f"and next funding doesn't qualify — closing after {hold_min}min",
                    extra={"trade_id": trade.trade_id, "symbol": trade.symbol, "action": "basis_hard_stop_exit"},
                )
                trade._exit_reason = _reason
                self._journal.exit_decision(
                    trade.trade_id, trade.symbol,
                    reason=_reason,
                    immediate_spread=Decimal(str(total_pnl_pct)),
                    hold_min=hold_min,
                )
                await self._close_trade(trade)
                return

        # ── Not yet at basis timeout — keep holding ──────────────
        if trade._funding_paid_at and not trade._hold_logged_until:
            logger.info(
                f"⏳ Trade {trade.trade_id}{tier_tag}: waiting for basis recovery "
                f"(entry={float(_entry_basis):+.4f}% current={float(_current_basis):+.4f}% "
                f"Δ={float(_current_basis - _entry_basis):+.4f}%) — "
                f"{time_since_funding_min:.0f}/{basis_timeout_min:.0f}min elapsed",
                extra={"trade_id": trade.trade_id, "symbol": trade.symbol, "action": "waiting_basis_recovery"},
            )
