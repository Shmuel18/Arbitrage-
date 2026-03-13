"""
Execution controller mixin — methods extracted from controller.py.
Do NOT import this module directly; use ExecutionController from controller.py.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from src.core.contracts import (
    OrderRequest,
    OrderSide,
    TradeMode,
    TradeRecord,
    TradeState,
)
from src.core.logging import get_logger

if TYPE_CHECKING:
    pass  # all attribute access via self (mixin pattern)

logger = get_logger("execution")


class _UtilMixin:
    async def _place_with_timeout(self, adapter, req: OrderRequest) -> Optional[dict]:
        """Place order with timeout. Returns fill dict or None.

        On TimeoutError the order may have already executed on the exchange side
        (market orders cannot be cancelled once submitted). This method detects
        that scenario via a post-timeout position check and immediately triggers
        an orphan-close if a naked position is found.
        """
        timeout = self._cfg.execution.order_timeout_ms / 1000
        streak_key = f"{req.symbol}:{req.exchange}"
        try:
            result = await asyncio.wait_for(adapter.place_order(req), timeout=timeout)
            # Success — reset streak counter
            self._timeout_streak.pop(streak_key, None)
            return result
        except asyncio.TimeoutError:
            count = self._timeout_streak.get(streak_key, 0) + 1
            self._timeout_streak[streak_key] = count
            logger.error(
                f"Order timeout ({timeout}s) on {req.exchange}/{req.symbol} "
                f"(streak {count}/{self._TIMEOUT_BLACKLIST_THRESHOLD})",
                extra={"exchange": req.exchange, "symbol": req.symbol, "action": "order_timeout"},
            )

            # ── Post-timeout orphan check ─────────────────────────────────
            # Market orders are fire-and-forget on the exchange side — the
            # order may have filled while we were waiting for the response.
            # Only ENTRY orders can create a naked position; a timed-out close
            # simply means the position is still open (no new orphan created).
            if not req.reduce_only and hasattr(adapter, "check_timed_out_fill"):
                await asyncio.sleep(2)  # brief settle — give exchange time to process
                try:
                    filled_base = await adapter.check_timed_out_fill(req)
                    if filled_base > 0:
                        close_side = (
                            OrderSide.SELL if req.side == OrderSide.BUY else OrderSide.BUY
                        )
                        alert = (
                            f"🚨 TIMEOUT ORPHAN on {req.exchange}/{req.symbol}: "
                            f"order filled {filled_base:.6f} base despite timeout — "
                            f"emergency closing now."
                        )
                        logger.critical(
                            alert,
                            extra={
                                "exchange": req.exchange,
                                "symbol": req.symbol,
                                "action": "timeout_orphan_detected",
                                "filled_base": float(filled_base),
                            },
                        )
                        if self._publisher:
                            try:
                                await self._publisher.publish_alert(
                                    alert,
                                    severity="critical",
                                    alert_type="timeout",
                                    symbol=req.symbol,
                                    exchange=req.exchange,
                                )
                            except Exception as pub_exc:
                                logger.debug(f"Alert publish failed: {pub_exc}")
                        await self._close_orphan(
                            adapter, req.exchange, req.symbol,
                            close_side, {"filled": float(filled_base)},
                        )
                except Exception as check_exc:
                    logger.error(
                        f"Post-timeout orphan check failed for {req.exchange}/{req.symbol}: {check_exc}",
                        exc_info=check_exc,
                        extra={
                            "exchange": req.exchange,
                            "symbol": req.symbol,
                            "action": "orphan_check_failed",
                        },
                    )

            # ── Streak / blacklist / cooldown management ──────────────────
            if count >= self._TIMEOUT_BLACKLIST_THRESHOLD:
                self._blacklist.add(req.symbol, req.exchange)
                logger.warning(
                    f"⛔ {req.symbol} blacklisted on {req.exchange} after "
                    f"{count} consecutive timeouts",
                )
                self._timeout_streak.pop(streak_key, None)
            else:
                # Short cooldown to stop immediate retry
                await self._redis.set_cooldown(req.symbol, self._TIMEOUT_COOLDOWN_SEC)
                logger.warning(
                    f"⏸️ {req.symbol} cooldown {self._TIMEOUT_COOLDOWN_SEC}s after timeout "
                    f"on {req.exchange}",
                )
            return None
        except Exception as e:
            err_str = str(e).lower()
            # Detect delisting / restricted errors and blacklist
            if any(kw in err_str for kw in [
                "delisting", "delist", "30228",
                "symbol is not available",
                "contract is being settled",
                "reduce-only", "reduce only",
            ]):
                self._blacklist.add(req.symbol, req.exchange)
                logger.warning(
                    f"Blacklisted {req.symbol} on {req.exchange} (delisting/restricted): {e}",
                    extra={"exchange": req.exchange, "symbol": req.symbol, "action": "blacklisted"},
                )
            else:
                logger.error(
                    f"Order failed on {req.exchange}/{req.symbol}: {e}",
                    extra={"exchange": req.exchange, "symbol": req.symbol},
                )
            return None

    async def _close_orphan(
        self, adapter, exchange: str, symbol: str,
        side: OrderSide, fill: dict, fallback_qty: Optional[Decimal] = None,
    ) -> None:
        """Emergency close of a single orphaned leg.

        Retries up to 3 times with 2-second back-off. If all attempts fail,
        publishes a critical alert so the operator is notified immediately
        rather than silently leaving an unhedged position.
        """
        filled_qty = Decimal(str(fill.get("filled", 0)))
        if filled_qty <= 0:
            if fallback_qty and fallback_qty > 0:
                logger.warning(
                    f"⚠️ Orphan fill reported 0 — using fallback qty {fallback_qty} "
                    f"for {symbol} on {exchange}"
                )
                filled_qty = fallback_qty
            else:
                return

        req = OrderRequest(
            exchange=exchange,
            symbol=symbol,
            side=side,
            quantity=filled_qty,
            reduce_only=True,
        )

        _MAX_RETRIES = 3
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                await adapter.place_order(req)
                logger.info(
                    f"Orphan closed (attempt {attempt}): {filled_qty} {symbol} on {exchange}",
                    extra={"exchange": exchange, "symbol": symbol, "action": "orphan_closed"},
                )
                break
            except Exception as e:
                logger.error(
                    f"ORPHAN CLOSE attempt {attempt}/{_MAX_RETRIES} FAILED "
                    f"{exchange}/{symbol}: {e}",
                    extra={"exchange": exchange, "symbol": symbol},
                )
                if attempt < _MAX_RETRIES:
                    await asyncio.sleep(2 * attempt)  # 2s, 4s back-off
                else:
                    # All retries exhausted — alert operator
                    alert_msg = (
                        f"🚨 ORPHAN CLOSE FAILED after {_MAX_RETRIES} attempts: "
                        f"{filled_qty} {symbol} on {exchange}. MANUAL INTERVENTION REQUIRED."
                    )
                    logger.critical(alert_msg, extra={"exchange": exchange, "symbol": symbol})
                    if self._publisher:
                        try:
                            await self._publisher.publish_alert(
                                alert_msg,
                                severity="critical",
                                alert_type="orphan",
                                symbol=symbol,
                                exchange=exchange,
                            )
                        except Exception as exc:
                            logger.debug(f"Alert publish failed: {exc}")
                    self._blacklist.add(symbol, exchange)

        cooldown_sec = self._cfg.trading_params.cooldown_after_orphan_hours * 3600
        await self._redis.set_cooldown(symbol, cooldown_sec)
    # ── Trade registration ────────────────────────────────────────

    def _register_trade(self, trade: TradeRecord) -> None:
        """Add trade to _active_trades and keep O(1) derived sets in sync."""
        self._active_trades[trade.trade_id] = trade
        self._active_symbols.add(trade.symbol)
        self._busy_exchanges.add(trade.long_exchange)
        self._busy_exchanges.add(trade.short_exchange)
        # Increment refcounts so deregistration stays O(1)
        self._symbol_refcount[trade.symbol] = self._symbol_refcount.get(trade.symbol, 0) + 1
        self._exchange_refcount[trade.long_exchange] = self._exchange_refcount.get(trade.long_exchange, 0) + 1
        self._exchange_refcount[trade.short_exchange] = self._exchange_refcount.get(trade.short_exchange, 0) + 1

    def _deregister_trade(self, trade: TradeRecord) -> None:
        """Remove trade and update derived sets; safe to call multiple times.

        O(1) — uses refcount maps rather than scanning _active_trades.
        """
        self._active_trades.pop(trade.trade_id, None)
        # Decrement symbol refcount; release slot when no trade holds it
        sym_rc = self._symbol_refcount.get(trade.symbol, 0) - 1
        if sym_rc <= 0:
            self._symbol_refcount.pop(trade.symbol, None)
            self._active_symbols.discard(trade.symbol)
        else:
            self._symbol_refcount[trade.symbol] = sym_rc
        # Decrement exchange refcounts; release slot when no trade holds it
        for ex in (trade.long_exchange, trade.short_exchange):
            ex_rc = self._exchange_refcount.get(ex, 0) - 1
            if ex_rc <= 0:
                self._exchange_refcount.pop(ex, None)
                self._busy_exchanges.discard(ex)
            else:
                self._exchange_refcount[ex] = ex_rc
    # ── Persistence ──────────────────────────────────────────────

    async def _persist_trade(self, trade: TradeRecord) -> None:
        await self._redis.set_trade_state(
            trade.trade_id, trade.to_persist_dict(),
        )

    async def _recover_trades(self) -> None:
        """Recover active trades from Redis after crash/restart."""
        stored = await self._redis.get_all_trades()
        for trade_id, data in stored.items():
            state_val = data.get("state", "")
            if state_val not in (TradeState.OPEN.value, TradeState.CLOSING.value):
                continue

            trade = TradeRecord.from_persist_dict(trade_id, data)
            self._register_trade(trade)
            logger.info(
                f"Recovered trade {trade_id} ({trade.symbol}) state={trade.state.value}",
                extra={"trade_id": trade_id, "action": "trade_recovered"},
            )

            if trade.state == TradeState.CLOSING:
                logger.warning(
                    f"Trade {trade_id} was mid-close — retrying",
                    extra={"trade_id": trade_id},
                )
                task = asyncio.create_task(
                    self._close_trade(trade),
                    name=f"retry-close-{trade_id}",
                )
                task.add_done_callback(_task_done_handler)

        if stored:
            logger.info(f"Recovered {len(self._active_trades)} active trades")

    # ── Balance logging ───────────────────────────────────────────

    async def _log_exchange_balances(self) -> None:
        """Log current USDT balances for all exchanges."""
        try:
            logger.info("💰 EXCHANGE BALANCES", extra={"action": "balance_log"})

            adapters: list[tuple[str, object]] = []
            for exchange_id in self._cfg.enabled_exchanges:
                adapter = self._exchanges.get(exchange_id)
                if adapter:
                    adapters.append((exchange_id, adapter))

            results = await asyncio.gather(
                *[adapter.get_balance() for _, adapter in adapters],
                return_exceptions=True,
            )

            for (exchange_id, _), result in zip(adapters, results):
                if isinstance(result, Exception):
                    logger.warning(f"Failed to fetch balance for {exchange_id}: {result}")
                    continue

                usdt_balance = result.get("free", 0)
                logger.info(
                    f"  {exchange_id.upper()}: ${usdt_balance:,.2f}",
                    extra={
                        "action": "exchange_balance",
                        "exchange": exchange_id,
                        "balance_usdt": usdt_balance
                    }
                )
        except Exception as e:
            logger.error(f"Balance logging error: {e}")

    async def _journal_balance_snapshot(self) -> None:
        """Record a balance snapshot to the trade journal (every ~30min)."""
        try:
            balances = {}
            total = 0.0

            adapters: list[tuple[str, object]] = []
            for exchange_id in self._cfg.enabled_exchanges:
                adapter = self._exchanges.get(exchange_id)
                if adapter:
                    adapters.append((exchange_id, adapter))

            results = await asyncio.gather(
                *[adapter.get_balance() for _, adapter in adapters],
                return_exceptions=True,
            )

            for (exchange_id, _), result in zip(adapters, results):
                if isinstance(result, Exception):
                    logger.debug(f"Balance fetch failed for {exchange_id}: {result}")
                    balances[exchange_id] = None
                    continue

                usdt = float(result.get("free", 0))
                balances[exchange_id] = usdt
                total += usdt

            self._journal.balance_snapshot(balances, total=total)
        except Exception as e:
            logger.debug(f"Balance snapshot error: {e}")
