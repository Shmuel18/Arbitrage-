"""Exit-decision mixin -- basis-recovery exit strategy.

Exit rules (in priority order):
  1. LIQUIDATION SAFETY: if either side approaches liquidation -> exit immediately
  2. PROFIT TARGET:      profit_target_pct on notional -> exit (always, even before funding)
  3. CHERRY_PICK HARD:   exit BEFORE costly funding payment
  4. BASIS RECOVERY:     after funding is collected, exit when the cross-exchange
                         price basis (long-short)/short returns to entry level or
                         better (favorable).  This ensures we don't give back
                         funding profits to adverse price movements.
  5. BASIS HARD STOP:    if basis doesn't recover within basis_recovery_timeout_minutes
                         (default 30min), exit immediately -- don't hold indefinitely.
  6. TIME-BASED:         if exit_timeout_hours after funding payment and no basis
                         recovery, check if NEXT IMMINENT funding qualifies.
                         If yes -> stay for next cycle, else -> exit.

IMPORTANT: The bot always evaluates only the NEXT upcoming funding payment.
Both at entry (scanner) AND while holding (exit logic), the decision is based
solely on the imminent payment within max_entry_window_minutes.  The bot never
stays in a trade waiting hours for a distant payment.

Do NOT import this module directly; _MonitorMixin inherits from it,
and ExecutionController inherits from _MonitorMixin.
"""
from __future__ import annotations

import asyncio
import time as _time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from src.core.contracts import ExitReason, TradeMode, TradeRecord
from src.core.logging import get_logger
from src.discovery.calculator import calculate_fees

_ZERO = Decimal("0")

if TYPE_CHECKING:
    pass  # all attribute access via self (mixin pattern)

logger = get_logger("execution")


