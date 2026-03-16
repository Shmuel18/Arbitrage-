"""
Execution controller mixin — monitor loop, upgrade check, reconciliation.

The _check_exit hold-or-exit logic is in _exit_logic_mixin.py.
Do NOT import this module directly; use ExecutionController from controller.py.
"""
from __future__ import annotations

import asyncio
import json
import time as _time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Dict, List, Optional

from src.core.contracts import (
    ExitReason,
    OrderSide,
    Position,
    TradeMode,
    TradeRecord,
    TradeState,
)
from src.core.logging import get_logger
from src.discovery.calculator import calculate_fees
from src.execution._exit_logic_mixin import _ExitLogicMixin

if TYPE_CHECKING:
    pass  # all attribute access via self (mixin pattern)

logger = get_logger("execution")

_HUNDRED: Decimal = Decimal("100")
_TWO: Decimal = Decimal("2")
_DEFAULT_TAKER_FEE: Decimal = Decimal("0.00075")  # conservative fallback when fee not yet loaded


class _MonitorMixin(_ExitLogicMixin):
    async def _exit_monitor_loop(self) -> None:
        reconcile_counter = 0
        balance_snapshot_counter = 0  # snapshot every 180 cycles (30min)
        while self._running:
            try:
                # ── Position reconciliation every ~2 min (12 × 10s) ──
                reconcile_counter += 1
                if reconcile_counter >= 12:
                    reconcile_counter = 0
                    await self._reconcile_positions()

                # ── Balance snapshot every ~30 min (180 × 10s) ──
                balance_snapshot_counter += 1
                if balance_snapshot_counter >= 180:
                    balance_snapshot_counter = 0
                    await self._journal_balance_snapshot()

                for trade_id, trade in list(self._active_trades.items()):
                    if not trade or trade.state != TradeState.OPEN:
                        continue
                    # Check for upgrade BEFORE normal exit check
                    upgraded = await self._check_upgrade(trade)
                    if upgraded:
                        continue  # trade was closed, skip exit check
                    await self._check_exit(trade)
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.error(f"Exit monitor error: {e}")
            await asyncio.sleep(10)

    async def _check_upgrade(self, trade: TradeRecord) -> bool:
        """Check if a significantly better opportunity exists.

        Reads qualified opportunities from Redis. If one has
        immediate_spread >= current_spread + upgrade_spread_delta
        AND is in the 15-min entry window → close current trade
        so the scanner can pick up the better one on next cycle.

        Returns True if the trade was closed for upgrade.
        """
        upgrade_delta = self._cfg.trading_params.upgrade_spread_delta
        if upgrade_delta <= 0:
            return False

        # ── Minimum hold time: never upgrade within first 3 minutes ──────────
        # Prevents rapid churn where trades are opened and immediately closed
        # for "better" opportunities before any value is captured.
        _MIN_UPGRADE_HOLD_SECONDS = self._cfg.trading_params.min_upgrade_hold_seconds
        if trade.opened_at:
            held_secs = (datetime.now(timezone.utc) - trade.opened_at).total_seconds()
            if held_secs < _MIN_UPGRADE_HOLD_SECONDS:
                return False

        # ── Never upgrade CHERRY_PICK trades ─────────────────────────────────
        # Cherry picks have a planned exit_before timestamp; upgrading them
        # defeats the purpose and risks missing the income payment.
        if trade.mode == TradeMode.CHERRY_PICK:
            return False

        # Get current trade's spread from cache (no REST call)
        long_adapter = self._exchanges.get(trade.long_exchange)
        short_adapter = self._exchanges.get(trade.short_exchange)
        try:
            long_funding = long_adapter.get_funding_rate_cached(trade.symbol)
            short_funding = short_adapter.get_funding_rate_cached(trade.symbol)
            if not long_funding or not short_funding:
                return False
        except Exception as exc:
            logger.debug(f"Upgrade check: failed to read cached funding for {trade.symbol}: {exc}")
            return False

        # ── Funding-proximity lock ────────────────────────────────────────────
        # Block upgrade if the CURRENT trade's next funding is within the lock
        # window (default 3 min). This prevents exiting a position right before
        # collecting the funding payment we opened the trade to capture.
        upgrade_funding_lock_secs = self._cfg.trading_params.upgrade_funding_lock_secs
        # 3-minute hard lock before funding for ALL modes.
        # Within 3min, exiting costs more than any realistic gain from switching.
        # Outside 3min, the net_pct comparison + basis guard handle the decision.
        if upgrade_funding_lock_secs > 0:
            now_ms = _time.time() * 1000
            # Prefer live cache timestamps; fall back to TradeRecord fields.
            # P3-2: Normalise to ms — some exchanges deliver epoch-seconds.
            def _to_ms(ts: Optional[float]) -> Optional[float]:
                if ts is None:
                    return None
                return ts * 1000 if ts < 1e12 else ts
            long_next_ts = _to_ms(long_funding.get("next_timestamp"))
            short_next_ts = _to_ms(short_funding.get("next_timestamp"))
            current_next_ts: Optional[float] = None
            if long_next_ts is not None and short_next_ts is not None:
                current_next_ts = min(long_next_ts, short_next_ts)
            elif long_next_ts is not None:
                current_next_ts = long_next_ts
            elif short_next_ts is not None:
                current_next_ts = short_next_ts
            # Fall back to TradeRecord datetime fields if cache has no timestamp
            if current_next_ts is None:
                if trade.next_funding_long:
                    current_next_ts = trade.next_funding_long.timestamp() * 1000
                if trade.next_funding_short:
                    short_ms = trade.next_funding_short.timestamp() * 1000
                    if current_next_ts is None or short_ms < current_next_ts:
                        current_next_ts = short_ms
            if current_next_ts is None:
                # No funding timestamp available — default to BLOCKING upgrades.
                # Without a timestamp we cannot verify it's safe to exit.
                logger.info(
                    f"🔒 Upgrade blocked for {trade.symbol}: "
                    f"no funding timestamp available — defaulting to block",
                    extra={
                        "trade_id": trade.trade_id,
                        "symbol": trade.symbol,
                        "action": "upgrade_blocked_no_timestamp",
                    },
                )
                return False
            secs_to_funding = (current_next_ts - now_ms) / 1000
            if 0 < secs_to_funding <= upgrade_funding_lock_secs:
                logger.info(
                    f"🔒 Upgrade blocked for {trade.symbol}: "
                    f"funding in {int(secs_to_funding)}s "
                    f"(lock={upgrade_funding_lock_secs}s)",
                    extra={
                        "trade_id": trade.trade_id,
                        "symbol": trade.symbol,
                        "action": "upgrade_blocked_funding_lock",
                        "secs_to_funding": int(secs_to_funding),
                    },
                )
                return False
            # Also block if funding JUST fired (secs_to_funding <= 0) and
            # the exit logic hasn't yet recorded the collection.
            if secs_to_funding <= 0 and not trade._exit_check_active:
                logger.info(
                    f"🔒 Upgrade blocked for {trade.symbol}: "
                    f"funding just fired ({int(secs_to_funding)}s ago) "
                    f"but not yet recorded — waiting for exit logic",
                    extra={
                        "trade_id": trade.trade_id,
                        "symbol": trade.symbol,
                        "action": "upgrade_blocked_funding_just_fired",
                    },
                )
                return False
        # ─────────────────────────────────────────────────────────────────────

        # Projected net for current trade: income spread minus round-trip fees.
        # This mirrors the scanner's net_pct formula so comparisons are apples-to-apples.
        current_immediate = (-long_funding["rate"] + short_funding["rate"]) * _HUNDRED
        fee_per_side = (
            (trade.long_taker_fee or _DEFAULT_TAKER_FEE)
            + (trade.short_taker_fee or _DEFAULT_TAKER_FEE)
        )
        fee_roundtrip_pct = fee_per_side * _TWO * _HUNDRED  # open + close
        current_projected_net = current_immediate - fee_roundtrip_pct

        # Read latest opportunities from Redis
        try:
            raw = await self._redis.get("trinity:opportunities")
            if not raw:
                return False
            data = json.loads(raw)
            # P2-1: Guard against stale snapshot — if the scanner paused/crashed,
            # opportunities may be minutes old; upgrading on that data risks
            # closing a profitable trade for an opportunity that no longer exists.
            _updated_at_str = data.get("updated_at")
            if _updated_at_str:
                try:
                    _updated_at = datetime.fromisoformat(
                        _updated_at_str.replace("Z", "+00:00")
                    )
                    _snapshot_age_s = (
                        datetime.now(timezone.utc) - _updated_at
                    ).total_seconds()
                    if _snapshot_age_s > 60:
                        logger.debug(
                            f"Upgrade check: skipping — opportunities snapshot is "
                            f"{int(_snapshot_age_s)}s old (threshold=60s)",
                        )
                        return False
                except (ValueError, TypeError):
                    pass  # malformed timestamp — proceed; age guard is best-effort
            candidates = data.get("opportunities", [])
        except Exception as e:
            logger.debug(f"Upgrade check: cannot read opportunities: {e}")
            return False

        entry_offset = self._cfg.trading_params.entry_offset_seconds
        now_ms = _time.time() * 1000
        # Threshold uses projected net (income - fees) so NUTCRACKER's high rate is
        # never beaten by a lower-rate candidate just because its immediate price is positive.
        threshold = current_projected_net + upgrade_delta

        for cand in candidates:
            if not cand.get("qualified", False):
                continue

            cand_symbol = cand.get("symbol", "")
            cand_long = cand.get("long_exchange", "")
            cand_short = cand.get("short_exchange", "")
            # Use net_pct (projected income - fees) for comparison, not immediate_spread.
            # This ensures a NUTCRACKER with high rate isn't displaced by a candidate
            # whose immediate price spread looks better but earns less total income.
            cand_spread = Decimal(str(cand.get("net_pct", cand.get("immediate_spread_pct", 0))))
            same_symbol = cand_symbol == trade.symbol

            if same_symbol:
                # Same symbol — only upgrade if the exchange pair is DIFFERENT
                if cand_long == trade.long_exchange and cand_short == trade.short_exchange:
                    continue
                # Ensure candidate's exchanges aren't busy with OTHER trades
                other_busy: set[str] = set()
                for t in self._active_trades.values():
                    if t.trade_id != trade.trade_id:
                        other_busy.add(t.long_exchange)
                        other_busy.add(t.short_exchange)
                if cand_long in other_busy or cand_short in other_busy:
                    continue
                # Compare projected net (next funding payment income - fees)
                if cand_spread < threshold:
                    continue
            else:
                if cand_spread < threshold:
                    continue

            # Must be in the entry window
            next_ms = cand.get("next_funding_ms")
            if next_ms is None:
                continue
            seconds_until = (next_ms - now_ms) / 1000
            if not (0 < seconds_until <= entry_offset):
                continue

            # ── Basis Guard: only upgrade if exit basis is favorable/neutral ──
            try:
                _lt = await long_adapter.get_ticker(trade.symbol)
                _st = await short_adapter.get_ticker(trade.symbol)
                _lp = Decimal(str(_lt.get("last") or _lt.get("close") or 0))
                _sp = Decimal(str(_st.get("last") or _st.get("close") or 0))
                if _lp > 0 and _sp > 0:
                    current_basis = (_lp - _sp) / _sp * Decimal("100")
                    entry_basis = trade.entry_basis_pct or Decimal("0")
                    # Block upgrade if exit basis is BELOW entry — short rose more than long,
                    # meaning we'd exit at a price loss. Wait for basis to recover.
                    if current_basis < entry_basis:
                        logger.info(
                            f"🔒 Upgrade blocked for {trade.symbol} by basis: "
                            f"current={float(current_basis):+.4f}% < entry={float(entry_basis):+.4f}% (adverse)",
                            extra={"trade_id": trade.trade_id, "symbol": trade.symbol, "action": "upgrade_blocked_basis"}
                        )
                        continue
            except Exception as _e:
                logger.debug(f"Upgrade basis check failed for {trade.symbol}: {_e}")

            # Found a significantly better opportunity — upgrade!
            hold_min = 0
            if trade.opened_at:
                hold_min = int(
                    (datetime.now(timezone.utc) - trade.opened_at).total_seconds() / 60
                )
            upgrade_type = "PAIR SWITCH" if same_symbol else "UPGRADE"
            logger.info(
                f"⬆️  {upgrade_type}: closing {trade.symbol} on "
                f"{trade.long_exchange}↔{trade.short_exchange} (net {float(current_projected_net):.4f}%) "
                f"→ {cand_symbol} on {cand_long}↔{cand_short} (net {float(cand_spread):.4f}%) — "
                f"delta {float(cand_spread - current_projected_net):.4f}% "
                f"≥ {float(upgrade_delta):.2f}% (held {hold_min}min)",
                extra={
                    "trade_id": trade.trade_id,
                    "symbol": trade.symbol,
                    "action": "upgrade_exit",
                    "upgrade_to": cand_symbol,
                    "upgrade_pair": f"{cand_long}_{cand_short}",
                },
            )
            # Re-arm grace period BEFORE closing to prevent risk guard
            # from seeing transient unhedged positions during the switch
            if self._risk_guard:
                self._risk_guard.mark_trade_opened(trade.symbol)
                if cand_symbol != trade.symbol:
                    self._risk_guard.mark_trade_opened(cand_symbol)
                logger.info(f"✅ Grace period re-armed for {upgrade_type} on {trade.symbol}")
            trade._exit_reason = ExitReason.UPGRADE_EXIT.value
            await self._close_trade(trade)
            # Set upgrade cooldown so the closed symbol doesn't immediately re-enter
            cooldown_sec = self._cfg.trading_params.upgrade_cooldown_seconds
            self._upgrade_cooldown[trade.symbol] = _time.time() + cooldown_sec
            logger.info(
                f"⬆️ Upgrade cooldown set for {trade.symbol}: {cooldown_sec}s",
                extra={"symbol": trade.symbol, "action": "upgrade_cooldown_set"},
            )
            return True

        return False

    # ── Position reconciliation (detect manual closes) ──────────

    async def _reconcile_positions(self) -> None:
        """Detect trades that were manually closed on the exchange.

        For each active OPEN trade, fetch real positions from both exchanges.
        - Both legs gone   -> fully manually closed -> clean up state
        - One leg gone     -> partial manual close  -> close remaining leg
        - Both legs exist  -> normal, do nothing
        """
        if not self._active_trades:
            return

        # Collect exchanges that have active trades
        exchanges_needed: set[str] = set()
        for trade in self._active_trades.values():
            if trade.state == TradeState.OPEN:
                exchanges_needed.add(trade.long_exchange)
                exchanges_needed.add(trade.short_exchange)

        if not exchanges_needed:
            return

        # One REST call per exchange to get all positions
        exchange_positions: Dict[str, List[Position]] = {}
        for exch_id in exchanges_needed:
            adapter = self._exchanges.get(exch_id)
            if not adapter:
                continue
            try:
                positions = await adapter.get_positions()
                exchange_positions[exch_id] = positions
            except Exception as e:
                logger.warning(
                    f"Reconcile: failed to fetch positions from {exch_id}: {e}",
                    extra={"exchange": exch_id, "action": "reconcile_error"},
                )
                # Don't act on incomplete data — skip this cycle entirely
                return

        # Check each active trade against real positions
        for trade_id in list(self._active_trades):
            trade = self._active_trades.get(trade_id)
            if not trade or trade.state != TradeState.OPEN:
                continue

            long_positions = exchange_positions.get(trade.long_exchange, [])
            short_positions = exchange_positions.get(trade.short_exchange, [])

            long_exists = any(
                p.symbol == trade.symbol and p.side == OrderSide.BUY
                for p in long_positions
            )
            short_exists = any(
                p.symbol == trade.symbol and p.side == OrderSide.SELL
                for p in short_positions
            )

            if long_exists and short_exists:
                continue  # both legs intact

            if not long_exists and not short_exists:
                # ── Fully manually closed ─────────────────────────
                logger.warning(
                    f"MANUAL CLOSE DETECTED: Trade {trade.trade_id} ({trade.symbol}) -- "
                    f"no positions on {trade.long_exchange} or {trade.short_exchange}. "
                    f"Removing from active trades.",
                    extra={
                        "trade_id": trade.trade_id,
                        "symbol": trade.symbol,
                        "action": "manual_close_detected",
                    },
                )
                trade.state = TradeState.CLOSED
                trade.closed_at = datetime.now(timezone.utc)
                await self._record_manual_close(trade)
                await self._redis.delete_trade_state(trade.trade_id)
                self._deregister_trade(trade)

            elif not long_exists:
                # ── Long leg gone, short remains ──────────────────
                logger.warning(
                    f"PARTIAL MANUAL CLOSE: Trade {trade.trade_id} ({trade.symbol}) -- "
                    f"long on {trade.long_exchange} GONE. "
                    f"Closing remaining short on {trade.short_exchange}.",
                    extra={
                        "trade_id": trade.trade_id,
                        "symbol": trade.symbol,
                        "action": "partial_manual_close",
                    },
                )
                short_adapter = self._exchanges.get(trade.short_exchange)
                if short_adapter:
                    await self._close_orphan(
                        short_adapter, trade.short_exchange, trade.symbol,
                        OrderSide.BUY, {"filled": float(trade.short_qty)},
                    )
                trade.state = TradeState.CLOSED
                trade.closed_at = datetime.now(timezone.utc)
                await self._record_manual_close(trade)
                await self._redis.delete_trade_state(trade.trade_id)
                self._deregister_trade(trade)

            else:
                # ── Short leg gone, long remains ──────────────────
                logger.warning(
                    f"PARTIAL MANUAL CLOSE: Trade {trade.trade_id} ({trade.symbol}) -- "
                    f"short on {trade.short_exchange} GONE. "
                    f"Closing remaining long on {trade.long_exchange}.",
                    extra={
                        "trade_id": trade.trade_id,
                        "symbol": trade.symbol,
                        "action": "partial_manual_close",
                    },
                )
                long_adapter = self._exchanges.get(trade.long_exchange)
                if long_adapter:
                    await self._close_orphan(
                        long_adapter, trade.long_exchange, trade.symbol,
                        OrderSide.SELL, {"filled": float(trade.long_qty)},
                    )
                trade.state = TradeState.CLOSED
                trade.closed_at = datetime.now(timezone.utc)
                await self._record_manual_close(trade)
                await self._redis.delete_trade_state(trade.trade_id)
                self._deregister_trade(trade)

    # ── Close trade ──────────────────────────────────────────────

