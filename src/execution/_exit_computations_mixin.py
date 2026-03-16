"""Exit computation helpers — extracted from _exit_logic_mixin.py.

Contains:
  _ExitComputationsMixin — funding settlement, P&L calc, next-funding check, liquidation check

Do NOT import this module directly; _ExitLogicMixin inherits from it.
"""
from __future__ import annotations

import asyncio
import time as _time
from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Dict, Optional

from src.core.contracts import ExitReason, TradeMode, TradeRecord
from src.core.logging import get_logger
from src.discovery.calculator import calculate_fees

_ZERO = Decimal("0")
_HUNDRED = Decimal("100")

if TYPE_CHECKING:
    pass  # all attribute access via self (mixin pattern)

logger = get_logger("execution")


class _ExitComputationsMixin:
    """Computation helpers for exit decisions — inherited by _ExitLogicMixin."""

    async def _process_funding_settlement(
        self,
        trade: TradeRecord,
        long_adapter,
        short_adapter,
        long_just_paid: bool,
        short_just_paid: bool,
        total_pnl_pct: Decimal,
        now: datetime,
    ) -> bool:
        """Handle funding payment detection, settlement recording and journal entries.

        Returns True if the trade was closed (negative funding guard triggered).
        """
        if not (long_just_paid or short_just_paid):
            return False

        trade._exit_check_active = True
        if long_just_paid:
            trade._funding_paid_long = True
        if short_just_paid:
            trade._funding_paid_short = True

        # ── Fetch ACTUAL funding settlement from exchange ─────────
        _long_usd = _ZERO
        _short_usd = _ZERO
        _lr: Optional[Decimal] = None
        _sr: Optional[Decimal] = None
        _funding_source = "estimate"

        _opened_ms = int(trade.opened_at.timestamp() * 1000) if trade.opened_at else None
        _now_ms = int(now.timestamp() * 1000)

        _real_long_hist: Optional[Dict] = None
        _real_short_hist: Optional[Dict] = None

        _funding_fetch_tasks = []
        _funding_fetch_sides: list[str] = []
        if long_just_paid and _opened_ms:
            _funding_fetch_tasks.append(
                long_adapter.fetch_funding_history(trade.symbol, _opened_ms, _now_ms)
            )
            _funding_fetch_sides.append("long")
        if short_just_paid and _opened_ms:
            _funding_fetch_tasks.append(
                short_adapter.fetch_funding_history(trade.symbol, _opened_ms, _now_ms)
            )
            _funding_fetch_sides.append("short")

        if _funding_fetch_tasks:
            _results = await asyncio.gather(*_funding_fetch_tasks, return_exceptions=True)
            for _side, _res in zip(_funding_fetch_sides, _results):
                if _side == "long":
                    _real_long_hist = _res if isinstance(_res, dict) else None
                else:
                    _real_short_hist = _res if isinstance(_res, dict) else None

        # Parse actual long-side settlement
        _long_actual_used = False
        if (
            long_just_paid
            and isinstance(_real_long_hist, dict)
            and _real_long_hist.get("source") == "exchange"
            and _real_long_hist.get("payments")
        ):
            _total_long_hist = Decimal(str(_real_long_hist["net_usd"]))
            _prev_long_sum = trade._actual_long_funding_sum
            _long_usd = _total_long_hist - _prev_long_sum
            trade._actual_long_funding_sum = _total_long_hist
            _long_actual_used = True
            _funding_source = "exchange"
            _cached_long = long_adapter.get_funding_rate_cached(trade.symbol)
            _lr = (
                Decimal(str(_cached_long["rate"]))
                if (_cached_long and _cached_long.get("rate") is not None)
                else trade.long_funding_rate
            )
            logger.info(
                f"[{trade.symbol}] Long funding from exchange history: "
                f"${float(_long_usd):.4f} (total hist=${float(_total_long_hist):.4f})",
                extra={"trade_id": trade.trade_id, "symbol": trade.symbol},
            )

        # Parse actual short-side settlement
        _short_actual_used = False
        if (
            short_just_paid
            and isinstance(_real_short_hist, dict)
            and _real_short_hist.get("source") == "exchange"
            and _real_short_hist.get("payments")
        ):
            _total_short_hist = Decimal(str(_real_short_hist["net_usd"]))
            _prev_short_sum = trade._actual_short_funding_sum
            _short_usd = _total_short_hist - _prev_short_sum
            trade._actual_short_funding_sum = _total_short_hist
            _short_actual_used = True
            _funding_source = "exchange"
            _cached_short = short_adapter.get_funding_rate_cached(trade.symbol)
            _sr = (
                Decimal(str(_cached_short["rate"]))
                if (_cached_short and _cached_short.get("rate") is not None)
                else trade.short_funding_rate
            )
            logger.info(
                f"[{trade.symbol}] Short funding from exchange history: "
                f"${float(_short_usd):.4f} (total hist=${float(_total_short_hist):.4f})",
                extra={"trade_id": trade.trade_id, "symbol": trade.symbol},
            )

        # ── Sign validation ──────────────────────────────────────
        _long_rate_for_sign = float(trade.long_funding_rate or 0)
        _short_rate_for_sign = float(trade.short_funding_rate or 0)

        if _long_actual_used and _long_usd != _ZERO and abs(_long_rate_for_sign) > 0.00005:
            _expect_long_positive = _long_rate_for_sign < 0
            if (_long_usd > _ZERO) != _expect_long_positive:
                logger.warning(
                    f"[{trade.symbol}] Funding sign correction on "
                    f"{trade.long_exchange} (long): API=${float(_long_usd):+.4f} "
                    f"vs entry_rate={_long_rate_for_sign:.6f} — flipping sign",
                    extra={"trade_id": trade.trade_id},
                )
                _long_usd = -_long_usd
                # P2-1: Do NOT negate the cumulative tracker — that corrupts the delta
                # used on the NEXT payment: _long_usd_n = total_n - prev_sum, and
                # negating prev_sum doubles the base, inflating all future deltas.
                # Instead, reset to zero so the next payment starts from scratch.
                trade._actual_long_funding_sum = _ZERO
                logger.warning(
                    f"[{trade.symbol}] Funding long tracker reset after sign correction "
                    f"(prev_sum invalidated)",
                    extra={"trade_id": trade.trade_id},
                )

        if _short_actual_used and _short_usd != _ZERO and abs(_short_rate_for_sign) > 0.00005:
            _expect_short_positive = _short_rate_for_sign > 0
            if (_short_usd > _ZERO) != _expect_short_positive:
                logger.warning(
                    f"[{trade.symbol}] Funding sign correction on "
                    f"{trade.short_exchange} (short): API=${float(_short_usd):+.4f} "
                    f"vs entry_rate={_short_rate_for_sign:.6f} — flipping sign",
                    extra={"trade_id": trade.trade_id},
                )
                _short_usd = -_short_usd
                # P2-1: Same fix as long side — reset rather than negate the cumulative.
                trade._actual_short_funding_sum = _ZERO
                logger.warning(
                    f"[{trade.symbol}] Funding short tracker reset after sign correction "
                    f"(prev_sum invalidated)",
                    extra={"trade_id": trade.trade_id},
                )

        # ── Cross-check: compare exchange amount vs rate-based estimate ──
        if _long_actual_used and abs(_long_rate_for_sign) > 0.00005:
            _l_est = float((trade.entry_price_long or _ZERO) * trade.long_qty) * (-_long_rate_for_sign)
            if abs(_l_est) > 0.01 and abs(float(_long_usd) - _l_est) / abs(_l_est) > 0.5:
                logger.warning(
                    f"[{trade.symbol}] Long funding cross-check MISMATCH: "
                    f"exchange=${float(_long_usd):+.4f} vs rate_estimate=${_l_est:+.4f} "
                    f"(>{50}% deviation) — rate may have changed since entry",
                    extra={"trade_id": trade.trade_id},
                )
        if _short_actual_used and abs(_short_rate_for_sign) > 0.00005:
            _s_est = float((trade.entry_price_short or _ZERO) * trade.short_qty) * _short_rate_for_sign
            if abs(_s_est) > 0.01 and abs(float(_short_usd) - _s_est) / abs(_s_est) > 0.5:
                logger.warning(
                    f"[{trade.symbol}] Short funding cross-check MISMATCH: "
                    f"exchange=${float(_short_usd):+.4f} vs rate_estimate=${_s_est:+.4f} "
                    f"(>{50}% deviation) — rate may have changed since entry",
                    extra={"trade_id": trade.trade_id},
                )

        # Fallback to rate-based estimate if exchange history unavailable
        if long_just_paid and not _long_actual_used:
            _lr = trade.long_funding_rate
            _long_usd = (
                (trade.entry_price_long or _ZERO) * trade.long_qty
                * (-(Decimal(str(_lr or 0))))
            ) if _lr else _ZERO
            logger.warning(
                f"[{trade.symbol}] Long funding estimated from entry rate: "
                f"rate={float(_lr or 0):.6f} → ${float(_long_usd):+.4f} "
                f"(exchange history unavailable)",
                extra={"trade_id": trade.trade_id, "symbol": trade.symbol},
            )

        if short_just_paid and not _short_actual_used:
            _sr = trade.short_funding_rate
            _short_usd = (
                (trade.entry_price_short or _ZERO) * trade.short_qty
                * (Decimal(str(_sr or 0)))
            ) if _sr else _ZERO
            logger.warning(
                f"[{trade.symbol}] Short funding estimated from entry rate: "
                f"rate={float(_sr or 0):.6f} → ${float(_short_usd):+.4f} "
                f"(exchange history unavailable)",
                extra={"trade_id": trade.trade_id, "symbol": trade.symbol},
            )

        _net_usd = _long_usd + _short_usd
        trade.funding_collections += 1
        trade.funding_collected_usd += _net_usd
        trade._funding_tracked_long += _long_usd
        trade._funding_tracked_short += _short_usd
        trade._funding_paid_at = now

        # Journal entries
        if long_just_paid:
            self._journal.funding_detected(
                trade.trade_id, trade.symbol, trade.long_exchange, 'long',
                rate=_lr, estimated_payment=_long_usd,
            )
        if short_just_paid:
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
            f"💰 [{trade.symbol}] Funding collection #{trade.funding_collections} "
            f"(source={_funding_source}): "
            f"${float(_net_usd):.4f} this cycle | cumulative ~${float(trade.funding_collected_usd):.4f}",
            extra={"trade_id": trade.trade_id, "symbol": trade.symbol, "action": "funding_collected"},
        )

        # ── NEGATIVE FUNDING GUARD ────────────────────────────────
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
            return True

        return False

    async def _calculate_current_pnl(self, trade: TradeRecord, long_adapter, short_adapter) -> Optional[dict]:
        """Calculate current total P&L as percentage of one-side notional.

        Returns dict with:
          total_pnl_pct:   funding + price - fees (% of notional)
          price_pnl_pct:   unrealized price P&L (%)
          funding_pnl_pct: funding collected (%)
          fees_pct:        total fees (%)
          long_price:      current long price (VWAP-adjusted)
          short_price:     current short price (VWAP-adjusted)
        """
        def _to_decimal_price(raw_price: object) -> Optional[Decimal]:
            if isinstance(raw_price, Decimal):
                return raw_price
            if isinstance(raw_price, (int, float, str)):
                try:
                    return Decimal(str(raw_price))
                except Exception:
                    return None
            return None

        l_price: Optional[Decimal] = None
        s_price: Optional[Decimal] = None

        # Preferred path: VWAP executable prices.
        try:
            raw_l_price, raw_s_price = await asyncio.gather(
                long_adapter.get_executable_price(
                    trade.symbol, trade.long_qty, side="sell"
                ),
                short_adapter.get_executable_price(
                    trade.symbol, trade.short_qty, side="buy"
                ),
            )
            l_price = _to_decimal_price(raw_l_price)
            s_price = _to_decimal_price(raw_s_price)
        except Exception as e:
            logger.debug(f"Executable price fetch failed for {trade.symbol}: {e}")

        # Fallback path (tests / adapters without order-book pricing): ticker last.
        if l_price is None or s_price is None:
            try:
                long_ticker, short_ticker = await asyncio.gather(
                    long_adapter.get_ticker(trade.symbol),
                    short_adapter.get_ticker(trade.symbol),
                )
                if l_price is None:
                    l_price = _to_decimal_price(long_ticker.get("last"))
                if s_price is None:
                    s_price = _to_decimal_price(short_ticker.get("last"))
            except Exception as e:
                logger.debug(f"Ticker price fetch failed for {trade.symbol}: {e}")
                return None

        if l_price is None or s_price is None or l_price <= 0 or s_price <= 0:
            return None

        entry_long = trade.entry_price_long or l_price
        entry_short = trade.entry_price_short or s_price

        notional = entry_long * trade.long_qty
        if notional <= 0:
            return None

        long_price_pnl = (l_price - entry_long) * trade.long_qty
        short_price_pnl = (entry_short - s_price) * trade.short_qty
        total_price_pnl = long_price_pnl + short_price_pnl
        price_pnl_pct = total_price_pnl / notional * Decimal("100")

        funding_pnl_pct = trade.funding_collected_usd / notional * Decimal("100") if trade.funding_collected_usd else Decimal("0")

        total_fees = trade.fees_paid_total or Decimal("0")
        fees_pct = total_fees / notional * Decimal("100") if total_fees else Decimal("0")

        exit_fees_est = Decimal("0")
        if trade.long_taker_fee:
            exit_fees_est += l_price * trade.long_qty * trade.long_taker_fee
        if trade.short_taker_fee:
            exit_fees_est += s_price * trade.short_qty * trade.short_taker_fee
        exit_fees_pct = exit_fees_est / notional * Decimal("100") if exit_fees_est else Decimal("0")

        total_pnl_pct = price_pnl_pct + funding_pnl_pct - fees_pct - exit_fees_pct

        return {
            "total_pnl_pct": total_pnl_pct,
            "price_pnl_pct": price_pnl_pct,
            "funding_pnl_pct": funding_pnl_pct,
            "fees_pct": fees_pct,
            "long_price": l_price,
            "short_price": s_price,
        }

    async def _next_funding_qualifies(self, trade: TradeRecord, long_adapter, short_adapter) -> bool:
        """Check if the NEXT IMMINENT funding payment justifies staying.

        Applies the SAME entry-window rules as the scanner:
          1. Classify each side as income or cost
          2. Check if any INCOME side fires within max_entry_window_minutes
          3. Compute imminent net spread (income minus cost that also fires)
          4. Net must exceed min_funding_spread after fees
        """
        tp = self._cfg.trading_params

        long_funding = long_adapter.get_funding_rate_cached(trade.symbol)
        short_funding = short_adapter.get_funding_rate_cached(trade.symbol)
        if not long_funding or not short_funding:
            return False

        long_rate = Decimal(str(long_funding["rate"]))
        short_rate = Decimal(str(short_funding["rate"]))

        entry_window_min = float(tp.max_entry_window_minutes)
        now_ms = _time.time() * 1000

        long_next_ts = long_funding.get("next_timestamp")
        short_next_ts = short_funding.get("next_timestamp")

        long_is_income = long_rate < 0
        short_is_income = short_rate > 0

        long_mins = (long_next_ts - now_ms) / 60_000 if (long_next_ts and long_next_ts > now_ms) else None
        short_mins = (short_next_ts - now_ms) / 60_000 if (short_next_ts and short_next_ts > now_ms) else None

        long_imminent = long_is_income and long_mins is not None and long_mins <= entry_window_min
        short_imminent = short_is_income and short_mins is not None and short_mins <= entry_window_min

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

        imminent_income_pct = _ZERO
        imminent_cost_pct = _ZERO
        if long_imminent:
            imminent_income_pct += abs(long_rate) * _HUNDRED
        if short_imminent:
            imminent_income_pct += abs(short_rate) * _HUNDRED
        if not long_is_income and long_mins is not None and long_mins <= entry_window_min:
            imminent_cost_pct += abs(long_rate) * _HUNDRED
        if not short_is_income and short_mins is not None and short_mins <= entry_window_min:
            imminent_cost_pct += abs(short_rate) * _HUNDRED
        imminent_spread_pct = imminent_income_pct - imminent_cost_pct

        long_spec = long_adapter.get_cached_instrument_spec(trade.symbol)
        short_spec = short_adapter.get_cached_instrument_spec(trade.symbol)
        if not long_spec or not short_spec:
            return False

        fees_pct = calculate_fees(long_spec.taker_fee, short_spec.taker_fee)
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

    async def _check_liquidation_risk(self, trade: TradeRecord, long_adapter, short_adapter) -> bool:
        """Check if either side is approaching liquidation.

        Returns True if trade was closed due to liquidation risk.
        """
        safety_pct = float(self._cfg.trading_params.liquidation_safety_pct)

        try:
            long_positions, short_positions = await asyncio.gather(
                long_adapter.get_positions(trade.symbol),
                short_adapter.get_positions(trade.symbol),
            )

            for positions, exchange, side in [
                (long_positions, trade.long_exchange, "LONG"),
                (short_positions, trade.short_exchange, "SHORT"),
            ]:
                for pos in positions:
                    if pos.symbol != trade.symbol:
                        continue
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
