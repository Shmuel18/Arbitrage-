"""
Execution controller mixin — entry gate logic and trade registration.
Do NOT import this module directly; use ExecutionController from controller.py.
"""
from __future__ import annotations

import asyncio
import time as _time
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from src.core.contracts import (
    OpportunityCandidate,
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

        # Exchange-in-use guard — O(1) via maintained set
        for ex in (opp.long_exchange, opp.short_exchange):
            if ex in self._busy_exchanges:
                logger.info(
                    f"🔒 Skipping {opp.symbol}: {ex} already in use by another trade"
                )
                return

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

        # ── Rate direction re-verification ─────────────────────────────────
        _verify_long = long_adapter.get_funding_rate_cached(opp.symbol) if long_adapter else None
        _verify_short = short_adapter.get_funding_rate_cached(opp.symbol) if short_adapter else None
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
                return

        # ── Acquire lock ─────────────────────────────────────────────────────
        lock_key = f"trade:{opp.symbol}"
        if not await self._redis.acquire_lock(lock_key):
            return

        trade_id = str(uuid.uuid4())[:12]
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
            self._register_trade(trade)
            await self._persist_trade(trade)

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
                f"  Price Spread: {float(opp.price_spread_pct):+.4f}% "
                    f"({'favorable ✅' if opp.price_spread_pct > 0 else 'adverse ⚠️' if opp.price_spread_pct < 0 else 'neutral'})\n"
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

            self._journal.trade_opened(
                trade_id=trade_id, symbol=opp.symbol, mode=opp.mode,
                long_exchange=opp.long_exchange, short_exchange=opp.short_exchange,
                long_qty=long_filled_qty, short_qty=short_filled_qty,
                entry_price_long=entry_price_long, entry_price_short=entry_price_short,
                long_funding_rate=opp.long_funding_rate, short_funding_rate=opp.short_funding_rate,
                spread_pct=opp.immediate_spread_pct, net_pct=opp.net_edge_pct,
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
            await self._redis.release_lock(lock_key)

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

    # ── Exit monitor ─────────────────────────────────────────────

