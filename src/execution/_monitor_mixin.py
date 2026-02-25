"""
Execution controller mixin — methods extracted from controller.py.
Do NOT import this module directly; use ExecutionController from controller.py.
"""
from __future__ import annotations

import asyncio
import time as _time
import json
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Dict, List, Optional

from src.core.contracts import (
    ExitReason,
    OpportunityCandidate,
    OrderRequest,
    OrderSide,
    Position,
    TradeMode,
    TradeRecord,
    TradeState,
)
from src.core.logging import get_logger
from src.core.journal import get_journal
from src.discovery.calculator import calculate_fees
from src.execution.blacklist import BlacklistManager
from src.execution.sizer import PositionSizer
from src.execution import helpers as _h

if TYPE_CHECKING:
    from src.core.config import Config
    from src.exchanges.adapter import ExchangeManager
    from src.storage.redis_client import RedisClient
    from src.risk.guard import RiskGuard
    from src.api.publisher import APIPublisher

logger = get_logger("execution")


class _MonitorMixin:
    async def _exit_monitor_loop(self) -> None:
        reconcile_counter = 0
        balance_snapshot_counter = 0  # snapshot every 60 cycles (30min)
        while self._running:
            try:
                # ── Position reconciliation every ~2 min (4 × 30s) ──
                reconcile_counter += 1
                if reconcile_counter >= 4:
                    reconcile_counter = 0
                    await self._reconcile_positions()

                # ── Balance snapshot every ~30 min (60 × 30s) ──
                balance_snapshot_counter += 1
                if balance_snapshot_counter >= 60:
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
            await asyncio.sleep(30)

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

        # Get current trade's spread from cache (no REST call)
        long_adapter = self._exchanges.get(trade.long_exchange)
        short_adapter = self._exchanges.get(trade.short_exchange)
        try:
            long_funding = long_adapter.get_funding_rate_cached(trade.symbol)
            short_funding = short_adapter.get_funding_rate_cached(trade.symbol)
            if not long_funding or not short_funding:
                return False
        except Exception:
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
            # Prefer live cache timestamps; fall back to TradeRecord fields
            long_next_ts = long_funding.get("next_timestamp")
            short_next_ts = short_funding.get("next_timestamp")
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
            if current_next_ts is not None:
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
        # ─────────────────────────────────────────────────────────────────────

        # Projected net for current trade: income spread minus round-trip fees.
        # This mirrors the scanner's net_pct formula so comparisons are apples-to-apples.
        current_immediate = (-long_funding["rate"] + short_funding["rate"]) * Decimal("100")
        fee_per_side = (
            (trade.long_taker_fee or Decimal("0.00075"))
            + (trade.short_taker_fee or Decimal("0.00075"))
        )
        fee_roundtrip_pct = fee_per_side * Decimal("2") * Decimal("100")  # open + close
        current_projected_net = current_immediate - fee_roundtrip_pct

        # Read latest opportunities from Redis
        try:
            raw = await self._redis.get("trinity:opportunities")
            if not raw:
                return False
            data = json.loads(raw)
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

    async def _check_exit(self, trade: TradeRecord) -> None:
        """Check if trade should be closed.

        Two modes:
          CHERRY_PICK: exit BEFORE the costly funding payment
          HOLD:        exit when edge reverses (both sides still income)
        """
        now = datetime.now(timezone.utc)

        # ── CHERRY_PICK: hard stop before costly payment ─────────
        if trade.mode == TradeMode.CHERRY_PICK and trade.exit_before:
            if now >= trade.exit_before:
                logger.info(
                    f"Cherry-pick hard exit for {trade.trade_id}: "
                    f"exiting before costly payment at {trade.exit_before.strftime('%H:%M UTC')}",
                    extra={"trade_id": trade.trade_id, "symbol": trade.symbol, "action": "exit_signal"},
                )
                await self._close_trade(trade)
                return
            # Don't return — fall through to spread check below (same as HOLD)

        # ── HOLD: use cached rates (no REST call) ─────────────────
        long_adapter = self._exchanges.get(trade.long_exchange)
        short_adapter = self._exchanges.get(trade.short_exchange)

        long_funding = long_adapter.get_funding_rate_cached(trade.symbol)
        short_funding = short_adapter.get_funding_rate_cached(trade.symbol)
        if not long_funding or not short_funding:
            logger.debug(f"No cached funding for {trade.symbol} — skipping exit check")
            return

        # Track next funding time per exchange (update when stale)
        # _funding_paid_* flags indicate we already collected this cycle's payment
        # and are in continuous hold-or-exit monitoring. Don't advance trackers
        # until we explicitly decide to HOLD for the next cycle.
        #
        # IMPORTANT: When old tracker value < now (funding time has passed),
        # only update if the new candidate is ALSO in the past (stale correction).
        # If candidate is in the future, the funding was just PAID — don't advance
        # yet, so the exit_offset check below can fire and trigger hold/exit.
        long_next_ts = long_funding.get("next_timestamp")
        if long_next_ts:
            candidate_long = datetime.fromtimestamp(long_next_ts / 1000, tz=timezone.utc)
            if not trade.next_funding_long or (
                trade.next_funding_long < now
                and not trade._funding_paid_long
                and candidate_long <= now  # only correct stale data, don't jump to future
            ):
                trade.next_funding_long = candidate_long
                li = long_funding.get("interval_hours", "?")
                logger.info(f"Trade {trade.trade_id}: {trade.long_exchange} next at "
                            f"{trade.next_funding_long.strftime('%H:%M UTC')} (every {li}h)")

        short_next_ts = short_funding.get("next_timestamp")
        if short_next_ts:
            candidate_short = datetime.fromtimestamp(short_next_ts / 1000, tz=timezone.utc)
            if not trade.next_funding_short or (
                trade.next_funding_short < now
                and not trade._funding_paid_short
                and candidate_short <= now  # only correct stale data, don't jump to future
            ):
                trade.next_funding_short = candidate_short
                si = short_funding.get("interval_hours", "?")
                logger.info(f"Trade {trade.trade_id}: {trade.short_exchange} next at "
                            f"{trade.next_funding_short.strftime('%H:%M UTC')} (every {si}h)")

        # ── Display current spread & time until next payment ──────
        # Immediate spread: next payment only — no 8h normalization
        immediate_spread = (-long_funding["rate"] + short_funding["rate"]) * Decimal("100")
        
        long_until = None
        short_until = None
        if trade.next_funding_long:
            long_until = int((trade.next_funding_long - now).total_seconds() / 60)
        if trade.next_funding_short:
            short_until = int((trade.next_funding_short - now).total_seconds() / 60)
        
        long_str = f"{long_until}min" if long_until is not None else "?"
        short_str = f"{short_until}min" if short_until is not None else "?"
        # If funding already paid, show next funding from API instead
        if long_until is not None and long_until < 0 and long_next_ts:
            api_long = datetime.fromtimestamp(long_next_ts / 1000, tz=timezone.utc)
            api_long_min = int((api_long - now).total_seconds() / 60)
            long_str = f"PAID (next {api_long_min}min)"
        if short_until is not None and short_until < 0 and short_next_ts:
            api_short = datetime.fromtimestamp(short_next_ts / 1000, tz=timezone.utc)
            api_short_min = int((api_short - now).total_seconds() / 60)
            short_str = f"PAID (next {api_short_min}min)"
        
        logger.info(
            f"🔔 {trade.symbol}: Immediate Spread = {float(immediate_spread):.4f}% | "
            f"{trade.long_exchange} in {long_str} | {trade.short_exchange} in {short_str}",
            extra={"trade_id": trade.trade_id, "symbol": trade.symbol, "action": "spread_update"},
        )

        # Wait until BOTH have paid, then wait exit_offset (15 min) after payment
        exit_offset = self._cfg.trading_params.exit_offset_seconds  # 900 = 15 min
        
        if trade.next_funding_long:
            long_exit_time = trade.next_funding_long + timedelta(seconds=exit_offset)
            long_paid = now >= long_exit_time
        else:
            long_paid = False
        
        if trade.next_funding_short:
            short_exit_time = trade.next_funding_short + timedelta(seconds=exit_offset)
            short_paid = now >= short_exit_time
        else:
            short_paid = False

        # Exit once ANY funding has paid + offset elapsed (grab and run)
        if not (long_paid or short_paid):
            return

        # Mark that this cycle's funding has been collected —
        # prevents tracker auto-advance so we keep checking every 30s.
        if long_paid:
            trade._funding_paid_long = True
        if short_paid:
            trade._funding_paid_short = True

        which_paid = "long" if long_paid else "short"
        # Log first detection only (avoid spamming every 30s)
        if not trade._exit_check_active:
            trade._exit_check_active = True
            logger.info(
                f"Trade {trade.trade_id}: {which_paid} funding paid + {exit_offset}s elapsed — evaluating hold/exit",
                extra={"trade_id": trade.trade_id, "symbol": trade.symbol, "action": "exit_trigger"},
            )
            # ── Per-payment tracking (SIGNED logic) ──────────────
            # Prefer the live cache rate (updated by the watcher every cycle)
            # for the CURRENT payment.  The entry-time rate on the trade object
            # is only a fallback for the case where the live cache is empty.
            # Using stale entry rates causes cumulative drift on multi-cycle trades.
            _live_long = long_adapter.get_funding_rate_cached(trade.symbol) if long_adapter else None
            _live_short = short_adapter.get_funding_rate_cached(trade.symbol) if short_adapter else None

            _lr = (
                Decimal(str(_live_long["rate"])) if (_live_long and long_paid and "rate" in _live_long)
                else (trade.long_funding_rate if long_paid else None)
            )
            _sr = (
                Decimal(str(_live_short["rate"])) if (_live_short and short_paid and "rate" in _live_short)
                else (trade.short_funding_rate if short_paid else None)
            )
            
            # Long side: income if rate < 0, cost if rate > 0
            _long_usd = float((trade.entry_price_long or Decimal('0')) * trade.long_qty * (-(Decimal(str(_lr or 0))))) if _lr else 0
            # Short side: income if rate > 0, cost if rate < 0
            _short_usd = float((trade.entry_price_short or Decimal('0')) * trade.short_qty * (Decimal(str(_sr or 0)))) if _sr else 0
            
            _net_usd = _long_usd + _short_usd

            trade.funding_collections += 1
            trade.funding_collected_usd += Decimal(str(_net_usd))

            # Journal: log individual funding payment detection
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

            # Journal: log this collection cycle with full detail
            self._journal.funding_collected(
                trade.trade_id, trade.symbol,
                collection_num=trade.funding_collections,
                long_exchange=trade.long_exchange,
                short_exchange=trade.short_exchange,
                long_rate=_lr,
                short_rate=_sr,
                long_payment_usd=_long_usd,
                short_payment_usd=_short_usd,
                net_payment_usd=_net_usd,
                cumulative_usd=float(trade.funding_collected_usd),
                immediate_spread=float(immediate_spread),
            )
            logger.info(
                f"💰 [{trade.symbol}] Funding collection #{trade.funding_collections}: "
                f"~${_net_usd:.4f} this cycle | cumulative ~${float(trade.funding_collected_usd):.4f}",
                extra={"trade_id": trade.trade_id, "symbol": trade.symbol, "action": "funding_collected"},
            )
            # Mark snapshot timer start
            trade._funding_paid_at = now

        # Check if still profitable to hold (funding spread)
        quick_cycle = self._cfg.trading_params.quick_cycle
        hold_min = 0
        if trade.opened_at:
            hold_min = int((now - trade.opened_at).total_seconds() / 60)

        if quick_cycle:
            # ── Hold-or-Exit: check if IMMEDIATE spread (actual next payment)
            #    meets threshold — NOT the normalized spread ──
            hold_min_spread = self._cfg.trading_params.hold_min_spread
            _long_adp = self._exchanges.get(trade.long_exchange)
            _short_adp = self._exchanges.get(trade.short_exchange)
            _lf = _long_adp.get_cached_instrument_spec(trade.symbol) if _long_adp else None
            _sf = _short_adp.get_cached_instrument_spec(trade.symbol) if _short_adp else None
            _exit_fee_pct = (
                ((_lf.taker_fee if _lf else Decimal("0.0006")) +
                 (_sf.taker_fee if _sf else Decimal("0.0006"))) * 2 * Decimal("100")
            )
            immediate_spread_net = immediate_spread - _exit_fee_pct

            # ── Live price basis at hold/exit decision ────────────
            # At exit: selling long, buying back short.
            # Favorable basis = long_price >= short_price (sell expensive, buy back cheap).
            _l_price = Decimal("0")
            _s_price = Decimal("0")
            exit_basis = Decimal("0")
            _adverse_exit_basis = Decimal("0")
            _basis_favorable = None  # None = unknown (prices unavailable)
            try:
                _l_ticker = await long_adapter.get_ticker(trade.symbol)
                _s_ticker = await short_adapter.get_ticker(trade.symbol)
                _l_price = Decimal(str(_l_ticker.get("last") or _l_ticker.get("close") or 0))
                _s_price = Decimal(str(_s_ticker.get("last") or _s_ticker.get("close") or 0))
                if _l_price > 0 and _s_price > 0:
                    # Exit basis: same formula as entry — (long − short) / short × 100
                    exit_basis = (_l_price - _s_price) / _s_price * Decimal("100")
                    # Break-even: exit_basis must be >= entry_basis.
                    # P&L ≈ qty × (exit_basis − entry_basis):
                    #   exit_basis > entry_basis → long rose more than short → profit
                    #   exit_basis < entry_basis → short rose more than long → loss
                    _entry_basis = trade.entry_basis_pct if trade.entry_basis_pct is not None else Decimal("0")
                    _adverse_exit_basis = max(_entry_basis - exit_basis, Decimal("0"))  # loss when exit < entry
                    _basis_favorable = exit_basis >= _entry_basis
                    if _adverse_exit_basis > Decimal("0"):
                        immediate_spread_net -= _adverse_exit_basis
                        logger.debug(
                            f"[{trade.symbol}] Adverse exit basis vs entry: "
                            f"exit={float(exit_basis):.4f}% < entry={float(_entry_basis):.4f}% "
                            f"→ −{float(_adverse_exit_basis):.4f}% from hold spread"
                        )
            except Exception as _eb:
                logger.debug(f"[{trade.symbol}] Exit basis check failed: {_eb}")

            if immediate_spread_net >= hold_min_spread:
                # Net spread still good — but check if next funding is too far away.
                # No point holding capital for hours when we could redeploy it.
                hold_max_wait = self._cfg.trading_params.hold_max_wait_seconds
                
                # ── Basis Check for Profitability Branch ──
                # Even if spread is high, if quick_cycle is true, we want to try to exit.
                # But we ONLY exit if basis is favorable.
                if _basis_favorable is False:
                    _wait_max_sec = 1800 # 30 min
                    _wait_start = trade._exit_wait_start
                    if _wait_start is None:
                        trade._exit_wait_start = now
                        logger.info(
                            f"⏳ Trade {trade.trade_id}: PROFITABLE BUT ADVERSE BASIS — waiting up to 30min "
                            f"(spread {float(immediate_spread):.4f}% >= {float(hold_min_spread):.2f}% "
                            f"but basis {float(exit_basis):.4f}% < entry — short moved against us)",
                            extra={"trade_id": trade.trade_id, "symbol": trade.symbol, "action": "basis_wait_profitable"}
                        )
                        return
                    
                    _waited_sec = (now - _wait_start).total_seconds()
                    if _waited_sec < _wait_max_sec:
                        logger.debug(f"⏳ Trade {trade.trade_id}: still waiting for basis ({int(_waited_sec/60)}min)")
                        return
                    
                    # 30 minutes reached and basis still bad. 
                    # Decision: Since spread is high (immediate_spread_net >= hold_min_spread),
                    # we do NOT force exit. Instead, we reset and STAY for next cycle.
                    logger.info(
                        f"🔄 Trade {trade.trade_id}: BASIS STILL BAD AFTER 30m, BUT FUNDING IS HIGH. "
                        f"Staying for next cycle to collect more funding instead of forcing exit.",
                        extra={"trade_id": trade.trade_id, "symbol": trade.symbol, "action": "stay_high_funding"}
                    )
                    trade._exit_wait_start = None
                    # Continue below to standard HOLD logic (1-hour check)
                else:
                    # Basis is favorable (or unknown) — we can exit or hold.
                    # Since quick_cycle=true, if next funding is far (>1h), we exit.
                    trade._exit_wait_start = None

                if hold_max_wait > 0:
                    long_next = long_funding.get("next_timestamp")
                    short_next = short_funding.get("next_timestamp")
                    # Find the NEAREST next funding across both sides (only look at the future)
                    next_funding_candidates = []
                    now_ts = now.timestamp()
                    if long_next:
                        ts = long_next / 1000
                        if ts > now_ts: next_funding_candidates.append(ts)
                    if short_next:
                        ts = short_next / 1000
                        if ts > now_ts: next_funding_candidates.append(ts)
                    
                    if next_funding_candidates:
                        nearest_sec = min(next_funding_candidates) - now_ts
                        if nearest_sec > hold_max_wait:
                            nearest_min = int(nearest_sec / 60)
                            logger.info(
                                f"🔄 Trade {trade.trade_id}: EXIT — spread {float(immediate_spread):.4f}% "
                                f"≥ {float(hold_min_spread):.2f}% BUT next funding in {nearest_min}min "
                                f"> max wait {hold_max_wait // 60}min — freeing capital (held {hold_min}min)",
                                extra={
                                    "trade_id": trade.trade_id,
                                    "symbol": trade.symbol,
                                    "action": "hold_max_wait_exit",
                                },
                            )
                            trade._exit_reason = f'max_wait_{nearest_min}min'
                            self._journal.exit_decision(
                                trade.trade_id, trade.symbol,
                                reason=f'max_wait (next funding {nearest_min}min > {hold_max_wait//60}min)',
                                immediate_spread=immediate_spread, hold_min=hold_min,
                            )
                            await self._close_trade(trade)
                            return
                    else:
                        # No future funding timestamps found? 
                        # This usually means the exchange hasn't rolled over yet 
                        # OR we are at the end of a series. To be safe in quick_cycle, 
                        # we wait a few cycles but if it persists, we exit.
                        pass

                # Cherry-pick: if the costly payment (exit_before) is within
                # hold_max_wait, there is no room for another profitable cycle —
                # exit now instead of holding toward the costly payment.
                if trade.mode == TradeMode.CHERRY_PICK and trade.exit_before:
                    secs_until_cost = (trade.exit_before - now).total_seconds()
                    if secs_until_cost <= hold_max_wait:
                        cost_min = int(secs_until_cost / 60)
                        logger.info(
                            f"🍒 Trade {trade.trade_id}: EXIT — cherry_pick costly payment in "
                            f"{cost_min}min ≤ max_wait {hold_max_wait // 60}min — "
                            f"no room for next cycle (held {hold_min}min)",
                            extra={
                                "trade_id": trade.trade_id,
                                "symbol": trade.symbol,
                                "action": "cherry_pick_cost_exit",
                            },
                        )
                        trade._exit_reason = f'cherry_pick_cost_in_{cost_min}min'
                        self._journal.exit_decision(
                            trade.trade_id, trade.symbol,
                            reason=f'cherry_pick costly payment in {cost_min}min ≤ {hold_max_wait // 60}min wait',
                            immediate_spread=immediate_spread, hold_min=hold_min,
                        )
                        await self._close_trade(trade)
                        return

                # Still within acceptable wait time — keep holding.
                # Log HOLD decision periodically (every 5 min) to avoid spam.
                # Do NOT advance trackers — keep gate open so we check every 30s.
                if not trade._hold_logged_until or trade._hold_logged_until < now:
                    # Show next funding from API (for display only)
                    _long_next = long_funding.get("next_timestamp")
                    _short_next = short_funding.get("next_timestamp")
                    next_long_str = datetime.fromtimestamp(
                        _long_next / 1000, tz=timezone.utc
                    ).strftime('%H:%M') if _long_next else '?'
                    next_short_str = datetime.fromtimestamp(
                        _short_next / 1000, tz=timezone.utc
                    ).strftime('%H:%M') if _short_next else '?'
                    # Calculate time until next funding for display
                    _nearest_min = '?'
                    _candidates = []
                    if _long_next:
                        _candidates.append(_long_next / 1000)
                    if _short_next:
                        _candidates.append(_short_next / 1000)
                    if _candidates:
                        _nearest_min = f"{int((min(_candidates) - now.timestamp()) / 60)}min"
                    trade._hold_logged_until = now + timedelta(minutes=5)
                    logger.info(
                        f"🔄 Trade {trade.trade_id}: HOLD — immediate spread {float(immediate_spread):.4f}% "
                        f"≥ {float(hold_min_spread):.2f}% threshold (held {hold_min}min) | "
                        f"Next funding in {_nearest_min} — "
                        f"{trade.long_exchange}={next_long_str}, "
                        f"{trade.short_exchange}={next_short_str}",
                        extra={
                            "trade_id": trade.trade_id,
                            "symbol": trade.symbol,
                            "action": "hold_after_payment",
                        },
                    )
                    self._journal.hold_decision(
                        trade.trade_id, trade.symbol,
                        immediate_spread=immediate_spread,
                        next_funding_min=_nearest_min,
                    )
                    # ── 5-min position snapshot (price + spread + unrealized PnL) ──
                    _min_since = int((now - trade._funding_paid_at).total_seconds() / 60) if trade._funding_paid_at else hold_min
                    try:
                        _l_ticker = await long_adapter.get_ticker(trade.symbol)
                        _s_ticker = await short_adapter.get_ticker(trade.symbol)
                        _l_price = Decimal(str(_l_ticker.get("last", 0)))
                        _s_price = Decimal(str(_s_ticker.get("last", 0)))
                        # Unrealized price PnL: long gains when price rises, short loses and vice-versa
                        _long_pnl_usd = float((_l_price - (trade.entry_price_long or _l_price)) * trade.long_qty)
                        _short_pnl_usd = float(((trade.entry_price_short or _s_price) - _s_price) * trade.short_qty)
                        _price_pnl_usd = _long_pnl_usd + _short_pnl_usd
                        self._journal.position_snapshot(
                            trade.trade_id, trade.symbol,
                            minutes_since_funding=_min_since,
                            long_exchange=trade.long_exchange,
                            short_exchange=trade.short_exchange,
                            long_price=float(_l_price),
                            short_price=float(_s_price),
                            immediate_spread=float(immediate_spread),
                            long_pnl_usd=_long_pnl_usd,
                            short_pnl_usd=_short_pnl_usd,
                            price_pnl_usd=_price_pnl_usd,
                            funding_collected_usd=float(trade.funding_collected_usd),
                        )
                    except Exception as _snap_err:
                        logger.debug(f"Snapshot fetch failed for {trade.symbol}: {_snap_err}")
                return
            else:
                # Spread dropped below threshold.
                # Wait for favorable price basis before exiting (max 30 min).
                _wait_max_sec = 1800 # 30 min
                _wait_start = trade._exit_wait_start
                _waited_sec = (now - _wait_start).total_seconds() if _wait_start else 0

                if _basis_favorable is True or _basis_favorable is None or _waited_sec >= _wait_max_sec:
                    # Exit now: basis is favorable OR 30-min timeout reached
                    if not _basis_favorable and _waited_sec >= _wait_max_sec:
                        _reason = f'spread_low_basis_timeout_{int(_waited_sec / 60)}min'
                        _entry_basis = trade.entry_basis_pct if trade.entry_basis_pct is not None else Decimal("0")
                        logger.info(
                            f"⏱ Trade {trade.trade_id}: EXIT (forced — {int(_waited_sec / 60)}min wait, basis still adverse: "
                            f"exit={float(exit_basis):.4f}% < entry={float(_entry_basis):.4f}% "
                            f"[{trade.long_exchange}={_l_price}/{trade.short_exchange}={_s_price}]) "
                            f"| spread {float(immediate_spread):.4f}% (held {hold_min}min)",
                            extra={"trade_id": trade.trade_id, "symbol": trade.symbol, "action": "basis_wait_timeout_exit"},
                        )
                    else:
                        _reason = f'spread_low_{float(immediate_spread):.4f}pct_basis_ok'
                        _entry_basis = trade.entry_basis_pct if trade.entry_basis_pct is not None else Decimal("0")
                        logger.info(
                            f"🔄 Trade {trade.trade_id}: EXIT — spread {float(immediate_spread):.4f}% "
                            f"< {float(hold_min_spread):.2f}% threshold, basis at/better than entry "
                            f"(exit={float(exit_basis):.4f}% ≥ entry={float(_entry_basis):.4f}% "
                            f"[{trade.long_exchange}={_l_price}/{trade.short_exchange}={_s_price}]) "
                            f"(held {hold_min}min)",
                            extra={"trade_id": trade.trade_id, "symbol": trade.symbol, "action": "quick_cycle_exit"},
                        )
                    trade._exit_reason = _reason
                    trade._exit_wait_start = None
                    self._journal.exit_decision(
                        trade.trade_id, trade.symbol,
                        reason=_reason,
                        immediate_spread=immediate_spread, hold_min=hold_min,
                    )
                    await self._close_trade(trade)
                else:
                    # Basis adverse — start or continue waiting
                    if _wait_start is None:
                        trade._exit_wait_start = now
                        _entry_basis = trade.entry_basis_pct if trade.entry_basis_pct is not None else Decimal("0")
                        logger.info(
                            f"⏳ Trade {trade.trade_id}: WAITING FOR ENTRY-LEVEL BASIS (max 30min) — "
                            f"spread {float(immediate_spread):.4f}% below threshold but "
                            f"exit basis {float(exit_basis):.4f}% < entry basis {float(_entry_basis):.4f}% "
                            f"(short moved against us: {float(_adverse_exit_basis):.4f}%)",
                            extra={"trade_id": trade.trade_id, "symbol": trade.symbol, "action": "basis_wait_start"},
                        )
                    else:
                        logger.debug(
                            f"⏳ Trade {trade.trade_id}: still waiting for favorable basis "
                            f"({int(_waited_sec / 60)}min / 30min) — "
                            f"adverse {float(_adverse_exit_basis):.4f}%"
                        )
                    return  # check again next cycle
                return

        long_spec = await long_adapter.get_instrument_spec(trade.symbol)
        short_spec = await short_adapter.get_instrument_spec(trade.symbol)
        if not long_spec or not short_spec:
            return

        fees_pct = calculate_fees(long_spec.taker_fee, short_spec.taker_fee)
        # Use immediate (next-payment) spread — no 8h normalization
        net = immediate_spread - fees_pct
        hold_min_spread = self._cfg.trading_params.hold_min_spread

        if net <= 0 or net < hold_min_spread:
            logger.info(
                f"Exit signal for {trade.trade_id}: net={net:.4f}% "
                f"< hold_min_spread {float(hold_min_spread):.2f}% — closing",
                extra={"trade_id": trade.trade_id, "symbol": trade.symbol, "action": "exit_signal"},
            )
            await self._close_trade(trade)
        else:
            # Advance trackers to next payment
            long_next = long_funding.get("next_timestamp")
            short_next = short_funding.get("next_timestamp")
            if long_next:
                trade.next_funding_long = datetime.fromtimestamp(long_next / 1000, tz=timezone.utc)
            if short_next:
                trade.next_funding_short = datetime.fromtimestamp(short_next / 1000, tz=timezone.utc)
            # Refresh stored rates to the new-cycle cache values so the NEXT payment
            # estimation uses the currently-visible rate (not the stale entry rate).
            _new_lr = long_funding.get("rate")
            _new_sr = short_funding.get("rate")
            if _new_lr is not None:
                trade.long_funding_rate = Decimal(str(_new_lr))
            if _new_sr is not None:
                trade.short_funding_rate = Decimal(str(_new_sr))
            # How long have we been holding?
            hold_min = 0
            if trade.opened_at:
                hold_min = int((now - trade.opened_at).total_seconds() / 60)
            logger.info(
                f"Trade {trade.trade_id}: ✅ HOLDING — still profitable! "
                f"net={net:.4f}% (entry was {trade.entry_edge_pct:.4f}%) | "
                f"holding for {hold_min}min | "
                f"Next payment: {trade.long_exchange}={trade.next_funding_long.strftime('%H:%M') if trade.next_funding_long else '?'}, "
                f"{trade.short_exchange}={trade.next_funding_short.strftime('%H:%M') if trade.next_funding_short else '?'}"
            )

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

