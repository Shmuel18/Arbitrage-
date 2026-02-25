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
        """Place order with timeout. Returns fill dict or None."""
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
                            await self._publisher.push_alert(alert_msg)
                        except Exception:
                            pass  # best-effort; logging is the fallback
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

    def _deregister_trade(self, trade: TradeRecord) -> None:
        """Remove trade and update derived sets; safe to call multiple times."""
        self._active_trades.pop(trade.trade_id, None)
        # Only release the symbol/exchange slots if no other trade holds them.
        remaining = self._active_trades.values()
        if not any(t.symbol == trade.symbol for t in remaining):
            self._active_symbols.discard(trade.symbol)
        if not any(
            t.long_exchange == trade.long_exchange or t.short_exchange == trade.long_exchange
            for t in remaining
        ):
            self._busy_exchanges.discard(trade.long_exchange)
        if not any(
            t.long_exchange == trade.short_exchange or t.short_exchange == trade.short_exchange
            for t in remaining
        ):
            self._busy_exchanges.discard(trade.short_exchange)
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
                asyncio.create_task(self._close_trade(trade))

        if stored:
            logger.info(f"Recovered {len(self._active_trades)} active trades")

    # ── Balance logging ───────────────────────────────────────────

    async def _log_exchange_balances(self) -> None:
        """Log current USDT balances for all exchanges."""
        try:
            logger.info("💰 EXCHANGE BALANCES", extra={"action": "balance_log"})
            
            for exchange_id in self._cfg.enabled_exchanges:
                adapter = self._exchanges.get(exchange_id)
                if not adapter:
                    continue
                
                try:
                    balance = await adapter.get_balance()
                    usdt_balance = balance.get("free", 0)
                    logger.info(
                        f"  {exchange_id.upper()}: ${usdt_balance:,.2f}",
                        extra={
                            "action": "exchange_balance",
                            "exchange": exchange_id,
                            "balance_usdt": usdt_balance
                        }
                    )
                except Exception as e:
                    logger.warning(f"Failed to fetch balance for {exchange_id}: {e}")
        except Exception as e:
            logger.error(f"Balance logging error: {e}")

    async def _journal_balance_snapshot(self) -> None:
        """Record a balance snapshot to the trade journal (every ~30min)."""
        try:
            balances = {}
            total = 0.0
            for exchange_id in self._cfg.enabled_exchanges:
                adapter = self._exchanges.get(exchange_id)
                if not adapter:
                    continue
                try:
                    bal = await adapter.get_balance()
                    usdt = float(bal.get("free", 0))
                    balances[exchange_id] = usdt
                    total += usdt
                except Exception:
                    balances[exchange_id] = None
            self._journal.balance_snapshot(balances, total=total)
        except Exception as e:
            logger.debug(f"Balance snapshot error: {e}")
