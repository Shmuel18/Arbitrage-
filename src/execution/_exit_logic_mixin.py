"""
Exit-decision mixin — tier-aware profit-target exit strategy.

Exit rules (in priority order):
  1. LIQUIDATION SAFETY: if either side approaches liquidation -> exit immediately
  2. PROFIT TARGET:      0.7% profit on notional (= 0.5% net + 0.2% slippage buffer) -> exit
  3. CHERRY_PICK HARD:   exit BEFORE costly funding payment
  4. TIME-BASED:         if 1.5h after funding payment and no profit target met:
                         check if next funding qualifies -> yes=stay, no=exit

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
                trade._exit_reason = ExitReason.SPREAD_BELOW_THRESHOLD.value
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

        # Record funding collection (once per cycle)
        if (long_paid or short_paid) and not trade._exit_check_active:
            trade._exit_check_active = True
            if long_paid:
                trade._funding_paid_long = True
            if short_paid:
                trade._funding_paid_short = True

            # Track funding payment
            _live_long = long_adapter.get_funding_rate_cached(trade.symbol)
            _live_short = short_adapter.get_funding_rate_cached(trade.symbol)

            _lr = (
                Decimal(str(_live_long["rate"])) if (_live_long and long_paid and "rate" in _live_long)
                else (trade.long_funding_rate if long_paid else None)
            )
            _sr = (
                Decimal(str(_live_short["rate"])) if (_live_short and short_paid and "rate" in _live_short)
                else (trade.short_funding_rate if short_paid else None)
            )

            _long_usd = float((trade.entry_price_long or Decimal('0')) * trade.long_qty * (-(Decimal(str(_lr or 0))))) if _lr else 0
            _short_usd = float((trade.entry_price_short or Decimal('0')) * trade.short_qty * (Decimal(str(_sr or 0)))) if _sr else 0
            _net_usd = _long_usd + _short_usd

            trade.funding_collections += 1
            trade.funding_collected_usd += Decimal(str(_net_usd))
            trade._funding_paid_at = now

            # Journal entries
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
            # If the funding payment we just received was NEGATIVE (we paid
            # instead of receiving), the trade direction is wrong. Exit
            # immediately rather than holding 1.5h for a profit target that
            # likely won't be reached.
            if _net_usd < 0:
                logger.warning(
                    f"🚨 [{trade.symbol}] NEGATIVE funding ${_net_usd:.4f} — "
                    f"direction was wrong! Exiting immediately.",
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
                    reason=f"negative_funding_{_net_usd:.4f}",
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
        if total_pnl_pct >= profit_target:
            _reason = f"profit_target_{float(total_pnl_pct):.4f}pct"
            logger.info(
                f"🎯 Trade {trade.trade_id}{tier_tag}: PROFIT TARGET HIT! "
                f"PnL={float(total_pnl_pct):+.4f}% >= {float(profit_target)}% target "
                f"(price={float(price_pnl_pct):+.4f}% funding={float(funding_pnl_pct):+.4f}%) — "
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

        # ── 4. TIME-BASED EXIT (after funding) ──────────────────
        # Only evaluate after funding has been collected
        if not (long_paid or short_paid):
            return

        # Check how long since funding was paid
        timeout_hours = float(tp.exit_timeout_hours)
        time_since_funding = 0.0
        if trade._funding_paid_at:
            time_since_funding = (now - trade._funding_paid_at).total_seconds() / 3600

        if time_since_funding >= timeout_hours:
            # Timeout reached — check if next funding cycle qualifies
            next_cycle_ok = await self._next_funding_qualifies(trade, long_adapter, short_adapter)

            if next_cycle_ok:
                # Stay for next cycle — reset timer
                logger.info(
                    f"🔄 Trade {trade.trade_id}{tier_tag}: {timeout_hours}h timeout reached "
                    f"(PnL={float(total_pnl_pct):+.4f}%), BUT next funding qualifies — "
                    f"staying for next cycle (collections={trade.funding_collections})",
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
                # Next cycle doesn't qualify — exit
                _reason = f"exit_timeout_{timeout_hours}h_no_next_funding"
                logger.info(
                    f"⏱️ Trade {trade.trade_id}{tier_tag}: EXIT — {timeout_hours}h timeout "
                    f"after funding, PnL={float(total_pnl_pct):+.4f}% (below {float(profit_target)}% target), "
                    f"next funding does NOT qualify — closing after {hold_min}min",
                    extra={"trade_id": trade.trade_id, "symbol": trade.symbol, "action": "timeout_exit"},
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

        # ── Not yet at timeout — keep holding ────────────────────
        # (Profit target check already ran above and didn't trigger)

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
            l_price = Decimal(str(l_ticker.get("last") or l_ticker.get("close") or 0))
            s_price = Decimal(str(s_ticker.get("last") or s_ticker.get("close") or 0))
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

        # Fees (total paid)
        total_fees = trade.fees_paid_total or Decimal("0")
        fees_pct = total_fees / notional * Decimal("100") if total_fees else Decimal("0")

        # Total P&L = price + funding - fees
        total_pnl_pct = price_pnl_pct + funding_pnl_pct - fees_pct

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
        """Check if the NEXT funding cycle still has profitable conditions.

        Returns True if the spread from the next funding payment
        would still be above min_funding_spread after fees.
        """
        tp = self._cfg.trading_params

        long_funding = long_adapter.get_funding_rate_cached(trade.symbol)
        short_funding = short_adapter.get_funding_rate_cached(trade.symbol)
        if not long_funding or not short_funding:
            return False

        long_rate = Decimal(str(long_funding["rate"]))
        short_rate = Decimal(str(short_funding["rate"]))

        # Immediate spread from next cycle's rates
        immediate_spread = (-long_rate + short_rate) * Decimal("100")

        # Fees
        long_spec = long_adapter.get_cached_instrument_spec(trade.symbol)
        short_spec = short_adapter.get_cached_instrument_spec(trade.symbol)
        if not long_spec or not short_spec:
            return False

        fees_pct = calculate_fees(long_spec.taker_fee, short_spec.taker_fee)
        net_spread = immediate_spread - fees_pct

        qualifies = net_spread >= tp.min_funding_spread

        # ── hold_max_wait_seconds: reject if next funding is too far away ──
        # Staying exposed to price risk for hours while waiting for the next
        # funding is not worth it. Only stay if the next payment is within
        # hold_max_wait_seconds.
        if qualifies and tp.hold_max_wait_seconds > 0:
            now_ms = _time.time() * 1000
            _ln_ts = long_funding.get("next_timestamp")
            _sn_ts = short_funding.get("next_timestamp")
            _next_ts = None
            if _ln_ts is not None and _sn_ts is not None:
                _next_ts = min(_ln_ts, _sn_ts)
            elif _ln_ts is not None:
                _next_ts = _ln_ts
            elif _sn_ts is not None:
                _next_ts = _sn_ts
            if _next_ts is not None:
                secs_until = (_next_ts - now_ms) / 1000
                if secs_until > tp.hold_max_wait_seconds:
                    qualifies = False
                    logger.info(
                        f"🔍 [{trade.symbol}] Next funding too far: "
                        f"{int(secs_until)}s > hold_max_wait={tp.hold_max_wait_seconds}s — EXIT",
                        extra={"trade_id": trade.trade_id, "symbol": trade.symbol},
                    )

        _result = "✅ STAY" if qualifies else "❌ EXIT"
        logger.info(
            f"🔍 [{trade.symbol}] Next funding check: "
            f"L={float(long_rate)*100:+.4f}% S={float(short_rate)*100:+.4f}% "
            f"→ spread={float(immediate_spread):.4f}% net={float(net_spread):.4f}% "
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

