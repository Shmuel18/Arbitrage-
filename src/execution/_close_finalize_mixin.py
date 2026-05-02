"""
Close finalization helpers — extracted from _close_mixin.py.
Contains:
  _CloseFinalizeMixin  — _finalize_and_publish_close, _close_leg, _record_manual_close

Do NOT import this module directly; _CloseMixin inherits from it.
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
)
from src.core.logging import get_logger
from src.execution import helpers as _h

if TYPE_CHECKING:
    pass  # all attribute access via self (mixin pattern)

logger = get_logger("execution")


class _CloseFinalizeMixin:
    """Post-close persistence, logging, and leg-close helpers."""

    async def _finalize_and_publish_close(
        self,
        trade: TradeRecord,
        total_fees: Decimal,
        long_adapter,
        short_adapter,
        _long_net: float,
        _short_net: float,
        _real_funding_source: str,
        _real_funding_long: Optional[Dict],
        _real_funding_short: Optional[Dict],
    ) -> None:
        """Deregister trade, log summary, persist to Redis and journal.

        Called only when BOTH legs closed successfully.
        """
        await self._redis.delete_trade_state(trade.trade_id)
        cooldown_sec = self._cfg.trading_params.cooldown_after_close_seconds
        if cooldown_sec > 0:
            await self._redis.set_cooldown(trade.symbol, cooldown_sec)
        self._deregister_trade(trade)

        # ── Compute P&L breakdown ─────────────────────────────────
        entry_notional_long = (trade.entry_price_long or Decimal("0")) * trade.long_qty
        entry_notional_short = (trade.entry_price_short or Decimal("0")) * trade.short_qty
        exit_notional_long = (trade.exit_price_long or Decimal("0")) * trade.long_qty
        exit_notional_short = (trade.exit_price_short or Decimal("0")) * trade.short_qty
        long_pnl = exit_notional_long - entry_notional_long
        short_pnl = entry_notional_short - exit_notional_short

        # ── Exchange-reported Realized PnL (preferred source) ─────
        # The (exit−entry)×qty calc above is approximate — it ignores
        # liquidation penalties, mark-price gaps, and per-fill rounding.
        # Whenever ccxt's fetch_my_trades returns an info.realizedPnl-class
        # field, prefer that authoritative number. Falls through silently
        # for exchanges (e.g. Gateio) that don't expose it on trades.
        _pnl_source = "computed"
        _ex_long: Optional[Decimal] = None
        _ex_short: Optional[Decimal] = None
        if trade.opened_at:
            _since_ms = int(trade.opened_at.timestamp() * 1000)
        else:
            _since_ms = None
        try:
            _ex_long, _ex_short = await asyncio.gather(
                long_adapter.fetch_realized_pnl_from_trades(trade.symbol, since_ms=_since_ms)
                    if long_adapter else asyncio.sleep(0, result=None),
                short_adapter.fetch_realized_pnl_from_trades(trade.symbol, since_ms=_since_ms)
                    if short_adapter else asyncio.sleep(0, result=None),
                return_exceptions=True,
            )
            if isinstance(_ex_long, Decimal):
                logger.info(
                    f"[{trade.symbol}] Long PnL: bot computed ${float(long_pnl):.4f} → "
                    f"exchange-reported ${float(_ex_long):.4f}",
                    extra={"trade_id": trade.trade_id, "exchange": trade.long_exchange,
                           "action": "realized_pnl_override_long"},
                )
                long_pnl = _ex_long
                _pnl_source = "exchange_partial"
            if isinstance(_ex_short, Decimal):
                logger.info(
                    f"[{trade.symbol}] Short PnL: bot computed ${float(short_pnl):.4f} → "
                    f"exchange-reported ${float(_ex_short):.4f}",
                    extra={"trade_id": trade.trade_id, "exchange": trade.short_exchange,
                           "action": "realized_pnl_override_short"},
                )
                short_pnl = _ex_short
            if isinstance(_ex_long, Decimal) and isinstance(_ex_short, Decimal):
                _pnl_source = "exchange"
        except Exception as _rp_exc:
            logger.debug(
                f"[{trade.symbol}] Realized PnL reconcile failed: {_rp_exc}",
            )

        # ── Liquidation reconcile ────────────────────────────────
        # When the exchange force-closed a leg before our reduce-only order
        # filled (flagged in _close_leg), the recovered fill price isn't the
        # whole story — there's also the liquidation penalty + bankruptcy-
        # price gap that ate the rest of the margin. The realised loss is
        # ≈ leg_margin = leg_notional / leverage.
        #
        # If the realized-PnL block above already pulled exchange-reported
        # numbers for this leg, those are MORE accurate than the heuristic
        # margin-floor and we leave them alone. The override only applies
        # when ccxt couldn't extract realized PnL (e.g. Gateio trades) AND
        # the position was force-closed.
        _liquidated_legs: list[str] = []
        _long_pnl_from_exchange = isinstance(_ex_long, Decimal)
        _short_pnl_from_exchange = isinstance(_ex_short, Decimal)
        if getattr(trade, "_long_closed_externally", False):
            if not _long_pnl_from_exchange:
                _long_lev = self._cfg.exchanges.get(trade.long_exchange)
                _long_lev_n = int(_long_lev.leverage) if _long_lev and _long_lev.leverage else 5
                _long_margin = entry_notional_long / Decimal(str(_long_lev_n))
                if long_pnl > -_long_margin:
                    logger.warning(
                        f"[{trade.symbol}] Long leg force-closed by {trade.long_exchange}, "
                        f"no exchange-reported PnL — applying margin floor: "
                        f"${float(long_pnl):.4f} → ${float(-_long_margin):.4f}",
                        extra={"trade_id": trade.trade_id, "action": "liq_pnl_override_long"},
                    )
                    long_pnl = -_long_margin
            _liquidated_legs.append("long")
        if getattr(trade, "_short_closed_externally", False):
            if not _short_pnl_from_exchange:
                _short_lev = self._cfg.exchanges.get(trade.short_exchange)
                _short_lev_n = int(_short_lev.leverage) if _short_lev and _short_lev.leverage else 5
                _short_margin = entry_notional_short / Decimal(str(_short_lev_n))
                if short_pnl > -_short_margin:
                    logger.warning(
                        f"[{trade.symbol}] Short leg force-closed by {trade.short_exchange}, "
                        f"no exchange-reported PnL — applying margin floor: "
                        f"${float(short_pnl):.4f} → ${float(-_short_margin):.4f}",
                        extra={"trade_id": trade.trade_id, "action": "liq_pnl_override_short"},
                    )
                    short_pnl = -_short_margin
            _liquidated_legs.append("short")

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

        # ── Fetch current funding rates at exit for comparison ────
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
        # External liquidation overrides whatever exit reason the bot's logic
        # had assigned — the bot never actually got to act on its decision,
        # the exchange did. Tag clearly so post-mortem analysis isn't fooled
        # by phantom "profit_target" labels on what was actually a liq.
        if _liquidated_legs:
            _exit_reason = (
                f"liquidation_external_{'+'.join(_liquidated_legs)}"
            )
        entry_lr = float(trade.long_funding_rate or 0) * 100
        entry_sr = float(trade.short_funding_rate or 0) * 100
        exit_lr = float(exit_funding_long_rate or 0) * 100 if exit_funding_long_rate else None
        exit_sr = float(exit_funding_short_rate or 0) * 100 if exit_funding_short_rate else None

        funding_rates_str = f"  At entry:  {trade.long_exchange}={entry_lr:+.4f}%  {trade.short_exchange}={entry_sr:+.4f}%\n"
        if exit_lr is not None and exit_sr is not None:
            funding_rates_str += f"  At exit:   {trade.long_exchange}={exit_lr:+.4f}%  {trade.short_exchange}={exit_sr:+.4f}%"
        else:
            funding_rates_str += f"  At exit:   (rates unavailable)"

        _entry_basis = trade.entry_basis_pct if trade.entry_basis_pct is not None else Decimal("0")
        _exit_basis = Decimal("0")
        _basis_pnl_str = "(prices unavailable)"
        if trade.exit_price_long and trade.exit_price_short and trade.exit_price_short > 0:
            _exit_basis = (trade.exit_price_long - trade.exit_price_short) / trade.exit_price_short * Decimal("100")
            _basis_delta = _exit_basis - _entry_basis
            _basis_pnl_str = (
                f"entry={float(_entry_basis):+.4f}% → exit={float(_exit_basis):+.4f}% "
                f"(Δ{float(_basis_delta):+.4f}% — "
                f"{'favorable ✔' if _basis_delta >= 0 else 'adverse ✘'})"
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

        self._journal.trade_closed(
            trade_id=trade.trade_id, symbol=trade.symbol, mode=trade.mode,
            duration_min=float(hold_minutes),
            long_exchange=trade.long_exchange,
            short_exchange=trade.short_exchange,
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
            funding_source=_real_funding_source,
            long_funding_net_real=_long_net if _real_funding_source == "exchange" else None,
            short_funding_net_real=_short_net if _real_funding_source == "exchange" else None,
        )

        # ── Publish PnL data point to Redis ───────────────────────
        try:
            pnl_value = float(total_pnl)
            ts = datetime.now(timezone.utc).timestamp()
            member = json.dumps({"trade_id": trade.trade_id, "pnl": pnl_value})
            await self._redis.zadd("trinity:pnl:timeseries", {member: ts})
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
            "long_24h_volume_usd": str(trade.long_24h_volume_usd) if trade.long_24h_volume_usd is not None else None,
            "short_24h_volume_usd": str(trade.short_24h_volume_usd) if trade.short_24h_volume_usd is not None else None,
        }
        await self._redis.zadd(
            "trinity:trades:history",
            {json.dumps(trade_data): datetime.now(timezone.utc).timestamp()},
        )
        if self._publisher:
            self._publisher.record_trade(is_win=total_pnl >= 0, pnl=total_pnl)
            await self._publisher.publish_alert(
                (
                    f"🔴 Trade closed: {trade.trade_id} {trade.symbol} "
                    f"pnl=${float(total_pnl):+.4f} "
                    f"hold={float(hold_minutes):.0f}m"
                ),
                severity="info",
                alert_type="trade_close",
                symbol=trade.symbol,
                payload={
                    "trade_id": trade.trade_id,
                    "mode": trade.mode,
                    "long_exchange": trade.long_exchange,
                    "short_exchange": trade.short_exchange,
                    "entry_price_long": str(trade.entry_price_long) if trade.entry_price_long is not None else None,
                    "entry_price_short": str(trade.entry_price_short) if trade.entry_price_short is not None else None,
                    "exit_price_long": str(trade.exit_price_long) if trade.exit_price_long is not None else None,
                    "exit_price_short": str(trade.exit_price_short) if trade.exit_price_short is not None else None,
                    "total_pnl": float(total_pnl),
                    "price_pnl": float(price_pnl),
                    "funding_net": float(funding_net),
                    "fees": float(total_fees),
                    "invested": float(invested),
                    "profit_pct": float(profit_pct),
                    "hold_minutes": float(hold_minutes),
                    "exit_reason": _exit_reason,
                },
            )

        # Per-trade portfolio reconciliation: capture post-close balances,
        # diff against the pre-entry snapshot, and persist. Best-effort —
        # any failure inside the helper is logged and swallowed so it can
        # never block close finalization.
        await self._record_reconciliation(trade, expected_pnl=total_pnl)

        if self._cfg.logging.log_balances_after_trade:
            await self._log_exchange_balances()

    async def _close_leg(
        self, adapter, exchange: str, symbol: str,
        side: OrderSide, qty: Decimal, trade_id: str,
    ) -> Optional[dict]:
        """Close one leg with retry (3×). Always reduceOnly.

        Recognises 'no open positions' / 'empty position' errors as
        success — the position is already gone.
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
                    # P2-2: Warn on partial fills — dust left-over keeps the
                    # position technically open on the exchange, consuming margin
                    # and accumulating funding costs until manually remedied.
                    filled = float(result.get("filled") or 0)
                    expected = float(qty)
                    if expected > 0 and abs(filled - expected) / expected > 0.01:
                        logger.warning(
                            f"[{symbol}] Partial close fill on {exchange}: "
                            f"expected={expected:.6f} filled={filled:.6f} "
                            f"(dust={expected - filled:.8f}) — position may not be fully flat",
                            extra={"exchange": exchange, "symbol": symbol},
                        )
                    return result
                if adapter and attempt >= 1:
                    try:
                        # P1-4: Use adapter.has_open_position instead of
                        # adapter._exchange.fetch_positions to respect the
                        # abstraction layer (rate-limit semaphore + retries).
                        has_pos = await adapter.has_open_position(symbol)
                        if not has_pos:
                            # CRITICAL: position was gone BEFORE our reduce-only
                            # order succeeded → the exchange closed it for us.
                            # Almost always a liquidation (DAM @ 11:53 incident:
                            # bot computed +$0.12 profit but exchange recorded
                            # -$15.88 because the long was force-liquidated for
                            # the full margin). Flag for the finalize step to
                            # override exit_reason + PnL using the leg's margin
                            # rather than trusting the recovered fill price.
                            logger.warning(
                                f"[{symbol}] Position gone on {exchange} BEFORE "
                                f"our close order filled — flagging as suspected "
                                f"external liquidation",
                                extra={"trade_id": trade_id, "exchange": exchange,
                                       "action": "external_close_detected"},
                            )
                            return {"filled": float(qty), "average": None,
                                    "id": "position_verified_gone",
                                    "closed_externally": True}
                    except Exception as _pe:
                        logger.debug(f"Position check failed on {exchange}/{symbol}: {_pe}")
            except Exception as e:
                err_lower = str(e).lower()
                if any(kw in err_lower for kw in _NO_POSITION_KEYWORDS):
                    logger.warning(
                        f"[{symbol}] {exchange}: '{e}' — position closed by "
                        f"exchange before our order. Flagging as suspected "
                        f"external liquidation.",
                        extra={"trade_id": trade_id, "exchange": exchange,
                               "action": "external_close_detected"},
                    )
                    return {"filled": float(qty), "average": None,
                            "id": "position_already_closed",
                            "closed_externally": True}
                logger.warning(
                    f"Close attempt {attempt+1}/3 failed {exchange}/{symbol}: {e}",
                    extra={"trade_id": trade_id, "exchange": exchange},
                )
                await asyncio.sleep(1)
        return None

    async def _record_manual_close(self, trade: TradeRecord) -> None:
        """Save a manually-closed trade to Redis history with best-effort PnL."""
        try:
            now = datetime.now(timezone.utc)
            trade.closed_at = trade.closed_at or now

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

            exit_long  = trade.exit_price_long  or trade.entry_price_long  or Decimal("0")
            exit_short = trade.exit_price_short or trade.entry_price_short or Decimal("0")

            entry_notional_long  = (trade.entry_price_long  or Decimal("0")) * trade.long_qty
            entry_notional_short = (trade.entry_price_short or Decimal("0")) * trade.short_qty
            exit_notional_long  = exit_long  * trade.long_qty
            exit_notional_short = exit_short * trade.short_qty
            long_pnl  = exit_notional_long  - entry_notional_long
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
                "long_24h_volume_usd": str(trade.long_24h_volume_usd) if trade.long_24h_volume_usd is not None else None,
                "short_24h_volume_usd": str(trade.short_24h_volume_usd) if trade.short_24h_volume_usd is not None else None,
            }
            await self._redis.zadd(
                "trinity:trades:history",
                {json.dumps(trade_data): datetime.now(timezone.utc).timestamp()},
            )
            if self._publisher:
                self._publisher.record_trade(is_win=total_pnl >= 0, pnl=total_pnl)
            logger.info(
                f"📋 Manual close recorded: {trade.trade_id} ({trade.symbol}) "
                f"PnL=${float(total_pnl):.4f} (held {float(hold_minutes):.0f}min)",
                extra={"trade_id": trade.trade_id, "action": "manual_close_recorded"},
            )
            # Same reconciliation hook as the automated close path. The
            # manual-close path lacks an exchange-fetched expected_pnl with
            # exchange-reported PnL overrides, so the bot-computed total
            # is used as the expected value for the drift calculation.
            await self._record_reconciliation(trade, expected_pnl=total_pnl)
        except Exception as e:
            logger.error(f"Failed to record manual close for {trade.trade_id}: {e}")
