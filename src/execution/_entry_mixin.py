"""
Execution controller mixin — entry gate logic and trade registration.
Do NOT import this module directly; use ExecutionController from controller.py.
"""
from __future__ import annotations

import asyncio
import logging
import time as _time
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from src.core.contracts import (
    OpportunityCandidate,
    OrderRequest,
    OrderSide,
    TradeMode,
    TradeRecord,
    TradeState,
)
from src.core.logging import get_logger
from src.execution._entry_orders_mixin import _EntryOrdersMixin
from src.execution import helpers as _h

if TYPE_CHECKING:
    pass  # all attribute access via self (mixin pattern)

logger = get_logger("execution")


class _EntryMixin(_EntryOrdersMixin):
    async def handle_opportunity(self, opp: OpportunityCandidate) -> None:
        """Validate and execute a new funding-arb trade."""
        logger.info(
            f"🔍 [{opp.symbol}] Evaluating opportunity: mode={opp.mode} "
            f"spread={opp.immediate_spread_pct:.4f}% net={opp.net_edge_pct:.4f}% "
            f"L={opp.long_exchange} S={opp.short_exchange}"
        )

        if self._blacklist.is_blacklisted(opp.symbol, opp.long_exchange, opp.short_exchange):
            return

        if await self._redis.is_cooled_down(opp.symbol):
            logger.info(f"❄️ Skipping {opp.symbol}: symbol is in cooldown")
            return

        # P1-2: Per-route cooldown — a failure on exchange_a→exchange_b must not
        # suppress exchange_c→exchange_d for the same symbol.  The route key is
        # symbol|long_exchange|short_exchange, independent of the symbol cooldown.
        if await self._redis.is_route_cooled_down(opp.symbol, opp.long_exchange, opp.short_exchange):
            logger.info(
                f"❄️ Skipping {opp.symbol}: route "
                f"{opp.long_exchange}→{opp.short_exchange} is in cooldown"
            )
            return

        upgrade_expiry = self._upgrade_cooldown.get(opp.symbol)
        if upgrade_expiry is not None:
            if _time.time() < upgrade_expiry:
                remaining = int(upgrade_expiry - _time.time())
                logger.info(
                    f"⬆️ Skipping {opp.symbol}: upgrade cooldown active ({remaining}s left)"
                )
                return
            else:
                del self._upgrade_cooldown[opp.symbol]

        # ── TOCTOU guard: claim the symbol slot BEFORE any await ──────────────
        if opp.symbol in self._symbols_entering:
            logger.info(f"🔒 Skipping {opp.symbol}: entry already in progress")
            return
        self._symbols_entering.add(opp.symbol)
        try:
            return await self._handle_opportunity_inner(opp)
        finally:
            self._symbols_entering.discard(opp.symbol)

    async def _handle_opportunity_inner(self, opp: OpportunityCandidate) -> None:
        """Inner implementation — called only after the TOCTOU guard is held."""
        _t0_mono = _time.monotonic()
        _reserved_exchanges: set[str] = set()

        # Duplicate guard — O(1) via maintained set
        if opp.symbol in self._active_symbols:
            logger.info(f"🔁 Skipping {opp.symbol}: already have active trade")
            return

        # Concurrency cap
        if len(self._active_trades) >= self._cfg.execution.concurrent_opportunities:
            logger.info(
                f"🚫 Skipping {opp.symbol}: concurrency cap reached "
                f"({len(self._active_trades)}/{self._cfg.execution.concurrent_opportunities})"
            )
            return

        # Exchange-in-use guard — block both OPEN trades and entries mid-flight.
        for ex in {opp.long_exchange, opp.short_exchange}:
            if ex in self._busy_exchanges or ex in self._exchanges_entering:
                logger.info(
                    f"🔒 Skipping {opp.symbol}: {ex} already in use by another trade"
                )
                return
        _reserved_exchanges = {opp.long_exchange, opp.short_exchange}
        self._exchanges_entering.update(_reserved_exchanges)
        try:
            return await self._handle_opportunity_guarded(opp, _reserved_exchanges, _t0_mono)
        finally:
            # P0: ALWAYS release reserved exchanges — prevents permanent lock-out
            # when any gate (timing, spread, lock, rate-flip, etc.) rejects early.
            for ex in _reserved_exchanges:
                self._exchanges_entering.discard(ex)

    async def _handle_opportunity_guarded(
        self,
        opp: OpportunityCandidate,
        _reserved_exchanges: set[str],
        _t0_mono: float,
    ) -> None:
        """Continue entry after exchange reservation — caller guarantees cleanup."""
        # ── Funding spread gate (safety check) ──
        tp = self._cfg.trading_params
        if opp.mode == TradeMode.CHERRY_PICK:
            if opp.net_edge_pct < tp.min_funding_spread:
                logger.info(
                    f"📉 Skipping {opp.symbol}: cherry-pick net {opp.net_edge_pct:.4f}% "
                    f"< min_funding_spread {tp.min_funding_spread}% (gross={opp.gross_edge_pct:.4f}%)"
                )
                return
        else:
            if opp.net_edge_pct < tp.min_funding_spread:
                logger.info(
                    f"📉 Skipping {opp.symbol}: net {opp.net_edge_pct:.4f}% "
                    f"< min_funding_spread {tp.min_funding_spread}% (gross={opp.gross_edge_pct:.4f}%)"
                )
                return

        # ── Hard price-spread gate (defense-in-depth) ──
        # The scanner already classifies tiers: TOP (spread ≤ 0), MEDIUM (spread ≤ cost),
        # WEAK (funding excess covers spread).  Trust the tier classification here
        # and only reject when the spread is truly uncovered (no tier or adverse).
        # Live VWAP check (_check_pre_entry_liquidity) and post-entry basis check
        # (max_entry_basis_spread_pct=0.15%) provide additional defense.
        if opp.entry_tier in (None, "adverse") and opp.price_spread_pct > Decimal("0"):
            logger.info(
                f"🚫 Skipping {opp.symbol}: adverse price spread "
                f"{float(opp.price_spread_pct):+.4f}% (no qualifying tier)"
            )
            return

        long_adapter = self._exchanges.get(opp.long_exchange)
        short_adapter = self._exchanges.get(opp.short_exchange)

        # ── Tier-based Entry timing gate ─────────────────────────────
        entry_offset = self._cfg.trading_params.entry_offset_seconds
        now_ms = _time.time() * 1000

        long_rate = opp.long_funding_rate
        short_rate = opp.short_funding_rate
        long_contribution = abs(long_rate) if long_rate < 0 else Decimal("0")
        short_contribution = abs(short_rate) if short_rate > 0 else Decimal("0")

        if long_contribution > short_contribution:
            primary_side = "long"
        else:
            primary_side = "short"

        primary_next_ms = opp.next_funding_ms

        tier = opp.entry_tier
        tier_emoji = {"top": "🏆", "medium": "📊", "weak": "⚡"}.get(tier or "", "")
        # Human-readable label used in all timing-gate log messages.
        # e.g. "🏆 TOP tier" or "no tier"
        tier_label = f"{tier_emoji} {tier.upper()} tier" if tier else "no tier"

        _MIN_ENTRY_SECS_BEFORE_FUNDING = tp.min_entry_secs_before_funding

        # ── Entry timing gate (identical logic for all tiers) ──────────────
        # Both tiered and un-tiered opportunities must pass the same window
        # check.  The only difference is the label used in log messages.
        if primary_next_ms is None:
            logger.info(
                f"⏳ Skipping {opp.symbol}: {tier_label} — no funding timestamp available"
            )
            return
        # Defense-in-depth: re-read live funding timestamp
        primary_next_ms = self._resolve_live_next_ms(
            opp, primary_side, long_adapter, short_adapter, primary_next_ms
        )
        seconds_until = (primary_next_ms - now_ms) / 1000
        if not (_MIN_ENTRY_SECS_BEFORE_FUNDING < seconds_until <= entry_offset):
            if seconds_until <= _MIN_ENTRY_SECS_BEFORE_FUNDING and seconds_until > 0:
                logger.info(
                    f"⏳ Skipping {opp.symbol}: {tier_label} — "
                    f"funding too close ({int(seconds_until)}s < {_MIN_ENTRY_SECS_BEFORE_FUNDING}s minimum)"
                )
            else:
                logger.info(
                    f"⏳ Skipping {opp.symbol}: {tier_label} — "
                    f"not in {entry_offset}s window. Next funding in {int(seconds_until/60)}min."
                )
            return
        if tier:
            logger.info(
                f"{tier_emoji} [{opp.symbol}] {tier.upper()} tier — "
                f"entering in {entry_offset}s window "
                f"(price_spread={float(opp.price_spread_pct):+.4f}%, "
                f"funding in {int(seconds_until/60)}min)"
            )

        logger.info(f"✅ [{opp.symbol}] Passed all gates — proceeding to entry")

        # ── P0-1: Acquire distributed lock BEFORE rate re-verification ────────
        # Moved from just-before-orders to here so the lock TTL covers the FULL
        # entry path: rate fetch + sizing + settings + pre-flight + orders + fills.
        # Token ownership prevents a stale `release_lock` (after our TTL expired)
        # from clobbering a lock that a second instance legitimately acquired.
        # A heartbeat task renews the TTL every 15 s to survive slow entries.
        lock_key = f"trade:{opp.symbol}"
        lock_token = str(uuid.uuid4())
        if not await self._redis.acquire_lock_with_token(lock_key, lock_token, ttl=60):
            logger.info(
                f"🔒 [{opp.symbol}] Distributed lock already held by another instance — skipping"
            )
            return

        _lock_heartbeat_task = asyncio.create_task(
            self._lock_heartbeat(lock_key, lock_token),
            name=f"lock_hb:{opp.symbol}",
        )
        _lock_heartbeat_task.add_done_callback(
            lambda t: logger.warning(
                f"[{opp.symbol}] Lock heartbeat ended unexpectedly: {t.exception()}"
            ) if not t.cancelled() and t.exception() else None
        )

        # ── Rate direction re-verification ─────────────────────────────────
        # P1-2: If the cached rate is >30s old, do a live REST fetch before
        # checking direction. The old code read from a cache with a 1h TTL,
        # giving a false appearance of safety: a rate that flipped 59 minutes
        # ago would silently pass the direction check.
        _RATE_FRESHNESS_SEC = 30.0
        _now_verify = _time.time()
        _verify_long = long_adapter.get_funding_rate_cached(opp.symbol) if long_adapter else None
        _verify_short = short_adapter.get_funding_rate_cached(opp.symbol) if short_adapter else None

        _long_stale = (
            _verify_long is None or
            (_now_verify * 1000 - float(_verify_long.get("cached_at_ms") or 0)) / 1000 > _RATE_FRESHNESS_SEC
        )
        _short_stale = (
            _verify_short is None or
            (_now_verify * 1000 - float(_verify_short.get("cached_at_ms") or 0)) / 1000 > _RATE_FRESHNESS_SEC
        )
        if _long_stale or _short_stale:
            _refresh_tasks = []
            if _long_stale and long_adapter:
                _refresh_tasks.append(long_adapter.get_funding_rate(opp.symbol))
            if _short_stale and short_adapter:
                _refresh_tasks.append(short_adapter.get_funding_rate(opp.symbol))
            try:
                await asyncio.gather(*_refresh_tasks, return_exceptions=True)
            except Exception as _re:
                logger.debug(f"[{opp.symbol}] Rate refresh before verify failed: {_re}")
            _verify_long = long_adapter.get_funding_rate_cached(opp.symbol) if long_adapter else None
            _verify_short = short_adapter.get_funding_rate_cached(opp.symbol) if short_adapter else None
            if _long_stale or _short_stale:
                logger.debug(f"[{opp.symbol}] Rate re-verification: fetched live rates (cache was stale)")

        if _verify_long and _verify_short:
            _vl_rate = Decimal(str(_verify_long["rate"]))
            _vs_rate = Decimal(str(_verify_short["rate"]))
            _orig_long_income = opp.long_funding_rate < 0
            _orig_short_income = opp.short_funding_rate > 0
            _now_long_income = _vl_rate < 0
            _now_short_income = _vs_rate > 0
            if _orig_long_income != _now_long_income or _orig_short_income != _now_short_income:
                logger.warning(
                    f"🚫 [{opp.symbol}] Rate direction FLIPPED since scan — aborting entry! "
                    f"Scan: L={float(opp.long_funding_rate)*100:+.4f}% S={float(opp.short_funding_rate)*100:+.4f}% "
                    f"→ Now: L={float(_vl_rate)*100:+.4f}% S={float(_vs_rate)*100:+.4f}%",
                    extra={"symbol": opp.symbol, "action": "rate_flip_abort"},
                )
                # P0-1: must clean up lock + heartbeat before early return
                # (these returns are outside the inner try/finally block)
                _lock_heartbeat_task.cancel()
                await self._redis.release_lock_if_owner(lock_key, lock_token)
                return
            # P1-2: Magnitude check — direction unchanged but rate may have collapsed.
            # Re-compute live net spread; abort if it no longer clears min_funding_spread.
            # (A rate falling from 0.40% to 0.07% is not a flip, but the trade is unprofitable.)
            _live_income = (
                (abs(_vl_rate) if _vl_rate < 0 else Decimal("0"))
                + (abs(_vs_rate) if _vs_rate > 0 else Decimal("0"))
            )
            _live_net = _live_income * Decimal("100") - tp.slippage_buffer_pct - tp.safety_buffer_pct
            if _live_net < tp.min_funding_spread:
                logger.warning(
                    f"\U0001f6ab [{opp.symbol}] Rate COLLAPSED since scan \u2014 aborting entry! "
                    f"Live net {float(_live_net):.4f}% < min {float(tp.min_funding_spread):.4f}% "
                    f"(scan: L={float(opp.long_funding_rate)*100:+.4f}% S={float(opp.short_funding_rate)*100:+.4f}% "
                    f"\u2192 now: L={float(_vl_rate)*100:+.4f}% S={float(_vs_rate)*100:+.4f}%)",
                    extra={"symbol": opp.symbol, "action": "rate_collapse_abort"},
                )
                # P0-1: same early-return cleanup
                _lock_heartbeat_task.cancel()
                await self._redis.release_lock_if_owner(lock_key, lock_token)
                return
        # (lock is held from above — no second acquire needed)

        trade_id = str(uuid.uuid4())[:12]
        if self._risk_guard:
            self._risk_guard.mark_entry_started(opp.symbol)
        try:
            # ── Execute entry orders (sizing → placement → fills → delta correction) ──
            result = await self._execute_entry_orders(opp, long_adapter, short_adapter)
            if result is None:
                return

            long_filled_qty = result["long_filled_qty"]
            short_filled_qty = result["short_filled_qty"]
            entry_price_long = result["entry_price_long"]
            entry_price_short = result["entry_price_short"]
            entry_fees = result["entry_fees"]
            long_spec = result["long_spec"]
            short_spec = result["short_spec"]
            entry_basis_pct = result["entry_basis_pct"]

            # ── Post-entry validation: reject if spread became adverse during execution ──
            # entry_basis_pct = (entry_price_long - entry_price_short) / entry_price_short * 100
            # Positive spread = adverse (long more expensive than short)
            tp = self._cfg.trading_params
            # P0-1: max_entry_basis_spread_pct is now a declared TradingParams field (Decimal "0.15").
            # The prior hasattr() fallback returned Decimal("0"), causing false rejections on every
            # normal market-order execution where ask_long > bid_short by the bid-ask spread.
            entry_basis_threshold = tp.max_entry_basis_spread_pct
            if entry_basis_pct > entry_basis_threshold:
                logger.warning(
                    f"🚫 [{opp.symbol}] POST-ENTRY REJECTION: actual spread {float(entry_basis_pct):+.4f}% "
                    f"> threshold {float(entry_basis_threshold):+.4f}% (scanner saw {float(opp.price_spread_pct):+.4f}%) — "
                    f"latency fillslipped the trade — CLOSING IMMEDIATELY"
                )
                # Emergency close: place reduce-only orders to unwind both positions
                try:
                    close_tasks = []
                    # Close long: SELL with reduce_only
                    if long_filled_qty > 0:
                        close_tasks.append(
                            self._place_with_timeout(
                                long_adapter,
                                OrderRequest(
                                    exchange=opp.long_exchange,
                                    symbol=opp.symbol,
                                    side=OrderSide.SELL,
                                    quantity=long_filled_qty,
                                    reduce_only=True,
                                ),
                            )
                        )
                    # Close short: BUY with reduce_only
                    if short_filled_qty > 0:
                        close_tasks.append(
                            self._place_with_timeout(
                                short_adapter,
                                OrderRequest(
                                    exchange=opp.short_exchange,
                                    symbol=opp.symbol,
                                    side=OrderSide.BUY,
                                    quantity=short_filled_qty,
                                    reduce_only=True,
                                ),
                            )
                        )
                    results = await asyncio.gather(*close_tasks, return_exceptions=True)
                    failed = [r for r in results if isinstance(r, Exception) or r is None]
                    if failed:
                        logger.error(
                            f"❌ [{opp.symbol}] Emergency close FAILED ({len(failed)} legs) — "
                            f"MANUAL INTERVENTION REQUIRED — positions may be unhedged"
                        )
                    else:
                        # P1-5: Order accepted ≠ order filled. Verify actual position
                        # is flat before declaring success (partial fills leave phantom
                        # positions that bypass the 60s risk-guard grace window).
                        await asyncio.sleep(0.5)  # brief settle for exchange to process
                        _residual_legs: list[str] = []
                        try:
                            _pos_checks = []
                            _pos_labels: list[str] = []
                            if long_filled_qty > 0 and long_adapter:
                                _pos_checks.append(long_adapter.get_positions(opp.symbol))
                                _pos_labels.append(opp.long_exchange)
                            if short_filled_qty > 0 and short_adapter:
                                _pos_checks.append(short_adapter.get_positions(opp.symbol))
                                _pos_labels.append(opp.short_exchange)
                            _pos_results = await asyncio.gather(*_pos_checks, return_exceptions=True)
                            for _label, _pres in zip(_pos_labels, _pos_results):
                                if isinstance(_pres, Exception):
                                    logger.warning(f"[{opp.symbol}] Post-close position check failed on {_label}: {_pres}")
                                    continue
                                for _p in (_pres or []):
                                    if getattr(_p, "symbol", None) == opp.symbol and getattr(_p, "quantity", 0) > 0:
                                        _residual_legs.append(f"{_label}:{_p.quantity}")
                        except Exception as _ve:
                            logger.warning(f"[{opp.symbol}] Emergency close fill verification error: {_ve}")

                        if _residual_legs:
                            logger.error(
                                f"❌ [{opp.symbol}] Emergency close INCOMPLETE — "
                                f"residual positions: {', '.join(_residual_legs)} — "
                                f"MANUAL INTERVENTION REQUIRED"
                            )
                        else:
                            logger.info(
                                f"✅ [{opp.symbol}] Emergency close CONFIRMED filled — "
                                f"adverse trade rejected and positions verified flat"
                            )
                except Exception as e:
                    logger.error(
                        f"❌ [{opp.symbol}] Emergency close ERROR: {e} — "
                        f"MANUAL INTERVENTION REQUIRED"
                    )

                # ── Journal the ghost trade so it appears in history ──────
                _ghost_fees = entry_fees * Decimal("2")  # entry + exit fees
                _ghost_notional = float(entry_price_long * long_filled_qty) if entry_price_long and long_filled_qty else 0.0
                self._journal.trade_closed(
                    trade_id=f"ghost-{trade_id}",
                    symbol=opp.symbol,
                    mode=opp.mode or "unknown",
                    duration_min=0.0,
                    entry_price_long=entry_price_long,
                    entry_price_short=entry_price_short,
                    exit_price_long=entry_price_long,
                    exit_price_short=entry_price_short,
                    fees=float(_ghost_fees),
                    net_profit=-float(_ghost_fees),
                    invested=_ghost_notional,
                    exit_reason="post_entry_rejection",
                    long_exchange=opp.long_exchange,
                    short_exchange=opp.short_exchange,
                )
                if self._publisher:
                    self._publisher.record_trade(is_win=False, pnl=-_ghost_fees)

                # ── Cooldown: prevent immediate re-entry into same symbol ─
                _rejection_cooldown = self._cfg.trading_params.cooldown_after_close_seconds
                await self._redis.set_cooldown(opp.symbol, _rejection_cooldown)
                # P2: Also set per-route cooldown — post-entry rejection is
                # route-specific (adverse fill on this long→short exchange pair).
                # A different route for same symbol should not be blocked.
                await self._redis.set_route_cooldown(
                    opp.symbol, opp.long_exchange, opp.short_exchange,
                    _rejection_cooldown, reason="post_entry_rejection",
                )
                logger.info(
                    f"❄️ [{opp.symbol}] Cooldown {_rejection_cooldown}s after post-entry rejection"
                )
                return

            trade = TradeRecord(
                trade_id=trade_id,
                symbol=opp.symbol,
                state=TradeState.OPEN,
                long_exchange=opp.long_exchange,
                short_exchange=opp.short_exchange,
                long_qty=long_filled_qty,
                short_qty=short_filled_qty,
                entry_edge_pct=opp.net_edge_pct,
                long_funding_rate=opp.long_funding_rate,
                short_funding_rate=opp.short_funding_rate,
                entry_price_long=entry_price_long,
                entry_price_short=entry_price_short,
                entry_basis_pct=entry_basis_pct,
                fees_paid_total=entry_fees,
                long_taker_fee=long_spec.taker_fee,
                short_taker_fee=short_spec.taker_fee,
                opened_at=datetime.now(timezone.utc),
                mode=opp.mode,
                exit_before=opp.exit_before,
                entry_tier=opp.entry_tier,
                price_spread_pct=opp.price_spread_pct,
            )
            # P0-3: Persist BEFORE registering in memory.
            # If persist fails after real fills, positions are open on the exchange
            # but nothing is in _active_trades — a crash/restart loses them forever.
            # Unwind immediately if Redis rejects the write.
            try:
                await self._persist_trade(trade)
            except Exception as _persist_exc:
                logger.critical(
                    f"❌ [{opp.symbol}] CRITICAL: persist_trade failed after fills — "
                    f"positions OPEN on exchange but NOT saved; emergency unwinding.",
                    extra={"trade_id": trade_id, "action": "persist_failed"},
                    exc_info=_persist_exc,
                )
                _ew: list = []
                if long_filled_qty > 0:
                    _ew.append(self._close_orphan(
                        long_adapter, opp.long_exchange, opp.symbol,
                        OrderSide.SELL, {"filled": float(long_filled_qty)},
                    ))
                if short_filled_qty > 0:
                    _ew.append(self._close_orphan(
                        short_adapter, opp.short_exchange, opp.symbol,
                        OrderSide.BUY, {"filled": float(short_filled_qty)},
                    ))
                if _ew:
                    await asyncio.gather(*_ew, return_exceptions=True)
                # 1h symbol cooldown + route cooldown after persist failure with live fills
                await self._redis.set_cooldown(opp.symbol, 3600)
                await self._redis.set_route_cooldown(
                    opp.symbol, opp.long_exchange, opp.short_exchange,
                    3600, reason="persist_failed",
                )
                return
            self._register_trade(trade)

            mode_str = f" mode={opp.mode}"
            tier_str = f" tier={opp.entry_tier.upper()}" if opp.entry_tier else ""
            price_spread_str = f" price_spread={float(opp.price_spread_pct):+.4f}%" if opp.price_spread_pct else ""
            if opp.exit_before:
                mode_str += f" exit_before={opp.exit_before.strftime('%H:%M UTC')}"
            if opp.n_collections > 0:
                mode_str += f" collections={opp.n_collections}"

            logger.info(
                f"Trade opened: {trade_id} {opp.symbol} "
                f"L={opp.long_exchange}({long_filled_qty}) "
                f"S={opp.short_exchange}({short_filled_qty}) "
                f"spread={opp.immediate_spread_pct:.4f}% net={opp.net_edge_pct:.4f}%{mode_str}{tier_str}{price_spread_str}",
                extra={
                    "trade_id": trade_id,
                    "symbol": opp.symbol,
                    "action": "trade_opened",
                },
            )

            immediate_spread = ((-opp.long_funding_rate) + opp.short_funding_rate) * Decimal("100")

            lr_pct = float(opp.long_funding_rate) * 100
            sr_pct = float(opp.short_funding_rate) * 100
            income_parts = []
            cost_parts = []
            if opp.long_funding_rate < 0:
                income_parts.append(f"{opp.long_exchange}(long) receives {abs(lr_pct):.4f}%")
            else:
                cost_parts.append(f"{opp.long_exchange}(long) pays {lr_pct:.4f}%")
            if opp.short_funding_rate > 0:
                income_parts.append(f"{opp.short_exchange}(short) receives {sr_pct:.4f}%")
            else:
                cost_parts.append(f"{opp.short_exchange}(short) pays {abs(sr_pct):.4f}%")
            income_str = ", ".join(income_parts) if income_parts else "none"
            cost_str = ", ".join(cost_parts) if cost_parts else "none"

            entry_reason = (
                f"{opp.mode.upper()}: spread={float(immediate_spread):.4f}% net={float(opp.net_edge_pct):.4f}% | "
                f"Income: {income_str} | Cost: {cost_str}"
            )
            if opp.mode == TradeMode.CHERRY_PICK:
                entry_reason += f" | collections={opp.n_collections}"
                if opp.exit_before:
                    entry_reason += f" exit_before={opp.exit_before.strftime('%H:%M UTC')}"

            entry_notional = float(entry_price_long * long_filled_qty) if entry_price_long else 0
            _exec_latency_ms = int((_time.monotonic() - _t0_mono) * 1000)

            entry_msg = (
                f"\n{'='*60}\n"
                f"  🟢 TRADE ENTRY — {trade_id}\n"
                f"  Symbol:    {opp.symbol}\n"
                f"  Mode:      {opp.mode}\n"
                f"  Tier:      {(opp.entry_tier or 'N/A').upper()} {tier_emoji}\n"
                f"  Reason:    {entry_reason}\n"
                f"  LONG:      {opp.long_exchange} qty={long_filled_qty} @ ${float(entry_price_long or 0):.6f} "
                    f"| funding={lr_pct:+.4f}%\n"
                f"  SHORT:     {opp.short_exchange} qty={short_filled_qty} @ ${float(entry_price_short or 0):.6f} "
                    f"| funding={sr_pct:+.4f}%\n"
                f"  Price Spread: {float(entry_basis_pct):+.4f}% "
                    f"({'favorable ✅' if entry_basis_pct < 0 else 'adverse ⚠️' if entry_basis_pct > 0 else 'neutral'})"
                    f" (scanner: {float(opp.price_spread_pct):+.4f}%)\n"
                f"  Notional:  ${entry_notional:.2f} per leg\n"
                f"  Spread:    {float(immediate_spread):.4f}% (immediate)\n"
                f"  Net edge:  {float(opp.net_edge_pct):.4f}% (after fees)\n"
                f"  Fees:      ${float(entry_fees):.4f}\n"
                f"  Latency:   {_exec_latency_ms}ms (discovery → filled)\n"
                f"{'='*60}"
            )
            logger.info(entry_msg, extra={"trade_id": trade_id, "symbol": opp.symbol, "action": "trade_entry"})
            if self._publisher:
                await self._publisher.publish_log("INFO", entry_msg)
                await self._publisher.publish_alert(
                    (
                        f"🟢 Trade opened: {trade_id} {opp.symbol} "
                        f"L={opp.long_exchange} S={opp.short_exchange} "
                        f"net={float(opp.net_edge_pct):.4f}%"
                    ),
                    severity="info",
                    alert_type="trade_open",
                    symbol=opp.symbol,
                )

            self._journal.trade_opened(
                trade_id=trade_id, symbol=opp.symbol, mode=opp.mode,
                long_exchange=opp.long_exchange, short_exchange=opp.short_exchange,
                long_qty=long_filled_qty, short_qty=short_filled_qty,
                entry_price_long=entry_price_long, entry_price_short=entry_price_short,
                long_funding_rate=opp.long_funding_rate, short_funding_rate=opp.short_funding_rate,
                spread_pct=entry_basis_pct, net_pct=opp.net_edge_pct,
                exit_before=opp.exit_before, n_collections=opp.n_collections,
                notional=entry_notional,
                entry_reason=entry_reason,
                exec_latency_ms=_exec_latency_ms,
            )

            if self._cfg.logging.log_balances_after_trade:
                await self._log_exchange_balances()
        except Exception as e:
            err_str = str(e).lower()
            if any(kw in err_str for kw in [
                "delisting", "delist", "30228",
                "symbol is not available",
                "contract is being settled",
                "reduce-only", "reduce only",
            ]):
                self._blacklist.add(opp.symbol, opp.long_exchange)
                self._blacklist.add(opp.symbol, opp.short_exchange)
            logger.error(f"Trade execution failed for {opp.symbol}: {e}",
                         extra={"symbol": opp.symbol})
        finally:
            if self._risk_guard:
                self._risk_guard.clear_entry_started(opp.symbol)
            _lock_heartbeat_task.cancel()
            await self._redis.release_lock_if_owner(lock_key, lock_token)

    # ── Pre-entry order book depth + VWAP check ──────────────────────────────
    async def _check_pre_entry_liquidity(
        self,
        opp: OpportunityCandidate,
        long_adapter,
        short_adapter,
        qty: Optional[Decimal] = None,
    ) -> bool:
        """Fetch live order book for both legs and verify there is enough depth.

        Returns True (safe to proceed) or False (entry should be skipped).

        Steps:
          1. Walk asks on the long exchange (we BUY there).
          2. Walk bids on the short exchange (we SELL there).
          3. If either book cannot fill our full qty → skip.
          4. If the resulting VWAP spread is adverse (positive %) → skip.

        P1-1: ``qty`` is the sizer's ``order_qty`` rather than
        ``opp.suggested_qty``.  The scanner snapshot can be 30–60 s stale and
        sized from a different balance baseline; checking depth against the
        wrong quantity either passes a too-shallow book or rejects a perfectly
        good entry.
        """
        # P1-1: Prefer the live order_qty from the sizer if provided.
        check_qty = qty if (qty is not None and qty > Decimal("0")) else opp.suggested_qty
        if check_qty <= Decimal("0"):
            # No qty estimate — allow entry; sizer will handle it
            return True

        symbol = opp.symbol
        try:
            long_vwap, long_ok = await long_adapter.get_vwap_and_depth(symbol, check_qty, "buy")
            short_vwap, short_ok = await short_adapter.get_vwap_and_depth(symbol, check_qty, "sell")
        except Exception as exc:
            logger.warning(
                f"[{symbol}] Pre-entry liquidity check error: {exc} — blocking entry",
                extra={"symbol": symbol, "action": "depth_check_error"},
            )
            return False

        if not long_ok:
            logger.info(
                f"📊 [{symbol}] PRE-ENTRY BLOCK: {opp.long_exchange} book too shallow "
                f"to fill {float(check_qty):.4g} units (BUY) — skipping",
                extra={"symbol": symbol, "action": "depth_insufficient"},
            )
            return False

        if not short_ok:
            logger.info(
                f"📊 [{symbol}] PRE-ENTRY BLOCK: {opp.short_exchange} book too shallow "
                f"to fill {float(check_qty):.4g} units (SELL) — skipping",
                extra={"symbol": symbol, "action": "depth_insufficient"},
            )
            return False

        if long_vwap <= Decimal("0") or short_vwap <= Decimal("0"):
            logger.warning(
                f"[{symbol}] Pre-entry depth check returned zero price — blocking entry"
            )
            return False

        vwap_spread_pct = (long_vwap - short_vwap) / short_vwap * Decimal("100")
        if vwap_spread_pct > Decimal("0"):
            logger.info(
                f"📊 [{symbol}] PRE-ENTRY BLOCK: VWAP spread adverse "
                f"({float(vwap_spread_pct):+.4f}%) — "
                f"{opp.long_exchange} ask={float(long_vwap):.6f} > "
                f"{opp.short_exchange} bid={float(short_vwap):.6f} — skipping",
                extra={"symbol": symbol, "action": "vwap_adverse"},
            )
            # P2: VWAP adverse is route-local (this exchange pair has a bad spread
            # right now; a different route for the same symbol should not be blocked).
            # Use route cooldown only — 30 s is too short to justify a global symbol block.
            await self._redis.set_route_cooldown(
                symbol, opp.long_exchange, opp.short_exchange, 30, reason="vwap_adverse",
            )
            return False

        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                f"[{symbol}] Pre-entry depth OK: "
                f"{opp.long_exchange} ask_vwap={float(long_vwap):.6f} | "
                f"{opp.short_exchange} bid_vwap={float(short_vwap):.6f} | "
                f"spread={float(vwap_spread_pct):+.4f}%"
            )
        return True

    # ── Private helpers ──────────────────────────────────────────

    def _resolve_live_next_ms(
        self,
        opp: OpportunityCandidate,
        primary_side: str,
        long_adapter,
        short_adapter,
        primary_next_ms: float,
    ) -> float:
        """Re-read live funding timestamp from adapter cache.

        Provides defense-in-depth: the scanner may have cached a timestamp
        that moved between scan and entry. Returns the updated value (or the
        original if nothing changed).
        """
        _live_funding = long_adapter.get_funding_rate_cached(opp.symbol) if long_adapter else None
        _live_short_funding = short_adapter.get_funding_rate_cached(opp.symbol) if short_adapter else None
        _live_next_ms: float | None = None
        if primary_side == "long" and _live_funding and _live_funding.get("next_timestamp"):
            _live_next_ms = _live_funding["next_timestamp"]
        elif primary_side == "short" and _live_short_funding and _live_short_funding.get("next_timestamp"):
            _live_next_ms = _live_short_funding["next_timestamp"]
        if _live_next_ms is not None and _live_next_ms != primary_next_ms:
            logger.info(
                f"🔄 [{opp.symbol}] Funding timestamp changed since scan: "
                f"opp={primary_next_ms} → live={_live_next_ms}. Using live value."
            )
            primary_next_ms = _live_next_ms
        return primary_next_ms

    # ── Lock heartbeat ────────────────────────────────────────────

    async def _lock_heartbeat(
        self,
        lock_key: str,
        token: str,
        interval: int = 15,
        ttl: int = 60,
    ) -> None:
        """Renew the distributed entry lock every ``interval`` seconds.

        P0-1: Without renewal the 60 s TTL may expire during a slow entry
        (large altcoin sizing + exchange settings + partial fills + delta trim
        can exceed 60 s under load).  If renewal fails, the lock was already
        lost; we log and return — the entry continues in a degraded state
        (race protection is gone) rather than blocking indefinitely.
        """
        try:
            while True:
                await asyncio.sleep(interval)
                renewed = await self._redis.extend_lock(lock_key, token, ttl=ttl)
                if not renewed:
                    logger.warning(
                        f"[{lock_key}] Lock heartbeat: renewal FAILED — lock expired or "
                        f"stolen by another instance. Entry proceeding without exclusive lock.",
                        extra={"lock_key": lock_key, "action": "lock_renewal_failed"},
                    )
                    return
                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug(f"[{lock_key}] Lock heartbeat: renewed (TTL={ttl}s)")
        except asyncio.CancelledError:
            pass  # normal shutdown — lock is about to be released by the caller