class _ExitLogicMixin:
    """Hold-or-exit decision logic — tier-aware profit-target strategy."""

    async def _check_exit(self, trade: TradeRecord) -> None:
        """Check if trade should be closed.

        New strategy (funding arb + price arb tiers):
          1. Liquidation safety: exit if margin ratio drops too low
          2. Cherry-pick hard stop: exit before costly payment
          3. Profit target: exit at profit_target_pct (0.7%) on notional
          4. Time-based: if exit_timeout_hours after funding -> check next cycle
        """
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

        # Update next funding trackers
        if long_funding:
            long_next_ts = long_funding.get("next_timestamp")
            if long_next_ts:
                candidate = datetime.fromtimestamp(long_next_ts / 1000, tz=timezone.utc)
                if not trade.next_funding_long or (
                    trade.next_funding_long < now and not trade._funding_paid_long
                    and candidate <= now
                ):
                    trade.next_funding_long = candidate

        if short_funding:
            short_next_ts = short_funding.get("next_timestamp")
            if short_next_ts:
                candidate = datetime.fromtimestamp(short_next_ts / 1000, tz=timezone.utc)
                if not trade.next_funding_short or (
                    trade.next_funding_short < now and not trade._funding_paid_short
                    and candidate <= now
                ):
                    trade.next_funding_short = candidate

        # Check if funding has been paid
        exit_offset = tp.exit_offset_seconds
        long_paid = False
        short_paid = False
        if trade.next_funding_long:
            long_exit_time = trade.next_funding_long + timedelta(seconds=exit_offset)
            long_paid = now >= long_exit_time
        if trade.next_funding_short:
            short_exit_time = trade.next_funding_short + timedelta(seconds=exit_offset)
            short_paid = now >= short_exit_time

        # Record funding collection — track each side independently
        # so staggered payments (cost side fires after income side)
        # are properly detected instead of blocked by _exit_check_active.
        long_just_paid = long_paid and not trade._funding_paid_long
        short_just_paid = short_paid and not trade._funding_paid_short

        if long_just_paid or short_just_paid:
            trade._exit_check_active = True
            if long_just_paid:
                trade._funding_paid_long = True
            if short_just_paid:
                trade._funding_paid_short = True

            # Track funding payment
            _live_long = long_adapter.get_funding_rate_cached(trade.symbol)
            _live_short = short_adapter.get_funding_rate_cached(trade.symbol)

            _lr = (
                Decimal(str(_live_long["rate"])) if (_live_long and long_just_paid and "rate" in _live_long)
                else (trade.long_funding_rate if long_just_paid else None)
            )
            _sr = (
                Decimal(str(_live_short["rate"])) if (_live_short and short_just_paid and "rate" in _live_short)
                else (trade.short_funding_rate if short_just_paid else None)
            )

            _long_usd = ((trade.entry_price_long or _ZERO) * trade.long_qty * (-(Decimal(str(_lr or 0))))) if _lr else _ZERO
            _short_usd = ((trade.entry_price_short or _ZERO) * trade.short_qty * (Decimal(str(_sr or 0)))) if _sr else _ZERO
            _net_usd = _long_usd + _short_usd

            trade.funding_collections += 1
            trade.funding_collected_usd += _net_usd
            trade._funding_paid_at = now

            # Journal entries
            if long_just_paid and _lr:
                self._journal.funding_detected(
                    trade.trade_id, trade.symbol, trade.long_exchange, 'long',
                    rate=_lr, estimated_payment=_long_usd,
                )
            if short_just_paid and _sr:
                self._journal.funding_detected(
                    trade.trade_id, trade.symbol, trade.short_exchange, 'short',
                    rate=_sr, estimated_payment=_short_usd,
                )
            self._journal.funding_collected(
                trade.trade_id, trade.symbol,
                collection_num=trade.funding_collections,
                long_exchange=trade.long_exchange,
                short_exchange=trade.short_exchange,
                long_rate=_lr, short_rate=_sr,
                long_payment_usd=_long_usd, short_payment_usd=_short_usd,
                net_payment_usd=_net_usd, cumulative_usd=float(trade.funding_collected_usd),
                immediate_spread=float(total_pnl_pct),
            )
            logger.info(
                f"💰 [{trade.symbol}] Funding collection #{trade.funding_collections}: "
                f"~${_net_usd:.4f} this cycle | cumulative ~${float(trade.funding_collected_usd):.4f}",
                extra={"trade_id": trade.trade_id, "symbol": trade.symbol, "action": "funding_collected"},
            )

            # ── NEGATIVE FUNDING GUARD ───────────────────────────────
            # Check CUMULATIVE funding, not just this payment.
            # For CHERRY picks the cost side may fire AFTER income;
            # for NUTCRACKERs with staggered payments, cumulative is
            # positive when net direction is correct.
            _cumulative = float(trade.funding_collected_usd)
            if _cumulative < 0:
                logger.warning(
                    f"🚨 [{trade.symbol}] NEGATIVE cumulative funding "
                    f"${_cumulative:.4f} — direction is wrong! "
                    f"Exiting immediately.",
                    extra={
                        "trade_id": trade.trade_id,
                        "symbol": trade.symbol,
                        "action": "negative_funding_exit",
                    },
                )
                trade._exit_reason = "negative_funding"
                _hold_min_neg = int((now - trade.opened_at).total_seconds() / 60) if trade.opened_at else 0
                self._journal.exit_decision(
                    trade.trade_id, trade.symbol,
                    reason=f"negative_funding_{_cumulative:.4f}",
                    immediate_spread=Decimal(str(total_pnl_pct)),
                    hold_min=_hold_min_neg,
                )
                await self._close_trade(trade)
                return

        # ── Display current status ───────────────────────────────
        hold_min = int((now - trade.opened_at).total_seconds() / 60) if trade.opened_at else 0
        tier_tag = f" [{trade.entry_tier.upper()}]" if trade.entry_tier else ""

        # Log status periodically (every 5 min)
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
            # Position snapshot for journal
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
        # Deduct exit slippage buffer — ticker prices overestimate
        # realisable PnL on illiquid coins because actual fills are
        # worse than top-of-book bid/ask.
        adjusted_pnl = total_pnl_pct - tp.exit_slippage_buffer_pct
        if adjusted_pnl >= profit_target:
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
        # After funding is collected, exit when the cross-exchange price
        # basis returns to entry level or better.  If it doesn't recover
        # within basis_recovery_timeout_minutes, force exit.
        if not (long_paid or short_paid):
            return

        # Calculate current price basis: (long − short) / short × 100
        _entry_basis = trade.entry_basis_pct if trade.entry_basis_pct is not None else _ZERO
        _current_basis = _ZERO
        if l_price > 0 and s_price > 0:
            _current_basis = (l_price - s_price) / s_price * Decimal("100")

        # Favorable = current basis ≤ entry basis (spread narrowed or reversed)
        # entry_basis is (long−short)/short at entry.  If it shrinks, we profit.
        _basis_favorable = _current_basis <= _entry_basis

        if _basis_favorable:
            _reason = f"basis_recovery_{float(_current_basis):+.4f}pct"
            logger.info(
                f"✅ Trade {trade.trade_id}{tier_tag}: BASIS RECOVERED! "
                f"entry_basis={float(_entry_basis):+.4f}% → current={float(_current_basis):+.4f}% "
                f"(favorable ✔) | PnL={float(total_pnl_pct):+.4f}% — exiting after {hold_min}min",
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
        basis_timeout_min = float(getattr(tp, 'basis_recovery_timeout_minutes', 30))
        time_since_funding_min = 0.0
        if trade._funding_paid_at:
            time_since_funding_min = (now - trade._funding_paid_at).total_seconds() / 60

        if time_since_funding_min >= basis_timeout_min:
            # Check if next funding cycle qualifies before giving up
            next_cycle_ok = await self._next_funding_qualifies(trade, long_adapter, short_adapter)

            if next_cycle_ok:
                # Stay for next cycle — reset timer
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

                # Advance funding trackers to next cycle
                if long_funding:
                    _ln = long_funding.get("next_timestamp")
                    if _ln:
                        trade.next_funding_long = datetime.fromtimestamp(_ln / 1000, tz=timezone.utc)
                if short_funding:
                    _sn = short_funding.get("next_timestamp")
                    if _sn:
                        trade.next_funding_short = datetime.fromtimestamp(_sn / 1000, tz=timezone.utc)

                # Update stored rates for next cycle
                if long_funding and "rate" in long_funding:
                    trade.long_funding_rate = Decimal(str(long_funding["rate"]))
                if short_funding and "rate" in short_funding:
                    trade.short_funding_rate = Decimal(str(short_funding["rate"]))
                return
            else:
                # Basis didn't recover + no next funding → force exit
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
        # Log that we're waiting for basis recovery
        if trade._funding_paid_at and not trade._hold_logged_until:
            logger.info(
                f"⏳ Trade {trade.trade_id}{tier_tag}: waiting for basis recovery "
                f"(entry={float(_entry_basis):+.4f}% current={float(_current_basis):+.4f}% "
                f"Δ={float(_current_basis - _entry_basis):+.4f}%) — "
                f"{time_since_funding_min:.0f}/{basis_timeout_min:.0f}min elapsed",
                extra={"trade_id": trade.trade_id, "symbol": trade.symbol, "action": "waiting_basis_recovery"},
            )

    # ── P&L Calculation ──────────────────────────────────────────

    async def _calculate_current_pnl(self, trade: TradeRecord, long_adapter, short_adapter) -> Optional[dict]:
        """Calculate current total P&L as percentage of one-side notional.

        Returns dict with:
          total_pnl_pct:   funding + price - fees (% of notional)
          price_pnl_pct:   unrealized price P&L (%)
          funding_pnl_pct: funding collected (%)
          fees_pct:        total fees (%)
          long_price:      current long price
          short_price:     current short price
        """
        try:
            l_ticker = await long_adapter.get_ticker(trade.symbol)
            s_ticker = await short_adapter.get_ticker(trade.symbol)
            # Long exit = sell at BID; short exit = buy at ASK.
            # Using last/close would overestimate PnL vs actual market impact.
            l_price = Decimal(str(
                l_ticker.get("bid") or l_ticker.get("last") or l_ticker.get("close") or 0
            ))
            s_price = Decimal(str(
                s_ticker.get("ask") or s_ticker.get("last") or s_ticker.get("close") or 0
            ))
        except Exception as e:
            logger.debug(f"Price fetch failed for {trade.symbol}: {e}")
            return None

        if l_price <= 0 or s_price <= 0:
            return None

        entry_long = trade.entry_price_long or l_price
        entry_short = trade.entry_price_short or s_price

        # One-side notional at entry (for % calculation)
        notional = entry_long * trade.long_qty
        if notional <= 0:
            return None

        # Price P&L: long gains when price rises, short gains when price drops
        long_price_pnl = (l_price - entry_long) * trade.long_qty
        short_price_pnl = (entry_short - s_price) * trade.short_qty
        total_price_pnl = long_price_pnl + short_price_pnl
        price_pnl_pct = total_price_pnl / notional * Decimal("100")

        # Funding P&L (accumulated)
        funding_pnl_pct = trade.funding_collected_usd / notional * Decimal("100") if trade.funding_collected_usd else Decimal("0")

        # Fees (entry fees already paid)
        total_fees = trade.fees_paid_total or Decimal("0")
        fees_pct = total_fees / notional * Decimal("100") if total_fees else Decimal("0")

        # Estimate exit fees (not yet incurred, but will be paid when closing).
        # Without this, the profit target fires ~0.2% too early, causing
        # trades that look profitable to close at a loss after the real
        # exit fees are charged.
        exit_fees_est = Decimal("0")
        if trade.long_taker_fee:
            exit_fees_est += l_price * trade.long_qty * trade.long_taker_fee
        if trade.short_taker_fee:
            exit_fees_est += s_price * trade.short_qty * trade.short_taker_fee
        exit_fees_pct = exit_fees_est / notional * Decimal("100") if exit_fees_est else Decimal("0")

        # Total P&L = price + funding - entry_fees - estimated_exit_fees
        total_pnl_pct = price_pnl_pct + funding_pnl_pct - fees_pct - exit_fees_pct

        return {
            "total_pnl_pct": total_pnl_pct,
            "price_pnl_pct": price_pnl_pct,
            "funding_pnl_pct": funding_pnl_pct,
            "fees_pct": fees_pct,
            "long_price": l_price,
            "short_price": s_price,
        }

    # ── Next Funding Check ───────────────────────────────────────

    async def _next_funding_qualifies(self, trade: TradeRecord, long_adapter, short_adapter) -> bool:
        """Check if the NEXT IMMINENT funding payment justifies staying.

        Applies the SAME entry-window rules as the scanner:
          1. Classify each side as income or cost
          2. Check if any INCOME side fires within max_entry_window_minutes
          3. Compute imminent net spread (income minus cost that also fires)
          4. Net must exceed min_funding_spread after fees

        This ensures the bot never waits hours for a distant payment.
        """
        tp = self._cfg.trading_params

        long_funding = long_adapter.get_funding_rate_cached(trade.symbol)
        short_funding = short_adapter.get_funding_rate_cached(trade.symbol)
        if not long_funding or not short_funding:
            return False

        long_rate = Decimal(str(long_funding["rate"]))
        short_rate = Decimal(str(short_funding["rate"]))

        # ── Entry window (same as scanner) ───────────────────────
        entry_window_min = float(tp.max_entry_window_minutes)
        now_ms = _time.time() * 1000

        long_next_ts = long_funding.get("next_timestamp")
        short_next_ts = short_funding.get("next_timestamp")

        # Classify each side: income or cost?
        long_is_income = long_rate < 0   # long on negative → we get paid
        short_is_income = short_rate > 0  # short on positive → we get paid

        # Minutes until each side's next funding
        long_mins = (long_next_ts - now_ms) / 60_000 if (long_next_ts and long_next_ts > now_ms) else None
        short_mins = (short_next_ts - now_ms) / 60_000 if (short_next_ts and short_next_ts > now_ms) else None

        # Is each income side within the entry window?
        long_imminent = long_is_income and long_mins is not None and long_mins <= entry_window_min
        short_imminent = short_is_income and short_mins is not None and short_mins <= entry_window_min

        # ── Gate: at least one INCOME side must be imminent ──────
        if not (long_imminent or short_imminent):
            _next_income_mins = None
            if long_is_income and long_mins is not None:
                _next_income_mins = long_mins
            if short_is_income and short_mins is not None:
                if _next_income_mins is None or short_mins < _next_income_mins:
                    _next_income_mins = short_mins
            logger.info(
                f"🔍 [{trade.symbol}] Next income payment too far: "
                f"{int(_next_income_mins)}min" if _next_income_mins is not None else "unknown"
                f" > entry_window={int(entry_window_min)}min — EXIT",
                extra={"trade_id": trade.trade_id, "symbol": trade.symbol},
            )
            return False

        # ── Compute imminent spread (income minus cost within window) ──
        _HUNDRED = Decimal("100")
        imminent_income_pct = Decimal("0")
        imminent_cost_pct = Decimal("0")
        if long_imminent:
            imminent_income_pct += abs(long_rate) * _HUNDRED
        if short_imminent:
            imminent_income_pct += abs(short_rate) * _HUNDRED
        # Cost sides that also fire within the window
        if not long_is_income and long_mins is not None and long_mins <= entry_window_min:
            imminent_cost_pct += abs(long_rate) * _HUNDRED
        if not short_is_income and short_mins is not None and short_mins <= entry_window_min:
            imminent_cost_pct += abs(short_rate) * _HUNDRED
        imminent_spread_pct = imminent_income_pct - imminent_cost_pct

        # ── Fees ─────────────────────────────────────────────────
        long_spec = long_adapter.get_cached_instrument_spec(trade.symbol)
        short_spec = short_adapter.get_cached_instrument_spec(trade.symbol)
        if not long_spec or not short_spec:
            return False

        fees_pct = calculate_fees(long_spec.taker_fee, short_spec.taker_fee)
        # We're already in the trade so entry fees are sunk.
        # Only exit fees matter, but for consistency with the scanner's
        # qualification gate we compare imminent spread vs min_funding_spread.
        net_spread = imminent_spread_pct - fees_pct

        qualifies = net_spread >= tp.min_funding_spread

        _result = "✅ STAY" if qualifies else "❌ EXIT"
        _income_detail = []
        if long_imminent:
            _income_detail.append(f"L({trade.long_exchange})={float(long_rate)*100:+.4f}% in {int(long_mins)}min")
        if short_imminent:
            _income_detail.append(f"S({trade.short_exchange})={float(short_rate)*100:+.4f}% in {int(short_mins)}min")
        logger.info(
            f"🔍 [{trade.symbol}] Next funding check (entry_window={int(entry_window_min)}min): "
            f"{' | '.join(_income_detail)} "
            f"→ imminent_spread={float(imminent_spread_pct):.4f}% net={float(net_spread):.4f}% "
            f"(need {float(tp.min_funding_spread)}%) → {_result}",
            extra={"trade_id": trade.trade_id, "symbol": trade.symbol},
        )
        return qualifies

    # ── Liquidation Risk Check ───────────────────────────────────

    async def _check_liquidation_risk(self, trade: TradeRecord, long_adapter, short_adapter) -> bool:
        """Check if either side is approaching liquidation.

        Uses unrealized PnL / position margin as a rough margin ratio.
        Returns True if trade was closed due to liquidation risk.
        """
        safety_pct = float(self._cfg.trading_params.liquidation_safety_pct)

        try:
            # Fetch positions from both exchanges
            long_positions = await long_adapter.get_positions(trade.symbol)
            short_positions = await short_adapter.get_positions(trade.symbol)

            for positions, exchange, side in [
                (long_positions, trade.long_exchange, "LONG"),
                (short_positions, trade.short_exchange, "SHORT"),
            ]:
                for pos in positions:
                    if pos.symbol != trade.symbol:
                        continue
                    # Estimate margin ratio: (equity / margin) * 100
                    # equity = margin + unrealized_pnl
                    # margin = entry_price * qty / leverage
                    leverage = pos.leverage or 5
                    margin = float(pos.entry_price * pos.quantity) / leverage if pos.entry_price > 0 else 0
                    if margin <= 0:
                        continue
                    equity = margin + float(pos.unrealized_pnl)
                    margin_ratio = (equity / margin) * 100

                    if margin_ratio < safety_pct:
                        logger.warning(
                            f"🚨 LIQUIDATION RISK: {trade.symbol} {side} on {exchange} — "
                            f"margin_ratio={margin_ratio:.1f}% < safety={safety_pct}% "
                            f"(equity=${equity:.2f}, margin=${margin:.2f}, uPnL=${float(pos.unrealized_pnl):.2f})",
                            extra={
                                "trade_id": trade.trade_id,
                                "symbol": trade.symbol,
                                "action": "liquidation_risk_exit",
                            },
                        )
                        trade._exit_reason = ExitReason.LIQUIDATION_RISK.value
                        hold_min = int((datetime.now(timezone.utc) - trade.opened_at).total_seconds() / 60) if trade.opened_at else 0
                        self._journal.exit_decision(
                            trade.trade_id, trade.symbol,
                            reason=f"liquidation_risk_{side.lower()}_{exchange}_ratio_{margin_ratio:.1f}pct",
                            immediate_spread=Decimal("0"),
                            hold_min=hold_min,
                        )
                        await self._close_trade(trade)
                        return True
        except Exception as e:
            logger.debug(f"Liquidation check failed for {trade.symbol}: {e}")

        return False

