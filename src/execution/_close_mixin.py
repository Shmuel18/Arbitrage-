"""
Execution controller mixin — methods extracted from controller.py.
Do NOT import this module directly; use ExecutionController from controller.py.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Dict, Optional

from src.core.contracts import (
    ExitReason,
    OrderRequest,
    OrderSide,
    TradeRecord,
    TradeState,
)
from src.core.logging import get_logger
from src.execution import helpers as _h

if TYPE_CHECKING:
    pass  # all attribute access via self (mixin pattern)

logger = get_logger("execution")


class _CloseMixin:
    async def _close_trade(self, trade: TradeRecord) -> None:
        trade.state = TradeState.CLOSING
        await self._persist_trade(trade)

        long_adapter = self._exchanges.get(trade.long_exchange)
        short_adapter = self._exchanges.get(trade.short_exchange)

        # Close both legs in parallel — sequential close wastes time
        # and risks the 10s timeout on the second leg after re-fetch delays.
        long_fill, short_fill = await asyncio.gather(
            self._close_leg(
                long_adapter, trade.long_exchange, trade.symbol,
                OrderSide.SELL, trade.long_qty, trade.trade_id,
            ),
            self._close_leg(
                short_adapter, trade.short_exchange, trade.symbol,
                OrderSide.BUY, trade.short_qty, trade.trade_id,
            ),
            return_exceptions=True,
        )
        # Unpack gather results — exceptions become None
        if isinstance(long_fill, BaseException):
            logger.error(
                f"Long close exception for {trade.symbol}: {long_fill}",
                extra={"trade_id": trade.trade_id},
            )
            long_fill = None
        if isinstance(short_fill, BaseException):
            logger.error(
                f"Short close exception for {trade.symbol}: {short_fill}",
                extra={"trade_id": trade.trade_id},
            )
            short_fill = None

        if long_fill and short_fill:
            trade.state = TradeState.CLOSED
            trade.closed_at = datetime.now(timezone.utc)
            trade.exit_price_long = _h.extract_avg_price(long_fill)
            trade.exit_price_short = _h.extract_avg_price(short_fill)

            # ── Fallback: if exchange didn't return avg price ──
            # Priority: (1) trades API (actual fill), (2) ticker (last resort)
            if trade.exit_price_long is None and long_adapter:
                try:
                    _order_id = long_fill.get("id") if long_fill else None
                    _recovered = await long_adapter.fetch_fill_price_from_trades(
                        trade.symbol, _order_id,
                    )
                    if _recovered is not None:
                        trade.exit_price_long = _recovered
                        logger.info(
                            f"[{trade.symbol}] Long exit price from trades API: "
                            f"{trade.exit_price_long}",
                        )
                    else:
                        t = await long_adapter.get_ticker(trade.symbol)
                        trade.exit_price_long = Decimal(str(t.get("last", 0)))
                        logger.warning(
                            f"[{trade.symbol}] Long exit price from ticker (last resort): "
                            f"{trade.exit_price_long}",
                        )
                except Exception as exc:
                    logger.debug(f"[{trade.symbol}] Long exit price recovery failed: {exc}")
            if trade.exit_price_short is None and short_adapter:
                try:
                    _order_id = short_fill.get("id") if short_fill else None
                    _recovered = await short_adapter.fetch_fill_price_from_trades(
                        trade.symbol, _order_id,
                    )
                    if _recovered is not None:
                        trade.exit_price_short = _recovered
                        logger.info(
                            f"[{trade.symbol}] Short exit price from trades API: "
                            f"{trade.exit_price_short}",
                        )
                    else:
                        t = await short_adapter.get_ticker(trade.symbol)
                        trade.exit_price_short = Decimal(str(t.get("last", 0)))
                        logger.warning(
                            f"[{trade.symbol}] Short exit price from ticker (last resort): "
                            f"{trade.exit_price_short}",
                        )
                except Exception as exc:
                    logger.debug(f"[{trade.symbol}] Short exit price recovery failed: {exc}")

            # ── Reconcile close fees from actual trade data ────────
            # The createOrder response for close/reduce orders often has no
            # fee data (exchanges skip fetchOrder retries for speed).  Fetch
            # actual per-fill fees from myTrades API for exchange-accurate PnL.
            _long_order_id = long_fill.get("id") if long_fill else None
            _short_order_id = short_fill.get("id") if short_fill else None

            _settled_details: list = [None, None]  # [long, short]
            _settle_tasks = []
            _settle_indices: list[int] = []

            if long_adapter and _long_order_id:
                _settle_tasks.append(
                    long_adapter.fetch_fill_details_from_trades(
                        trade.symbol, _long_order_id,
                    )
                )
                _settle_indices.append(0)
            if short_adapter and _short_order_id:
                _settle_tasks.append(
                    short_adapter.fetch_fill_details_from_trades(
                        trade.symbol, _short_order_id,
                    )
                )
                _settle_indices.append(1)

            if _settle_tasks:
                _settle_results = await asyncio.gather(
                    *_settle_tasks, return_exceptions=True,
                )
                for idx, res in zip(_settle_indices, _settle_results):
                    if isinstance(res, dict):
                        _settled_details[idx] = res
                    elif isinstance(res, BaseException):
                        logger.debug(
                            f"[{trade.symbol}] Fill details fetch failed "
                            f"({'long' if idx == 0 else 'short'}): {res}",
                        )

            # Update exit prices from settled data if original was missing
            if _settled_details[0] and trade.exit_price_long is None:
                trade.exit_price_long = _settled_details[0]["avg_price"]
            if _settled_details[1] and trade.exit_price_short is None:
                trade.exit_price_short = _settled_details[1]["avg_price"]

            # Use stored taker fees as fallback for extract_fee
            fallback_long = trade.long_taker_fee
            fallback_short = trade.short_taker_fee

            # If not in record (old trades), fetch from adapter
            if fallback_long is None and long_adapter:
                _ls = await long_adapter.get_instrument_spec(trade.symbol)
                fallback_long = _ls.taker_fee
            if fallback_short is None and short_adapter:
                _ss = await short_adapter.get_instrument_spec(trade.symbol)
                fallback_short = _ss.taker_fee

            # Prefer actual exchange fees from trades API, fall back to
            # extract_fee (order response → taker_rate estimate).
            if _settled_details[0] and _settled_details[0]["total_fee"] > 0:
                close_fee_long = _settled_details[0]["total_fee"]
            else:
                close_fee_long = _h.extract_fee(long_fill, fallback_long)
            if _settled_details[1] and _settled_details[1]["total_fee"] > 0:
                close_fee_short = _settled_details[1]["total_fee"]
            else:
                close_fee_short = _h.extract_fee(short_fill, fallback_short)

            close_fees = close_fee_long + close_fee_short
            total_fees = (trade.fees_paid_total or Decimal("0")) + close_fees
            trade.fees_paid_total = total_fees

            # Log fee source for debugging
            _long_src = "trades_api" if (_settled_details[0] and _settled_details[0]["total_fee"] > 0) else "estimate"
            _short_src = "trades_api" if (_settled_details[1] and _settled_details[1]["total_fee"] > 0) else "estimate"
            logger.info(
                f"[{trade.symbol}] Close fees: "
                f"{trade.long_exchange}=${float(close_fee_long):.6f} [{_long_src}]  "
                f"{trade.short_exchange}=${float(close_fee_short):.6f} [{_short_src}]  "
                f"total_fees=${float(total_fees):.6f}",
                extra={"trade_id": trade.trade_id, "symbol": trade.symbol},
            )
            if trade.funding_paid_total is None and trade.funding_received_total is None:
                if trade.funding_collected_usd != 0:
                    # Use actual accumulated collection total — multi-payment aware.
                    # Correctly split net into received/paid for the breakdown display.
                    if trade.funding_collected_usd > 0:
                        trade.funding_received_total = trade.funding_collected_usd
                        trade.funding_paid_total = Decimal("0")
                    else:
                        trade.funding_received_total = Decimal("0")
                        trade.funding_paid_total = abs(trade.funding_collected_usd)
                else:
                    # Fallback: estimate from entry rates — BUT only if we actually
                    # held through a funding payment. If closed before next_funding_time,
                    # no payment fired so funding P&L is zero.
                    next_long_ms = trade.next_funding_long.timestamp() * 1000 if trade.next_funding_long else None
                    next_short_ms = trade.next_funding_short.timestamp() * 1000 if trade.next_funding_short else None
                    earliest_funding_ms: Optional[float] = None
                    if next_long_ms is not None and next_short_ms is not None:
                        earliest_funding_ms = min(next_long_ms, next_short_ms)
                    elif next_long_ms is not None:
                        earliest_funding_ms = next_long_ms
                    elif next_short_ms is not None:
                        earliest_funding_ms = next_short_ms
                    closed_ms = trade.closed_at.timestamp() * 1000 if trade.closed_at else None
                    if earliest_funding_ms is not None and closed_ms is not None and closed_ms < earliest_funding_ms:
                        # Closed before any payment fired — no funding to report
                        logger.info(
                            f"[{trade.symbol}] Closed {(earliest_funding_ms - closed_ms)/1000:.0f}s before funding "
                            f"— funding P&L = $0 (not collected)",
                            extra={"trade_id": trade.trade_id, "symbol": trade.symbol}
                        )
                        trade.funding_paid_total = Decimal("0")
                        trade.funding_received_total = Decimal("0")
                    else:
                        paid, received = _h.estimate_funding_totals(trade)
                        trade.funding_paid_total = paid
                        trade.funding_received_total = received

            # ── Reconcile with ACTUAL exchange funding history ────────────
            # Fetch real funding payments from both exchanges for the trade window.
            # This overrides the bot's internal estimate (which was based on rates
            # snapshotted at entry) with what the exchange actually settled.
            _since_ms = int(trade.opened_at.timestamp() * 1000) if trade.opened_at else None
            # Add a small buffer after close so delayed settlements are captured
            _until_ms = int((trade.closed_at.timestamp() + 300) * 1000) if trade.closed_at else None
            _real_funding_source = "estimate"
            _real_funding_long: Optional[Dict] = None
            _real_funding_short: Optional[Dict] = None
            _long_net: float = 0.0
            _short_net: float = 0.0
            if _since_ms and long_adapter and short_adapter:
                _real_funding_long, _real_funding_short = await asyncio.gather(
                    long_adapter.fetch_funding_history(trade.symbol, _since_ms, _until_ms),
                    short_adapter.fetch_funding_history(trade.symbol, _since_ms, _until_ms),
                    return_exceptions=True,
                )

            _long_hist_ok = (
                isinstance(_real_funding_long, dict)
                and _real_funding_long.get("source") == "exchange"
            )
            _short_hist_ok = (
                isinstance(_real_funding_short, dict)
                and _real_funding_short.get("source") == "exchange"
            )

            if _long_hist_ok or _short_hist_ok:
                _long_net = _real_funding_long.get("net_usd", 0.0) if _long_hist_ok else 0.0
                _short_net = _real_funding_short.get("net_usd", 0.0) if _short_hist_ok else 0.0

                # ── Sign validation ──────────────────────────────────
                # Some exchanges (e.g. Bybit) return funding amounts with
                # reversed sign (positive = paid instead of positive = received).
                # Validate using funding rate direction:
                #   Long:  income when rate < 0,  cost when rate > 0
                #   Short: income when rate > 0,  cost when rate < 0
                _long_rate_f = float(trade.long_funding_rate or 0)
                _short_rate_f = float(trade.short_funding_rate or 0)

                if _long_hist_ok and _long_net != 0 and abs(_long_rate_f) > 0.00005:
                    _expect_long_positive = _long_rate_f < 0
                    if (_long_net > 0) != _expect_long_positive:
                        logger.warning(
                            f"[{trade.symbol}] Funding sign correction on "
                            f"{trade.long_exchange} (long): API=${_long_net:+.4f} "
                            f"vs rate={_long_rate_f:.6f} — flipping sign",
                            extra={"trade_id": trade.trade_id},
                        )
                        _long_net = -_long_net

                if _short_hist_ok and _short_net != 0 and abs(_short_rate_f) > 0.00005:
                    _expect_short_positive = _short_rate_f > 0
                    if (_short_net > 0) != _expect_short_positive:
                        logger.warning(
                            f"[{trade.symbol}] Funding sign correction on "
                            f"{trade.short_exchange} (short): API=${_short_net:+.4f} "
                            f"vs rate={_short_rate_f:.6f} — flipping sign",
                            extra={"trade_id": trade.trade_id},
                        )
                        _short_net = -_short_net

                # If only one side responded but the other didn't, estimate
                # the missing side independently using rate × notional.
                # NEVER back-calculate from funding_collected_usd — that total
                # may itself be wrong (sign-correction bugs, stale rates), making
                # the reconciliation circular and useless.
                if _long_hist_ok and not _short_hist_ok:
                    _s_notional = float((trade.entry_price_short or Decimal("0")) * trade.short_qty)
                    _s_rate = float(trade.short_funding_rate or 0)
                    # Short receives when rate > 0, pays when rate < 0
                    _short_net = _s_notional * _s_rate * max(trade.funding_collections, 1)
                    logger.warning(
                        f"[{trade.symbol}] Real funding: {trade.long_exchange} responded "
                        f"(${_long_net:+.4f}) but {trade.short_exchange} unavailable — "
                        f"estimating short from rate ({_s_rate:.6f} × ${_s_notional:.2f} "
                        f"× {max(trade.funding_collections, 1)} collections = ${_short_net:+.4f})",
                        extra={"trade_id": trade.trade_id},
                    )
                elif _short_hist_ok and not _long_hist_ok:
                    _l_notional = float((trade.entry_price_long or Decimal("0")) * trade.long_qty)
                    _l_rate = float(trade.long_funding_rate or 0)
                    # Long receives when rate < 0, pays when rate > 0
                    _long_net = _l_notional * (-_l_rate) * max(trade.funding_collections, 1)
                    logger.warning(
                        f"[{trade.symbol}] Real funding: {trade.short_exchange} responded "
                        f"(${_short_net:+.4f}) but {trade.long_exchange} unavailable — "
                        f"estimating long from rate ({_l_rate:.6f} × ${_l_notional:.2f} "
                        f"× {max(trade.funding_collections, 1)} collections = ${_long_net:+.4f})",
                        extra={"trade_id": trade.trade_id},
                    )

                _real_net_total = _long_net + _short_net
                _est_net = float((trade.funding_received_total or Decimal("0")) - (trade.funding_paid_total or Decimal("0")))
                logger.info(
                    f"[{trade.symbol}] Funding reconcile: "
                    f"estimated=${_est_net:+.4f}  real=${_real_net_total:+.4f}  "
                    f"(long={trade.long_exchange}:${_long_net:+.4f}  short={trade.short_exchange}:${_short_net:+.4f})",
                    extra={"trade_id": trade.trade_id, "symbol": trade.symbol, "action": "funding_reconcile"},
                )
                # Override estimates with real values
                if _real_net_total >= 0:
                    trade.funding_received_total = Decimal(str(round(_real_net_total, 8)))
                    trade.funding_paid_total = Decimal("0")
                else:
                    trade.funding_received_total = Decimal("0")
                    trade.funding_paid_total = Decimal(str(round(abs(_real_net_total), 8)))
                _real_funding_source = "exchange"

                # Log per-exchange breakdown
                if _long_hist_ok and _real_funding_long.get("payments"):
                    _long_pmts = _real_funding_long["payments"]
                    logger.info(
                        f"[{trade.symbol}] {trade.long_exchange} funding ({len(_long_pmts)} payment(s)): "
                        + "  ".join(
                            f"${p['amount']:+.4f} @ {datetime.fromtimestamp(p['timestamp']/1000, tz=timezone.utc).strftime('%H:%M:%S')}"
                            for p in _long_pmts
                        ),
                        extra={"trade_id": trade.trade_id},
                    )
                if _short_hist_ok and _real_funding_short.get("payments"):
                    _short_pmts = _real_funding_short["payments"]
                    logger.info(
                        f"[{trade.symbol}] {trade.short_exchange} funding ({len(_short_pmts)} payment(s)): "
                        + "  ".join(
                            f"${p['amount']:+.4f} @ {datetime.fromtimestamp(p['timestamp']/1000, tz=timezone.utc).strftime('%H:%M:%S')}"
                            for p in _short_pmts
                        ),
                        extra={"trade_id": trade.trade_id},
                    )
            else:
                logger.info(
                    f"[{trade.symbol}] Funding reconcile: exchange history unavailable — using bot estimate "
                    f"(${float((trade.funding_received_total or Decimal('0')) - (trade.funding_paid_total or Decimal('0'))):+.4f})",
                    extra={"trade_id": trade.trade_id},
                )

            await self._redis.delete_trade_state(trade.trade_id)
            # Cooldown prevents immediate re-entry into same symbol
            cooldown_sec = self._cfg.trading_params.cooldown_after_close_seconds
            if cooldown_sec > 0:
                await self._redis.set_cooldown(trade.symbol, cooldown_sec)
            self._deregister_trade(trade)

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
            except Exception as exc:
                logger.debug(f"Exit funding rate fetch failed for {trade.symbol}: {exc}")

            _exit_reason = trade._exit_reason or ExitReason.SPREAD_BELOW_THRESHOLD
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

            # Entry vs exit price basis: (long_price − short_price) / short_price × 100
            _entry_basis = trade.entry_basis_pct if trade.entry_basis_pct is not None else Decimal("0")
            _exit_basis = Decimal("0")
            _basis_pnl_str = "(prices unavailable)"
            if trade.exit_price_long and trade.exit_price_short and trade.exit_price_short > 0:
                _exit_basis = (trade.exit_price_long - trade.exit_price_short) / trade.exit_price_short * Decimal("100")
                _basis_delta = _exit_basis - _entry_basis
                _basis_pnl_str = (
                    f"entry={float(_entry_basis):+.4f}% → exit={float(_exit_basis):+.4f}% "
                    f"(Δ{float(_basis_delta):+.4f}% — "
                    f"{'favorable ✔' if _basis_delta <= 0 else 'adverse ✘'})"
                )

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
                f"  ────────── PRICE BASIS ──────────\n"
                f"  Basis:      {_basis_pnl_str}\n"
                f"  ────────── FUNDING P&L [{_real_funding_source.upper()}] ──────────\n"
                + (
                    f"  {trade.long_exchange} (long):   ${_long_net:+.4f}\n"
                    f"  {trade.short_exchange} (short):  ${_short_net:+.4f}\n"
                    if _real_funding_source == "exchange" and _real_funding_long and _real_funding_short
                    else ""
                ) +
                f"  Funding:    +${float(funding_income):.4f} income  -${float(funding_cost):.4f} cost  = ${float(funding_net):.4f} net\n"
                f"  ────────── TOTALS ──────────\n"
                f"  Price PnL:  ${float(price_pnl):.4f}  (long=${float(long_pnl):.4f} + short=${float(short_pnl):.4f})\n"
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
                # Real exchange data (if available)
                funding_source=_real_funding_source,
                long_funding_net_real=_long_net if _real_funding_source == "exchange" else None,
                short_funding_net_real=_short_net if _real_funding_source == "exchange" else None,
            )

            # ── Publish PnL data point to Redis for frontend chart ──
            try:
                pnl_value = float(total_pnl)
                ts = datetime.now(timezone.utc).timestamp()
                # Use JSON member with trade_id to avoid dedup when two trades
                # close with identical PnL values (sorted-set members must be unique).
                member = json.dumps({"trade_id": trade.trade_id, "pnl": pnl_value})
                await self._redis.zadd(
                    "trinity:pnl:timeseries",
                    {member: ts},
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
                "entry_basis_pct": str(trade.entry_basis_pct) if trade.entry_basis_pct is not None else None,
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
                {json.dumps(trade_data): datetime.now(timezone.utc).timestamp()},
            )
            if self._publisher:
                self._publisher.record_trade(is_win=total_pnl >= 0, pnl=float(total_pnl))

            # Log balances after trade closure (if enabled)
            if self._cfg.logging.log_balances_after_trade:
                await self._log_exchange_balances()
        else:
            trade.state = TradeState.ERROR
            await self._persist_trade(trade)
            # Free exchange locks so a single failed close doesn't block the whole bot.
            self._deregister_trade(trade)
            logger.error(
                f"Trade {trade.trade_id} partially closed — MANUAL INTERVENTION NEEDED "
                f"(exchange locks released, cooldown applied)",
                extra={"trade_id": trade.trade_id, "action": "close_partial_fail"},
            )
            cooldown_sec = self._cfg.trading_params.cooldown_after_orphan_hours * 3600
            await self._redis.set_cooldown(trade.symbol, cooldown_sec)
            # Alert operator immediately
            if self._publisher:
                try:
                    await self._publisher.push_alert(
                        f"🚨 Trade {trade.trade_id} ({trade.symbol}) in ERROR state — "
                        f"one leg may still be open. MANUAL INTERVENTION REQUIRED."
                    )
                except Exception as exc:
                    logger.debug(f"Error-state alert publish failed: {exc}")

    async def _close_leg(
        self, adapter, exchange: str, symbol: str,
        side: OrderSide, qty: Decimal, trade_id: str,
    ) -> Optional[dict]:
        """Close one leg with retry (3×). Always reduceOnly.

        Recognises 'no open positions' / 'empty position' errors as
        success — the position is already gone (closed by panic close
        or the original order filled but fetchOrder was unreliable).
        """
        _NO_POSITION_KEYWORDS = (
            "no open position",
            "empty position",
            "position does not exist",
            "reduce only: current position is",
        )
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
                # _place_with_timeout returned None — check if position
                # is already gone (order may have succeeded silently)
                if adapter and attempt >= 1:
                    try:
                        resolved = adapter._resolve_symbol(symbol)
                        positions = await adapter._exchange.fetch_positions([resolved])
                        has_pos = any(
                            abs(float(p.get("contracts", 0) or 0)) > 1e-12
                            for p in positions
                        )
                        if not has_pos:
                            logger.info(
                                f"[{symbol}] Position already gone on {exchange} — "
                                f"treating as successful close",
                                extra={"trade_id": trade_id, "exchange": exchange},
                            )
                            return {"filled": float(qty), "average": None,
                                    "id": "position_verified_gone"}
                    except Exception as _pe:
                        logger.debug(f"Position check failed on {exchange}/{symbol}: {_pe}")
            except Exception as e:
                err_lower = str(e).lower()
                if any(kw in err_lower for kw in _NO_POSITION_KEYWORDS):
                    logger.info(
                        f"[{symbol}] {exchange}: '{e}' — position already closed",
                        extra={"trade_id": trade_id, "exchange": exchange},
                    )
                    return {"filled": float(qty), "average": None,
                            "id": "position_already_closed"}
                logger.warning(
                    f"Close attempt {attempt+1}/3 failed {exchange}/{symbol}: {e}",
                    extra={"trade_id": trade_id, "exchange": exchange},
                )
                await asyncio.sleep(1)
        return None

    # ── Close all (shutdown) ─────────────────────────────────────

    async def _record_manual_close(self, trade: TradeRecord) -> None:
        """Save a manually-closed trade to Redis history with best-effort PnL."""
        try:
            now = datetime.now(timezone.utc)
            trade.closed_at = trade.closed_at or now

            # Try to get live exit prices (same approach as _close_trade auto-exit)
            long_adapter = self._exchanges.get(trade.long_exchange)
            short_adapter = self._exchanges.get(trade.short_exchange)

            if trade.exit_price_long is None and long_adapter:
                try:
                    ticker = await long_adapter.get_ticker(trade.symbol)
                    p = ticker.get("last") or ticker.get("close")
                    if p:
                        trade.exit_price_long = Decimal(str(p))
                        logger.info(f"[{trade.symbol}] Manual-close long exit price from ticker: {trade.exit_price_long}")
                except Exception as exc:
                    logger.debug(f"[{trade.symbol}] Manual-close long ticker failed: {exc}")
                if trade.exit_price_long is None:
                    mp = long_adapter.get_mark_price(trade.symbol)
                    if mp:
                        trade.exit_price_long = Decimal(str(mp))
                        logger.info(f"[{trade.symbol}] Manual-close long exit price from mark cache: {trade.exit_price_long}")

            if trade.exit_price_short is None and short_adapter:
                try:
                    ticker = await short_adapter.get_ticker(trade.symbol)
                    p = ticker.get("last") or ticker.get("close")
                    if p:
                        trade.exit_price_short = Decimal(str(p))
                        logger.info(f"[{trade.symbol}] Manual-close short exit price from ticker: {trade.exit_price_short}")
                except Exception as exc:
                    logger.debug(f"[{trade.symbol}] Manual-close short ticker failed: {exc}")
                if trade.exit_price_short is None:
                    mp = short_adapter.get_mark_price(trade.symbol)
                    if mp:
                        trade.exit_price_short = Decimal(str(mp))
                        logger.info(f"[{trade.symbol}] Manual-close short exit price from mark cache: {trade.exit_price_short}")

            # Last resort: use entry price (zero price movement — PnL from funding only)
            exit_long  = trade.exit_price_long  or trade.entry_price_long  or Decimal("0")
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
                paid, received = _h.estimate_funding_totals(trade)
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
                "entry_basis_pct": str(trade.entry_basis_pct) if trade.entry_basis_pct is not None else None,
                "total_pnl": float(total_pnl),
                "price_pnl": float(price_pnl),
                "funding_net": float(funding_net),
                "invested": float(invested),
                "hold_minutes": float(hold_minutes),
                "exit_reason": ExitReason.MANUAL_CLOSE,
                "funding_collections": trade.funding_collections,
                "funding_collected_usd": str(trade.funding_collected_usd),
            }
            await self._redis.zadd(
                "trinity:trades:history",
                {json.dumps(trade_data): datetime.now(timezone.utc).timestamp()},
            )
            if self._publisher:
                self._publisher.record_trade(is_win=total_pnl >= 0, pnl=float(total_pnl))
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

    # _TIMEOUT_COOLDOWN_SEC and _TIMEOUT_BLACKLIST_THRESHOLD live in controller.py

