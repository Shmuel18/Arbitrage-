"""
Execution controller mixin — close-trade orchestration.
Do NOT import this module directly; use ExecutionController from controller.py.

Heavy helpers (finalize, leg-close, manual-close) live in _close_finalize_mixin.py.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, Optional

from src.core.contracts import (
    ExitReason,
    OrderSide,
    TradeRecord,
    TradeState,
)
from src.core.logging import get_logger
from src.execution import helpers as _h
from src.execution._close_finalize_mixin import _CloseFinalizeMixin

logger = get_logger("execution")


class _CloseMixin(_CloseFinalizeMixin):
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

            # P1-3: Post-close dust reconcile.
            # Verify both positions are actually flat. If a partial fill or
            # exchange step-size residue was left, the position stays open
            # technically — consuming margin and accumulating funding costs.
            # We log a warning (not a fatal error) since the main risk guard
            # delta loop will also catch unbalanced positions independently.
            try:
                _long_has_dust, _short_has_dust = await asyncio.gather(
                    long_adapter.has_open_position(trade.symbol) if long_adapter else asyncio.sleep(0),
                    short_adapter.has_open_position(trade.symbol) if short_adapter else asyncio.sleep(0),
                    return_exceptions=True,
                )
                if _long_has_dust is True:
                    logger.warning(
                        f"[{trade.symbol}] DUST DETECTED on {trade.long_exchange} after close "
                        f"— position not fully flat. Risk guard delta loop will flag this.",
                        extra={"trade_id": trade.trade_id, "action": "dust_detected_long"},
                    )
                if _short_has_dust is True:
                    logger.warning(
                        f"[{trade.symbol}] DUST DETECTED on {trade.short_exchange} after close "
                        f"— position not fully flat. Risk guard delta loop will flag this.",
                        extra={"trade_id": trade.trade_id, "action": "dust_detected_short"},
                    )
            except Exception as _dust_exc:
                logger.debug(f"[{trade.symbol}] Post-close dust check failed: {_dust_exc}")

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

                # If only one side responded but the other didn't, use the
                # bot's per-side tracked estimate for the missing side.
                # The old formula `missing = bot_net - real_side` assumed
                # bot_net was accurate, but it breaks when the bot missed a
                # payment at collection time (e.g. rate was null, funding
                # fired on a different interval than expected).  Per-side
                # tracking avoids this: each side's estimate is independent.
                if _long_hist_ok and not _short_hist_ok:
                    _short_net = float(trade._funding_tracked_short)
                    logger.warning(
                        f"[{trade.symbol}] Real funding: {trade.long_exchange} responded "
                        f"(${_long_net:+.4f}) but {trade.short_exchange} unavailable — "
                        f"using bot per-side estimate for short (${_short_net:+.4f})",
                        extra={"trade_id": trade.trade_id},
                    )
                elif _short_hist_ok and not _long_hist_ok:
                    _long_net = float(trade._funding_tracked_long)
                    logger.warning(
                        f"[{trade.symbol}] Real funding: {trade.short_exchange} responded "
                        f"(${_short_net:+.4f}) but {trade.long_exchange} unavailable — "
                        f"using bot per-side estimate for long (${_long_net:+.4f})",
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

            # ── Finalize: persist, log summary, journal, Redis ──
            await self._finalize_and_publish_close(
                trade, total_fees, long_adapter, short_adapter,
                _long_net, _short_net, _real_funding_source,
                _real_funding_long, _real_funding_short,
            )
        else:
            # P0-4: Before declaring ERROR, do a live position reconcile.
            # _close_leg retries 3× with a position check on attempt ≥1, but if
            # ALL attempts timed out the exchange may have executed the orders anyway.
            # A false ERROR state leaves the bot thinking the trade is unresolved while
            # the actual positions are flat — causing manual confusion and missed clean-up.
            _long_still_open = True
            _short_still_open = True
            try:
                if long_adapter:
                    _live_long_pos = await long_adapter.get_positions(trade.symbol)
                    _long_still_open = any(
                        abs(float(getattr(p, "quantity", 0) or 0)) > 1e-9
                        for p in _live_long_pos
                        if getattr(p, "side", None) == OrderSide.BUY
                    )
                if short_adapter:
                    _live_short_pos = await short_adapter.get_positions(trade.symbol)
                    _short_still_open = any(
                        abs(float(getattr(p, "quantity", 0) or 0)) > 1e-9
                        for p in _live_short_pos
                        if getattr(p, "side", None) == OrderSide.SELL
                    )
            except Exception as _rc_exc:
                logger.warning(
                    f"[{trade.trade_id}] Post-close reconcile position check failed: {_rc_exc} "
                    f"— falling back to ERROR state",
                    extra={"trade_id": trade.trade_id},
                )

            if not _long_still_open and not _short_still_open:
                # Both legs are confirmed flat on the exchange — the close succeeded
                # despite no API fill confirmation (likely a network timeout after fill).
                logger.info(
                    f"[{trade.trade_id}] Post-close reconcile: both positions verified flat "
                    f"— treating as CLOSED despite missing fill receipts",
                    extra={"trade_id": trade.trade_id, "action": "reconcile_closed"},
                )
                trade.state = TradeState.CLOSED
                trade.closed_at = datetime.now(timezone.utc)
                await self._finalize_and_publish_close(
                    trade,
                    trade.fees_paid_total or Decimal("0"),
                    long_adapter,
                    short_adapter,
                    0.0, 0.0, "estimate", None, None,
                )
            else:
                # P0-2: Before declaring ERROR, attempt to close the still-open leg.
                # One leg may have closed successfully while the other timed out.
                # Leaving a naked directional position without attempting auto-close
                # risks unlimited loss if the market moves against the open leg.
                _orphan_ok = True
                if _long_still_open and not _short_still_open and long_adapter:
                    logger.warning(
                        f"[{trade.trade_id}] Short closed but LONG still open — "
                        f"auto-closing orphaned LONG on {trade.long_exchange}",
                        extra={"trade_id": trade.trade_id, "action": "orphan_auto_close_long"},
                    )
                    _orphan_ok = await self._close_orphan(
                        long_adapter, trade.long_exchange, trade.symbol,
                        OrderSide.SELL, {"filled": float(trade.long_qty)},
                    )
                elif _short_still_open and not _long_still_open and short_adapter:
                    logger.warning(
                        f"[{trade.trade_id}] Long closed but SHORT still open — "
                        f"auto-closing orphaned SHORT on {trade.short_exchange}",
                        extra={"trade_id": trade.trade_id, "action": "orphan_auto_close_short"},
                    )
                    _orphan_ok = await self._close_orphan(
                        short_adapter, trade.short_exchange, trade.symbol,
                        OrderSide.BUY, {"filled": float(trade.short_qty)},
                    )

                _state_msg = (
                    "orphan leg auto-closed successfully"
                    if _orphan_ok
                    else "orphan leg close FAILED — position still open on exchange"
                )
                trade.state = TradeState.ERROR
                await self._persist_trade(trade)
                # Free exchange locks so a single failed close doesn't block the whole bot.
                self._deregister_trade(trade)
                logger.error(
                    f"Trade {trade.trade_id} partially closed "
                    f"({_state_msg}) — "
                    f"long_open={_long_still_open} short_open={_short_still_open}. "
                    f"Exchange locks released, cooldown applied.",
                    extra={"trade_id": trade.trade_id, "action": "close_partial_fail"},
                )
                # P2-3: Persist ERROR trade ID to Redis so it survives process restart
                # and remains visible to operators even if the alert publish fails.
                # Key: trinity:error_trades (Redis set, 7-day TTL).
                try:
                    _err_key = "trinity:error_trades"
                    await self._redis.sadd(_err_key, trade.trade_id)
                    await self._redis.expire(_err_key, 7 * 24 * 3600)
                    logger.warning(
                        f"[{trade.trade_id}] Recorded in Redis set '{_err_key}'. "
                        f"Inspect with: redis-cli smembers {_err_key}",
                        extra={"trade_id": trade.trade_id},
                    )
                except Exception as _re:
                    logger.debug(f"Failed to persist error trade to Redis: {_re}")
                # P1: Apply cooldown and alert only on genuine ERROR — not when
                # post-close reconcile confirmed both legs are flat (CLOSED path).
                # Firing these unconditionally created false 'orphan' suppression
                # and operator noise for successful closes with missing fill receipts.
                cooldown_sec = self._cfg.trading_params.cooldown_after_orphan_hours * 3600
                await self._redis.set_cooldown(trade.symbol, cooldown_sec)
                if self._publisher:
                    try:
                        await self._publisher.publish_alert(
                            (
                                f"🚨 Trade {trade.trade_id} ({trade.symbol}) in ERROR state — "
                                f"one leg may still be open. MANUAL INTERVENTION REQUIRED."
                            ),
                            severity="critical",
                            alert_type="error_state",
                            symbol=trade.symbol,
                        )
                    except Exception as exc:
                        logger.debug(f"Error-state alert publish failed: {exc}")

    async def close_all_positions(self) -> None:
        """Close every active trade — called during graceful shutdown.

        P2-1: Parallel gather (was sequential) so N trades close in
        max(close_time) rather than N × close_time.  return_exceptions=True
        ensures one failed close doesn't block the others.
        """
        open_trades = [
            t for t in list(self._active_trades.values())
            if t.state == TradeState.OPEN
        ]
        if not open_trades:
            return
        logger.info(f"Shutdown: closing {len(open_trades)} open trade(s) in parallel")
        await asyncio.gather(
            *[self._close_trade(t) for t in open_trades],
            return_exceptions=True,
        )
