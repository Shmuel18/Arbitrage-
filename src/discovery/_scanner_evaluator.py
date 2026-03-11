"""Scanner evaluator mixin — pair evaluation and opportunity building.

Extracted from scanner.py to keep file sizes manageable.
Contains:
  _classify_tier()        — module-level tier classification helper
  _ScannerEvaluatorMixin  — _evaluate_pair, _evaluate_direction, _build_opportunity
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Dict, Optional

from src.core.contracts import EntryTier, OpportunityCandidate, OrderSide, TradeMode
from src.core.logging import get_logger
from src.discovery.calculator import (
    analyze_per_payment_pnl,
    calculate_cherry_pick_edge,
    calculate_fees,
    calculate_funding_spread,
)

if TYPE_CHECKING:
    from src.core.config import Config
    from src.exchanges.adapter import ExchangeAdapter

logger = get_logger("scanner")

_MIN_WINDOW_MINUTES = 30
_MIN_CHERRY_GAP_MINUTES = 30  # income and cost must fire at least this far apart


def _classify_tier(
    tier_net: Decimal,
    price_spread_pct: Decimal,
    total_cost_pct: Decimal,
    min_funding_spread: Decimal,
    weak_min_funding_excess: Decimal,
) -> Optional[str]:
    """Classify a funding opportunity into TOP / MEDIUM / WEAK tier.

    Returns the tier value string, or ``None`` if the opportunity
    doesn't qualify for any tier (adverse spread too large).

    Args:
        tier_net: Net funding after costs (%).
        price_spread_pct: Price spread between exchanges (%).
        total_cost_pct: Total fees/costs (%).
        min_funding_spread: Minimum net funding to qualify for any tier.
        weak_min_funding_excess: Funding must exceed adverse spread by this %.
    """
    if tier_net < min_funding_spread:
        return None
    if price_spread_pct >= Decimal("0"):
        return EntryTier.TOP.value
    if abs(price_spread_pct) <= total_cost_pct:
        return EntryTier.MEDIUM.value
    if tier_net - abs(price_spread_pct) >= weak_min_funding_excess:
        return EntryTier.WEAK.value
    return None


class _ScannerEvaluatorMixin:
    """Mixin providing pair evaluation logic for Scanner."""

    # ── Evaluate a single pair ───────────────────────────────────

    async def _evaluate_pair(
        self,
        symbol: str,
        eid_a: str,
        eid_b: str,
        funding: Dict[str, dict],
        adapters: Dict[str, "ExchangeAdapter"],
    ) -> Optional[OpportunityCandidate]:
        rate_a = funding[eid_a]["rate"]
        rate_b = funding[eid_b]["rate"]
        interval_a = funding[eid_a].get("interval_hours", 8)
        interval_b = funding[eid_b].get("interval_hours", 8)

        # Try both directions, pick the one with the higher funding spread
        # Prefer qualified over unqualified
        best = None
        for long_eid, short_eid in [(eid_a, eid_b), (eid_b, eid_a)]:
            long_rate = funding[long_eid]["rate"]
            short_rate = funding[short_eid]["rate"]
            long_interval = funding[long_eid].get("interval_hours", 8)
            short_interval = funding[short_eid].get("interval_hours", 8)

            opp = await self._evaluate_direction(
                symbol, long_eid, short_eid,
                long_rate, short_rate,
                long_interval, short_interval,
                funding, adapters,
            )
            if opp is None:
                continue
            if best is None:
                best = opp
            elif opp.qualified and not best.qualified:
                best = opp  # prefer qualified
            elif opp.qualified == best.qualified and opp.net_edge_pct > best.net_edge_pct:
                best = opp  # same qualification level, pick better next-payment net

        return best

    async def _evaluate_direction(
        self,
        symbol: str,
        long_eid: str, short_eid: str,
        long_rate: Decimal, short_rate: Decimal,
        long_interval: int, short_interval: int,
        funding: Dict[str, dict],
        adapters: Dict[str, "ExchangeAdapter"],
    ) -> Optional[OpportunityCandidate]:
        """Evaluate one direction (long on A, short on B).

        Entry logic — PURE FUNDING ARBITRAGE:
          1. Compute immediate funding spread: (-long_rate) + short_rate (actual next payment, no 8h normalization)
          2. Per-payment analysis → HOLD (both sides income) or CHERRY_PICK (one income, one cost)
          3. HOLD:        both sides income, imminent spread ≥ min_funding_spread
          4. CHERRY_PICK: income side fires first, collect BEFORE the cost side fires
        """
        tp = self._cfg.trading_params

        # ── Compute funding spread (no 8h normalization) ─────────
        spread_info = calculate_funding_spread(
            long_rate, short_rate,
            long_interval_hours=long_interval,
            short_interval_hours=short_interval,
        )
        immediate_spread = spread_info["immediate_spread_pct"]
        funding_spread = spread_info["funding_spread_pct"]

        # Guard f-string: called for every exchange pair on every scan cycle
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                f"[PAIR_EVAL] [{symbol}] PAIR EVALUATION: "
                f"LONG({long_eid})={long_rate:.8f} ({long_rate*100:.6f}%, {long_interval}h) | "
                f"SHORT({short_eid})={short_rate:.8f} ({short_rate*100:.6f}%, {short_interval}h) | "
                f"Spread={immediate_spread:.4f}% (immediate), {funding_spread:.4f}% (8h norm)",
                extra={
                    "action": "pair_evaluation",
                    "symbol": symbol,
                    "long_eid": long_eid,
                    "short_eid": short_eid,
                    "long_rate": str(long_rate),
                    "short_rate": str(short_rate),
                },
            )

        # ── Per-payment analysis ─────────────────────────────────
        pnl = analyze_per_payment_pnl(long_rate, short_rate)

        # Both sides cost us → skip
        if pnl["both_cost"]:
            return None

        # ── Fees & buffers ───────────────────────────────────────
        # Use the in-memory cache (sync, zero coroutine overhead) when available.
        # Falls back to a REST fetch only on the very first scan after startup.
        long_spec = (
            adapters[long_eid].get_cached_instrument_spec(symbol)
            or await adapters[long_eid].get_instrument_spec(symbol)
        )
        short_spec = (
            adapters[short_eid].get_cached_instrument_spec(symbol)
            or await adapters[short_eid].get_instrument_spec(symbol)
        )
        if not long_spec or not short_spec:
            return None
        fees_pct = calculate_fees(long_spec.taker_fee, short_spec.taker_fee)
        # slippage + safety buffers (fixed costs paid at entry/exit regardless)
        buffers_pct = tp.slippage_buffer_pct + tp.safety_buffer_pct
        total_cost_pct = fees_pct + buffers_pct

        # ── Live price basis check (info only — NOT added to entry cost) ──
        price_basis_pct = Decimal("0")
        _live_basis_available = False
        try:
            long_price_raw = adapters[long_eid].get_mark_price(symbol)
            short_price_raw = adapters[short_eid].get_mark_price(symbol)
            long_price = Decimal(str(long_price_raw)) if long_price_raw else Decimal("0")
            short_price = Decimal(str(short_price_raw)) if short_price_raw else Decimal("0")
            if long_price > 0 and short_price > 0:
                _live_basis_available = True
                raw_basis = (long_price - short_price) / short_price * Decimal("100")
                price_basis_pct = raw_basis  # signed — informational only
                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug(
                        f"[{symbol}] Entry price basis: {long_eid}={long_price} vs "
                        f"{short_eid}={short_price} → {float(price_basis_pct):+.4f}% (info only, not added to cost)"
                    )
        except Exception as _basis_err:
            logger.debug(f"[{symbol}] Price basis check failed: {_basis_err}")

        # Guard f-string: spec.taker_fee formatting on every pair per scan
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                f"[{symbol}] NEV Calculation: "
                f"Spread={immediate_spread:.4f}% (immediate), {funding_spread:.4f}% (8h) - "
                f"Fees={fees_pct:.4f}% (L:{long_spec.taker_fee*100:.4f}% + S:{short_spec.taker_fee*100:.4f}% × 2) - "
                f"Slippage={tp.slippage_buffer_pct:.4f}% - "
                f"Safety={tp.safety_buffer_pct:.4f}% - "
                f"PriceBasis={float(price_basis_pct):+.4f}% (info only)"
            )

        # ── Tier classification (funding arb + price arb) ─────────
        if _live_basis_available and long_price > 0:
            price_spread_pct = (short_price - long_price) / long_price * Decimal("100")
        else:
            price_spread_pct = Decimal("0")

        # Net funding after costs (used for tier classification)
        _tier_net = immediate_spread - total_cost_pct
        entry_tier = _classify_tier(
            _tier_net, price_spread_pct, total_cost_pct,
            tp.min_funding_spread, tp.weak_min_funding_excess,
        )

        if entry_tier and logger.isEnabledFor(logging.DEBUG):
            tier_emoji = {"top": "🏆", "medium": "📊", "weak": "⚡"}.get(entry_tier, "")
            logger.debug(
                f"[{symbol}] Tier: {tier_emoji} {entry_tier.upper()} | "
                f"Price spread: {float(price_spread_pct):+.4f}% | "
                f"Net funding: {float(_tier_net):.4f}%"
            )

        # ── Gate: price spread too adverse for all tiers ──────────
        _tier_too_adverse = (
            entry_tier is None
            and _live_basis_available
            and _tier_net >= tp.min_funding_spread
        )
        if _tier_too_adverse:
            entry_tier = "adverse"
            logger.debug(
                f"[{symbol}] Display-only (adverse): price spread {float(price_spread_pct):+.4f}% "
                f"too adverse for all tiers (funding excess {float(_tier_net - abs(price_spread_pct)):.4f}% "
                f"< required {tp.weak_min_funding_excess}%)"
            )

        # ── Qualification tracking (soft gates for display) ──────
        qualified = True

        # ── Entry window: ANY income side with imminent funding ────
        current_entry_window_minutes = tp.narrow_entry_window_minutes

        now_ms = time.time() * 1000
        long_next = funding[long_eid].get("next_timestamp")
        short_next = funding[short_eid].get("next_timestamp")

        # Classify each side: income or cost?
        long_is_income = long_rate < 0   # long on negative → we get paid
        short_is_income = short_rate > 0  # short on positive → we get paid

        # Minutes until each side's next funding
        long_mins = (long_next - now_ms) / 60_000 if (long_next and long_next > now_ms) else None
        short_mins = (short_next - now_ms) / 60_000 if (short_next and short_next > now_ms) else None

        # Is each income side within the entry window?
        long_imminent = long_is_income and long_mins is not None and long_mins <= current_entry_window_minutes
        short_imminent = short_is_income and short_mins is not None and short_mins <= current_entry_window_minutes

        # Stale: income side has funding timestamp in the past
        long_stale = long_is_income and long_next is not None and long_next <= now_ms
        short_stale = short_is_income and short_next is not None and short_next <= now_ms

        # Calculate imminent spread: income from imminent payments
        # minus cost from payments that also fire during the hold
        imminent_income_pct = Decimal("0")
        imminent_cost_pct = Decimal("0")
        if long_imminent:
            imminent_income_pct += abs(long_rate) * Decimal("100")
        if short_imminent:
            imminent_income_pct += abs(short_rate) * Decimal("100")
        # Cost sides that also fire during the hold window
        if not long_is_income and long_mins is not None and long_mins <= current_entry_window_minutes:
            imminent_cost_pct += abs(long_rate) * Decimal("100")
        if not short_is_income and short_mins is not None and short_mins <= current_entry_window_minutes:
            imminent_cost_pct += abs(short_rate) * Decimal("100")
        imminent_spread_pct = imminent_income_pct - imminent_cost_pct

        # Earliest income payment within window → entry target
        closest_ms = None
        _income_ts = []
        if long_imminent:
            _income_ts.append(long_next)
        if short_imminent:
            _income_ts.append(short_next)
        if _income_ts:
            closest_ms = min(_income_ts)
        elif long_next and long_next > now_ms:
            closest_ms = long_next   # display only
        elif short_next and short_next > now_ms:
            closest_ms = short_next  # display only

        # ── Gate: imminent income must exist & meet threshold ────
        hold_qualified = True
        if long_stale or short_stale:
            hold_qualified = False
        elif not (long_imminent or short_imminent):
            hold_qualified = False
        elif (imminent_spread_pct - total_cost_pct) < tp.min_funding_spread:
            hold_qualified = False

        # ── Determine mode & net (based on imminent payments) ────
        mode: TradeMode = TradeMode.HOLD
        exit_before = None
        n_collections = 0

        gross_pct = imminent_spread_pct
        net_pct = imminent_spread_pct - total_cost_pct

        if hold_qualified:
            # ── HOLD / POT / NUTCRACKER Block ─────────────────
            if pnl["both_income"]:
                mode = TradeMode.POT
                emoji = "🍯"
                label = "POT"
            elif pnl["long_is_income"] or pnl["short_is_income"]:
                if pnl["long_is_income"]:
                    cost_imminent_now = short_mins is not None and short_mins <= current_entry_window_minutes
                    _cost_next_ts_hold = short_next
                else:
                    cost_imminent_now = long_mins is not None and long_mins <= current_entry_window_minutes
                    _cost_next_ts_hold = long_next
                if cost_imminent_now:
                    mode = TradeMode.NUTCRACKER
                    emoji = "🔨🥜"
                    label = "NUTCRACKER"
                else:
                    mode = TradeMode.CHERRY_PICK
                    emoji = "🍒"
                    label = "CHERRY"
                    if _cost_next_ts_hold and _cost_next_ts_hold > now_ms:
                        exit_before = datetime.fromtimestamp(
                            (_cost_next_ts_hold - 120_000) / 1000, tz=timezone.utc
                        )
                    _income_next_ts = long_next if pnl["long_is_income"] else short_next
                    _cost_next_ts = short_next if pnl["long_is_income"] else long_next
                    if _income_next_ts and _cost_next_ts:
                        _cherry_gap_min = abs(_cost_next_ts - _income_next_ts) / 60_000
                        if _cherry_gap_min < _MIN_CHERRY_GAP_MINUTES:
                            mode = TradeMode.NUTCRACKER
                            emoji = "🔨🥜"
                            label = "NUTCRACKER"
                            exit_before = None
            else:
                mode = TradeMode.HOLD
                emoji = "🤝"
                label = "HOLD"

            if mode == TradeMode.CHERRY_PICK and entry_tier is None:
                entry_tier = _classify_tier(
                    net_pct, price_spread_pct, total_cost_pct,
                    tp.min_funding_spread, tp.weak_min_funding_excess,
                )
            min_to_funding = int((closest_ms - now_ms) / 60_000) if closest_ms else None
            funding_tag = f"{min_to_funding}min" if min_to_funding is not None else "unknown"
            tier_tag = f" [{entry_tier.upper()}]" if entry_tier else ""
            price_tag = f" price_spread={float(price_spread_pct):+.4f}%" if _live_basis_available else ""
            _is_adverse = entry_tier == "adverse"
            _log_prefix = "⚠️ [NO ENTRY — adverse price spread]" if _is_adverse else "🎯 OPPORTUNITY FOUND"
            logger.info(
                f"{_log_prefix} [{symbol}] ({label} {emoji}){tier_tag}: "
                f"L({long_eid}) @ {long_rate:.8f} | S({short_eid}) @ {short_rate:.8f} | "
                f"NET={net_pct:.4f}%{price_tag} | NEXT_FUNDING={funding_tag}",
                extra={
                    "action": "opportunity_found" if not _is_adverse else "opportunity_adverse",
                    "symbol": symbol,
                    "mode": mode,
                    "long_rate": str(long_rate),
                    "short_rate": str(short_rate),
                    "min_to_funding": min_to_funding,
                },
            )
        else:
            # ── HOLD didn't qualify — try CHERRY_PICK ────────────
            qualified = False  # default off, cherry_pick turns it back on
            if not pnl["both_cost"] and not (long_stale or short_stale):
                cherry_ok = False
                if pnl["long_is_income"]:
                    income_pnl = pnl["long_pnl_per_payment"]
                    income_interval = long_interval
                    income_eid = long_eid
                    cost_eid = short_eid
                elif pnl["short_is_income"]:
                    income_pnl = pnl["short_pnl_per_payment"]
                    income_interval = short_interval
                    income_eid = short_eid
                    cost_eid = long_eid
                else:
                    income_pnl = None

                if income_pnl is not None:
                    cost_next_ts = funding[cost_eid].get("next_timestamp")
                    income_next_ts = funding[income_eid].get("next_timestamp")
                    if cost_next_ts and income_next_ts:
                        cp_now_ms = time.time() * 1000
                        ms_until_cost = cost_next_ts - cp_now_ms
                        ms_until_income = income_next_ts - cp_now_ms
                        minutes_until_cost = ms_until_cost / 60_000
                        minutes_until_income = ms_until_income / 60_000

                        _MIN_INCOME_MINUTES = 2.0
                        if (minutes_until_cost >= _MIN_WINDOW_MINUTES
                                and minutes_until_income >= _MIN_INCOME_MINUTES
                                and (minutes_until_cost - minutes_until_income) >= _MIN_CHERRY_GAP_MINUTES
                                and minutes_until_income < minutes_until_cost
                                and minutes_until_income <= current_entry_window_minutes):
                            cp_gross = calculate_cherry_pick_edge(income_pnl, 1)
                            cp_net = cp_gross - total_cost_pct
                            if cp_net >= tp.min_funding_spread:
                                cherry_ok = True
                                qualified = True
                                mode = TradeMode.CHERRY_PICK
                                gross_pct = cp_gross
                                net_pct = cp_net
                                n_collections = 1
                                exit_before = datetime.fromtimestamp(
                                    (cost_next_ts - 120_000) / 1000, tz=timezone.utc
                                )
                                closest_ms = income_next_ts
                                logger.info(
                                    f"🍒 Cherry-pick {symbol}: collect 1× {income_interval}h payment "
                                    f"(gross={float(cp_gross):.4f}%, net={float(cp_net):.4f}%) — "
                                    f"enter {int(minutes_until_income)}min before payment, "
                                    f"exit before {exit_before.strftime('%H:%M UTC')}",
                                    extra={
                                        "action": "cherry_pick_found", "symbol": symbol, "mode": "cherry_pick"},
                                )
                                entry_tier = _classify_tier(
                                    cp_net, price_spread_pct, total_cost_pct,
                                    tp.min_funding_spread, tp.weak_min_funding_excess,
                                )

        # ── Force disqualify if price spread is too adverse for any tier ──
        if _tier_too_adverse:
            qualified = False

        # ── Skip truly uninteresting candidates (no positive spread) ──
        if not qualified and immediate_spread <= Decimal("0"):
            return None

        # ── Build opportunity ────────────────────────────────────
        if qualified:
            opp = await self._build_opportunity(
                symbol, long_eid, short_eid,
                long_rate, short_rate,
                gross_pct, fees_pct, net_pct,
                adapters, mode=mode,
                long_interval_hours=long_interval,
                short_interval_hours=short_interval,
                next_funding_ms=closest_ms,
                long_next_funding_ms=long_next,
                short_next_funding_ms=short_next,
                exit_before=exit_before,
                n_collections=n_collections,
                entry_tier=entry_tier,
                price_spread_pct=price_spread_pct,
            )
            return opp
        else:
            # Lightweight display-only candidate (no API calls for balance/ticker)
            min_interval = min(long_interval, short_interval)
            immediate_net = immediate_spread - total_cost_pct

            # ── Determine display mode FIRST ────────────────────
            if pnl["both_income"]:
                mode = TradeMode.POT
            elif pnl["long_is_income"] and not pnl["short_is_income"]:
                cost_mins_disp = short_mins
                income_interval_mins = long_interval * 60
                if cost_mins_disp is not None and cost_mins_disp < income_interval_mins:
                    mode = TradeMode.NUTCRACKER
                else:
                    mode = TradeMode.CHERRY_PICK
            elif pnl["short_is_income"] and not pnl["long_is_income"]:
                cost_mins_disp = long_mins
                income_interval_mins = short_interval * 60
                if cost_mins_disp is not None and cost_mins_disp < income_interval_mins:
                    mode = TradeMode.NUTCRACKER
                else:
                    mode = TradeMode.CHERRY_PICK

            # ── Projected net: accurate per-mode calculation ──────
            _display_window = float(tp.max_entry_window_minutes)
            projected_income_pct = Decimal("0")
            if long_is_income and long_mins is not None and long_mins <= _display_window:
                projected_income_pct += abs(long_rate) * Decimal("100")
            if short_is_income and short_mins is not None and short_mins <= _display_window:
                projected_income_pct += abs(short_rate) * Decimal("100")

            projected_cost_pct = Decimal("0")
            if mode == TradeMode.NUTCRACKER:
                if not long_is_income:
                    projected_cost_pct += abs(long_rate) * Decimal("100")
                if not short_is_income:
                    projected_cost_pct += abs(short_rate) * Decimal("100")

            projected_net_pct = projected_income_pct - projected_cost_pct - total_cost_pct
            hourly_rate = projected_net_pct / Decimal(str(min_interval)) if min_interval > 0 else Decimal("0")
            return OpportunityCandidate(
                symbol=symbol,
                long_exchange=long_eid,
                short_exchange=short_eid,
                long_funding_rate=long_rate,
                short_funding_rate=short_rate,
                funding_spread_pct=funding_spread,
                immediate_spread_pct=immediate_spread,
                immediate_net_pct=immediate_net,
                gross_edge_pct=gross_pct,
                fees_pct=fees_pct,
                net_edge_pct=projected_net_pct,
                suggested_qty=Decimal("0"),
                reference_price=Decimal("0"),
                min_interval_hours=min_interval,
                hourly_rate_pct=hourly_rate,
                next_funding_ms=closest_ms,
                long_next_funding_ms=long_next,
                short_next_funding_ms=short_next,
                long_interval_hours=long_interval,
                short_interval_hours=short_interval,
                qualified=False,
                mode=mode,
                exit_before=exit_before,
                n_collections=n_collections,
                entry_tier=entry_tier,
                price_spread_pct=price_spread_pct,
            )

    async def _build_opportunity(
        self,
        symbol: str,
        long_eid: str, short_eid: str,
        long_rate: Decimal, short_rate: Decimal,
        gross_pct: Decimal, fees_pct: Decimal, net_pct: Decimal,
        adapters: Dict[str, "ExchangeAdapter"],
        mode: "TradeMode" = TradeMode.HOLD,
        exit_before: Optional[datetime] = None,
        n_collections: int = 0,
        long_interval_hours: int = 8,
        short_interval_hours: int = 8,
        next_funding_ms: Optional[float] = None,
        long_next_funding_ms: Optional[float] = None,
        short_next_funding_ms: Optional[float] = None,
        entry_tier: Optional[str] = None,
        price_spread_pct: Decimal = Decimal("0"),
    ) -> Optional[OpportunityCandidate]:
        """Build opportunity with position sizing (70% of min balance × leverage)."""
        long_bal = await adapters[long_eid].get_balance()
        short_bal = await adapters[short_eid].get_balance()
        free_usd = min(long_bal["free"], short_bal["free"])

        position_pct = self._cfg.risk_limits.position_size_pct
        long_exc_cfg = self._cfg.exchanges.get(long_eid)
        leverage = Decimal(str(long_exc_cfg.leverage if long_exc_cfg and long_exc_cfg.leverage else 5))
        margin = free_usd * position_pct
        notional = margin * leverage
        max_pos = self._cfg.risk_limits.max_position_size_usd
        notional = min(max_pos, notional)

        long_ticker = await adapters[long_eid].get_ticker(symbol)
        price = Decimal(str(long_ticker.get("last", 0)))
        if price <= 0:
            return None
        quantity = notional / price

        spread_info = calculate_funding_spread(
            long_rate, short_rate,
            long_interval_hours=long_interval_hours,
            short_interval_hours=short_interval_hours,
        )

        min_interval = min(long_interval_hours, short_interval_hours)
        immediate_net = spread_info["immediate_spread_pct"] - fees_pct
        hourly_rate = immediate_net / Decimal(str(min_interval)) if min_interval > 0 else Decimal("0")

        return OpportunityCandidate(
            symbol=symbol,
            long_exchange=long_eid,
            short_exchange=short_eid,
            long_funding_rate=long_rate,
            short_funding_rate=short_rate,
            funding_spread_pct=spread_info["funding_spread_pct"],
            immediate_spread_pct=spread_info["immediate_spread_pct"],
            immediate_net_pct=immediate_net,
            gross_edge_pct=gross_pct,
            fees_pct=fees_pct,
            net_edge_pct=net_pct,
            suggested_qty=quantity,
            reference_price=price,
            min_interval_hours=min_interval,
            hourly_rate_pct=hourly_rate,
            next_funding_ms=next_funding_ms,
            long_next_funding_ms=long_next_funding_ms,
            short_next_funding_ms=short_next_funding_ms,
            long_interval_hours=long_interval_hours,
            short_interval_hours=short_interval_hours,
            mode=mode,
            exit_before=exit_before,
            n_collections=n_collections,
            entry_tier=entry_tier,
            price_spread_pct=price_spread_pct,
        )
