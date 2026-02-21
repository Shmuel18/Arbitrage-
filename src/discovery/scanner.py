"""Scanner — find funding-rate arbitrage opportunities across exchange pairs.

Two modes:
  HOLD:        both sides are income -> hold until edge reverses
  CHERRY_PICK: one side is income, one is cost -> collect income payments,
               exit BEFORE the next costly payment
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

from src.core.contracts import OpportunityCandidate, OrderSide
from src.core.logging import get_logger
from src.discovery.calculator import (
    analyze_per_payment_pnl,
    calculate_cherry_pick_edge,
    calculate_fees,
    calculate_funding_spread,
)

if TYPE_CHECKING:
    from src.core.config import Config
    from src.exchanges.adapter import ExchangeAdapter, ExchangeManager
    from src.storage.redis_client import RedisClient

logger = get_logger("scanner")

_FUNDING_STALE_SEC = 3600
_MIN_WINDOW_MINUTES = 30
_TOP_OPPS_LOG_INTERVAL_SEC = 300


class Scanner:
    def __init__(
        self,
        config: "Config",
        exchange_mgr: "ExchangeManager",
        redis: "RedisClient",
        publisher=None,
    ):
        self._cfg = config
        self._exchanges = exchange_mgr
        self._redis = redis
        self._running = False
        self._publisher = publisher
        self._last_top_log_ts = 0.0

    # ── Lifecycle ────────────────────────────────────────────────

    async def start(self, callback) -> None:
        """Continuously scan; call *callback(opp)* when an opportunity is found."""
        self._running = True
        scan_interval = getattr(self._cfg.risk_guard, 'scanner_interval_sec', 30)
        
        # Start WebSocket watchers for all symbols
        adapters = self._exchanges.all()
        all_symbols = set()
        for adapter in adapters.values():
            all_symbols.update(adapter._exchange.symbols)
        
        for adapter in adapters.values():
            try:
                await adapter.start_funding_rate_watchers(list(all_symbols))
            except Exception as e:
                logger.warning(f"Failed to start watchers for {adapter.exchange_id}: {e}")
        
        logger.info(
            f"Scanner started (interval: {scan_interval}s, WebSocket monitoring {len(all_symbols)} symbols)",
            extra={"action": "scanner_start"},
        )

        while self._running:
            try:
                opps = await self.scan_all()
                
                # Split qualified (tradeable) and display-only
                qualified_opps = [o for o in opps if o.qualified]
                all_opps = list(opps)  # includes both qualified and display-only

                # Sort all by hourly return for display
                all_opps.sort(key=lambda o: o.hourly_rate_pct, reverse=True)
                # Sort qualified by immediate net edge for execution (best immediate profit first)
                qualified_opps.sort(key=lambda o: o.immediate_net_pct, reverse=True)

                # Display top 5: qualified first, then fill with display-only
                display_qualified = [o for o in all_opps if o.qualified][:5]
                remaining_slots = 5 - len(display_qualified)
                display_unqualified = [o for o in all_opps if not o.qualified][:remaining_slots] if remaining_slots > 0 else []
                display_top = display_qualified + display_unqualified

                if display_top:
                    now_ts = time.time()
                    if now_ts - self._last_top_log_ts >= _TOP_OPPS_LOG_INTERVAL_SEC:
                        self._last_top_log_ts = now_ts
                        logger.info(
                            "📊 TOP 5 OPPORTUNITIES (by Hourly Return)",
                            extra={"action": "top_opportunities"},
                        )
                        for idx, opp in enumerate(display_top, 1):
                            immediate_spread = (
                                (-opp.long_funding_rate) + opp.short_funding_rate
                            ) * Decimal("100")
                            q_mark = "✅" if opp.qualified else "○ "
                            logger.info(
                                f"  {idx}. {q_mark} {opp.symbol} | {opp.long_exchange}↔{opp.short_exchange} | "
                                f"L={opp.long_funding_rate:.6f} S={opp.short_funding_rate:.6f} | "
                                f"Spread: {immediate_spread:.4f}% | Net: {opp.net_edge_pct:.4f}% | "
                                f"/h: {opp.hourly_rate_pct:.4f}% ({opp.min_interval_hours}h)",
                                extra={
                                    "action": "opportunity",
                                    "data": {
                                        "rank": idx,
                                        "symbol": opp.symbol,
                                        "funding_spread_pct": opp.funding_spread_pct,
                                        "net_pct": opp.net_edge_pct,
                                        "pair": f"{opp.long_exchange}_{opp.short_exchange}",
                                    },
                                },
                            )
                        if self._publisher:
                            await self._publisher.publish_log(
                                "INFO",
                                "Top 5 opportunities updated (5 min interval)",
                            )
                    
                    # Publish ALL display opportunities to Redis for frontend
                    if self._publisher:
                        opp_data = [
                            {
                                "symbol": o.symbol,
                                "long_exchange": o.long_exchange,
                                "short_exchange": o.short_exchange,
                                "net_pct": float(o.net_edge_pct),
                                "gross_pct": float(o.gross_edge_pct),
                                "funding_spread_pct": float(o.funding_spread_pct),
                                "immediate_spread_pct": float(o.immediate_spread_pct),
                                "hourly_rate_pct": float(o.hourly_rate_pct),
                                "min_interval_hours": o.min_interval_hours,
                                "next_funding_ms": o.next_funding_ms,
                                "long_rate": float(o.long_funding_rate),
                                "short_rate": float(o.short_funding_rate),
                                "price": float(o.reference_price),
                                "mode": o.mode,
                                "qualified": o.qualified,
                            }
                            for o in display_top
                        ]
                        await self._publisher.publish_opportunities(opp_data)
                        if now_ts - self._last_top_log_ts < 1:
                            await self._publisher.publish_log(
                                "INFO",
                                f"Top 5 updated: {len(qualified_opps)} qualified, {len(all_opps) - len(qualified_opps)} display-only"
                            )
                    
                    # Send opportunities to controller
                    execute_only_best = getattr(
                        self._cfg.trading_params, 'execute_only_best_opportunity', True
                    )
                    
                    if execute_only_best and qualified_opps:
                        # Send best opportunity PER exchange pair
                        # (sorted by net_edge_pct desc, so first hit per pair is the best)
                        seen_pairs: set[tuple[str, str]] = set()
                        best_per_pair: list = []
                        for opp in qualified_opps:
                            pair = tuple(sorted([opp.long_exchange, opp.short_exchange]))
                            if pair not in seen_pairs:
                                seen_pairs.add(pair)
                                best_per_pair.append(opp)
                        for opp in best_per_pair:
                            logger.info(
                                f"🎯 Sending BEST for {opp.long_exchange}↔{opp.short_exchange}: "
                                f"{opp.symbol} net={opp.net_edge_pct:.4f}%"
                            )
                            await callback(opp)
                    else:
                        # Send top qualified opportunities — controller handles further filtering
                        for opp in qualified_opps[:5]:
                            await callback(opp)
                else:
                    if self._publisher:
                        await self._publisher.publish_opportunities([])
                        if time.time() - self._last_top_log_ts >= _TOP_OPPS_LOG_INTERVAL_SEC:
                            self._last_top_log_ts = time.time()
                            await self._publisher.publish_log("INFO", "Top 5 updated: 0 opportunities found")
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.error(f"Scan cycle error: {e}")
                if self._publisher:
                    try:
                        await self._publisher.publish_log("ERROR", f"Scan error: {e}")
                    except Exception:
                        pass
            await asyncio.sleep(scan_interval)

    def stop(self) -> None:
        self._running = False
        # Cancel all WebSocket watcher tasks
        for adapter in self._exchanges.all().values():
            for task in adapter._ws_tasks:
                task.cancel()

    # ── Scan logic ───────────────────────────────────────────────

    async def scan_all(self) -> List[OpportunityCandidate]:
        """Scan every (symbol × exchange-pair) for funding edge."""
        t0 = time.monotonic()
        adapters = self._exchanges.all()
        exchange_ids = list(adapters.keys())
        if len(exchange_ids) < 2:
            return []

        # Get all symbols available on at least 2 exchanges (not just ALL)
        symbol_sets = [set(adapters[eid]._exchange.symbols) for eid in exchange_ids]
        all_symbols = set.union(*symbol_sets)
        symbol_counts = {s: sum(1 for ss in symbol_sets if s in ss) for s in all_symbols}
        common_symbols = {s for s, c in symbol_counts.items() if c >= 2}
        
        # Parallelism for faster scanning
        parallelism = getattr(self._cfg.execution, 'scan_parallelism', 10)
        logger.debug(f"Scanning {len(common_symbols)} symbols (on 2+ exchanges) across {len(exchange_ids)} exchanges (parallelism={parallelism})")

        results: List[OpportunityCandidate] = []
        
        # Scan symbols in parallel batches
        symbol_list = list(common_symbols)
        scan_tasks = [
            self._scan_symbol(symbol, adapters, exchange_ids)
            for symbol in symbol_list
        ]
        
        # Use semaphore to limit concurrent scans
        semaphore = asyncio.Semaphore(parallelism)
        async def bounded_scan(task):
            async with semaphore:
                return await task
        
        gathered = await asyncio.gather(*[bounded_scan(t) for t in scan_tasks], return_exceptions=True)
        
        for symbol_results in gathered:
            if isinstance(symbol_results, Exception):
                logger.debug(f"Symbol scan error: {symbol_results}")
                continue
            if symbol_results:
                results.extend(symbol_results)

        elapsed = time.monotonic() - t0
        if results:
            results.sort(key=lambda o: o.funding_spread_pct, reverse=True)
            logger.info(
                f"✅ Scan completed: {len(results)} opportunities from {len(common_symbols)} symbols in {elapsed:.1f}s",
                extra={"action": "scan_complete", "data": {"count": len(results), "elapsed": round(elapsed, 1)}},
            )
        else:
            logger.info(
                f"✅ Scan completed: 0 opportunities from {len(common_symbols)} symbols in {elapsed:.1f}s",
                extra={"action": "scan_complete", "data": {"count": 0, "elapsed": round(elapsed, 1)}},
            )
        return results

    async def _scan_symbol(
        self, symbol: str, adapters: Dict[str, "ExchangeAdapter"], exchange_ids: List[str]
    ) -> List[OpportunityCandidate]:
        """Scan a single symbol for opportunities using WebSocket-cached rates."""
        # Cooldown check
        if await self._redis.is_cooled_down(symbol):
            return []

        # Fetch funding from in-memory cache (updated by WebSocket)
        funding: Dict[str, dict] = {}
        eligible_eids = [eid for eid in exchange_ids if symbol in adapters[eid]._exchange.symbols]
        if len(eligible_eids) < 2:
            return []
        
        for eid in eligible_eids:
            cached = adapters[eid].get_funding_rate_cached(symbol)
            if cached:
                funding[eid] = cached
            # No REST fallback — cache is populated by warm_up + background polling

        if len(funding) < 2:
            return []

        # [DEBUG] Log all retrieved rates with full detail
        funding_detail = " | ".join(
            f"{eid}: rate={funding[eid]['rate']:.8f} ({funding[eid]['rate']*100:.6f}%), interval={funding[eid].get('interval_hours', 8)}h"
            for eid in sorted(funding.keys())
        )
        logger.debug(
            f"[ALL_RATES] [{symbol}] SCANNER RETRIEVED RATES: {funding_detail}",
            extra={
                "action": "scanner_rates_retrieved",
                "symbol": symbol,
            },
        )

        # Try every pair
        results = []
        eids = list(funding.keys())
        for i in range(len(eids)):
            for j in range(i + 1, len(eids)):
                opp = await self._evaluate_pair(
                    symbol, eids[i], eids[j], funding, adapters,
                )
                if opp:
                    results.append(opp)
        
        return results

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
            elif opp.qualified == best.qualified and opp.funding_spread_pct > best.funding_spread_pct:
                best = opp  # same qualification level, pick better spread

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
          1. Compute funding spread: (-long_rate) + short_rate   (normalized to 8h)
          2. Per-payment analysis → HOLD or CHERRY_PICK
          3. HOLD:        spread ≥ min_funding_spread AND net > min_net_pct
          4. CHERRY_PICK: total income from N collections > min_funding_spread
             (e.g. income every 1h, cost every 8h → collect 7× before paying once)
        """
        tp = self._cfg.trading_params

        # ── Compute funding spread (normalized to 8h) ────────────
        spread_info = calculate_funding_spread(
            long_rate, short_rate,
            long_interval_hours=long_interval,
            short_interval_hours=short_interval,
        )
        immediate_spread = spread_info["immediate_spread_pct"]
        funding_spread = spread_info["funding_spread_pct"]
        
        # Log funding rates for clarity (DEBUG to avoid log spam)
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
        long_spec = await adapters[long_eid].get_instrument_spec(symbol)
        short_spec = await adapters[short_eid].get_instrument_spec(symbol)
        if not long_spec or not short_spec:
            return None
        fees_pct = calculate_fees(long_spec.taker_fee, short_spec.taker_fee)
        buffers_pct = tp.slippage_buffer_pct + tp.safety_buffer_pct + tp.basis_buffer_pct
        total_cost_pct = fees_pct + buffers_pct
        
        # Debug: show NEV breakdown
        logger.debug(
            f"[{symbol}] NEV Calculation: "
            f"Spread={immediate_spread:.4f}% (immediate), {funding_spread:.4f}% (8h) - "
            f"Fees={fees_pct:.4f}% (L:{long_spec.taker_fee*100:.4f}% + S:{short_spec.taker_fee*100:.4f}% × 2) - "
            f"Slippage={tp.slippage_buffer_pct:.4f}% - "
            f"Safety={tp.safety_buffer_pct:.4f}% - "
            f"Basis={tp.basis_buffer_pct:.4f}%"
        )

        # ── Qualification tracking (soft gates for display) ──────
        qualified = True

        # ── Entry window: ANY income side with imminent funding ────
        # Check each side independently.  If ANY income-generating side
        # has funding within the window, calculate imminent net from
        # payments that actually fire during the hold.
        max_window = getattr(tp, 'max_entry_window_minutes', 15)
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
        long_imminent = long_is_income and long_mins is not None and long_mins <= max_window
        short_imminent = short_is_income and short_mins is not None and short_mins <= max_window

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
        if not long_is_income and long_mins is not None and long_mins <= max_window:
            imminent_cost_pct += abs(long_rate) * Decimal("100")
        if not short_is_income and short_mins is not None and short_mins <= max_window:
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
            # No income side has funding within the entry window
            hold_qualified = False
        elif imminent_spread_pct < tp.min_funding_spread:
            # Imminent gross spread below 0.5%
            hold_qualified = False

        # ── Determine mode & net (based on imminent payments) ────
        mode = "hold"
        gross_pct = imminent_spread_pct
        net_pct = imminent_spread_pct - total_cost_pct
        exit_before = None
        n_collections = 0

        if hold_qualified and pnl["both_income"]:
            # ── HOLD mode: both sides are income ────────────────
            if (imminent_spread_pct - total_cost_pct) < tp.min_net_pct:
                qualified = False
            else:
                min_to_funding = int((closest_ms - now_ms) / 60_000) if closest_ms else None
                funding_tag = f"{min_to_funding}min" if min_to_funding is not None else "unknown"
                logger.info(
                    f"🎯 [{symbol}] OPPORTUNITY FOUND (HOLD): "
                    f"L({long_eid}) @ {long_rate:.8f} ({long_rate*100:.6f}%) | S({short_eid}) @ {short_rate:.8f} ({short_rate*100:.6f}%) | "
                    f"IMMINENT={imminent_spread_pct:.4f}% (gross) | "
                    f"FEES={total_cost_pct:.4f}% | NET={net_pct:.4f}% | NEXT_FUNDING={funding_tag}",
                    extra={
                        "action": "opportunity_found",
                        "symbol": symbol,
                        "mode": "hold",
                        "long_rate": str(long_rate),
                        "short_rate": str(short_rate),
                        "min_to_funding": min_to_funding,
                    },
                )
        elif hold_qualified and not pnl["both_cost"]:
            # ── One side income, one side cost — imminent window met ──
            if (imminent_spread_pct - total_cost_pct) >= tp.min_net_pct:
                # Plain HOLD is net-positive from imminent payments
                min_to_funding = int((closest_ms - now_ms) / 60_000) if closest_ms else None
                funding_tag = f"{min_to_funding}min" if min_to_funding is not None else "unknown"
                logger.info(
                    f"🎯 [{symbol}] OPPORTUNITY FOUND (HOLD, mixed): "
                    f"L({long_eid}) @ {long_rate:.8f} ({long_rate*100:.6f}%) | S({short_eid}) @ {short_rate:.8f} ({short_rate*100:.6f}%) | "
                    f"IMMINENT={imminent_spread_pct:.4f}% (gross) | "
                    f"FEES={total_cost_pct:.4f}% | NET={net_pct:.4f}% | NEXT_FUNDING={funding_tag}",
                    extra={
                        "action": "opportunity_found",
                        "symbol": symbol,
                        "mode": "hold_mixed",
                        "long_rate": str(long_rate),
                        "short_rate": str(short_rate),
                        "min_to_funding": min_to_funding,
                    },
                )
            else:
                qualified = False
        else:
            # ── HOLD didn't qualify — try CHERRY_PICK ────────────
            # Cherry-pick works independently of the 15-min window:
            # Enter now, collect income payments over time, exit
            # BEFORE the costly side fires.
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

                        # Cost must be far enough away (>30 min)
                        # Income must arrive before cost
                        if (minutes_until_cost >= _MIN_WINDOW_MINUTES
                                and minutes_until_income < minutes_until_cost
                                and minutes_until_income <= max_window):
                            # Single-payment cherry_pick: only the immediate income pulse counts.
                            # Do NOT accumulate multiple payments — if this one payment
                            # alone yields ≥ min_funding_spread net, enter.
                            cp_gross = calculate_cherry_pick_edge(income_pnl, 1)
                            cp_net = cp_gross - total_cost_pct
                            if cp_gross >= tp.min_funding_spread and cp_net >= tp.min_net_pct:
                                cherry_ok = True
                                qualified = True
                                mode = "cherry_pick"
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
                                    extra={"action": "cherry_pick_found", "symbol": symbol},
                                )

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
                exit_before=exit_before,
                n_collections=n_collections,
            )
            return opp
        else:
            # Lightweight display-only candidate (no API calls for balance/ticker)
            min_interval = min(long_interval, short_interval)
            immediate_net = immediate_spread - total_cost_pct
            # hourly rate based on immediate net (no 8h normalization)
            hourly_rate = immediate_net / Decimal(str(min_interval)) if min_interval > 0 else Decimal("0")
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
                net_edge_pct=net_pct,
                suggested_qty=Decimal("0"),
                reference_price=Decimal("0"),
                min_interval_hours=min_interval,
                hourly_rate_pct=hourly_rate,
                next_funding_ms=closest_ms,
                qualified=False,
                mode=mode,
                exit_before=exit_before,
                n_collections=n_collections,
            )

    async def _build_opportunity(
        self,
        symbol: str,
        long_eid: str, short_eid: str,
        long_rate: Decimal, short_rate: Decimal,
        gross_pct: Decimal, fees_pct: Decimal, net_pct: Decimal,
        adapters: Dict[str, "ExchangeAdapter"],
        mode: str = "hold",
        exit_before: Optional[datetime] = None,
        n_collections: int = 0,
        long_interval_hours: int = 8,
        short_interval_hours: int = 8,
        next_funding_ms: Optional[float] = None,
    ) -> Optional[OpportunityCandidate]:
        """Build opportunity with position sizing (70% of min balance × leverage)."""
        long_bal = await adapters[long_eid].get_balance()
        short_bal = await adapters[short_eid].get_balance()
        free_usd = min(long_bal["free"], short_bal["free"])

        # Position sizing: position_size_pct (70%) of smallest balance × leverage
        position_pct = self._cfg.risk_limits.position_size_pct  # 0.70
        long_exc_cfg = self._cfg.exchanges.get(long_eid)
        leverage = Decimal(str(long_exc_cfg.leverage if long_exc_cfg and long_exc_cfg.leverage else 5))
        margin = free_usd * position_pct          # 70% of min balance as margin
        notional = margin * leverage              # multiply by leverage for actual position size
        max_pos = self._cfg.risk_limits.max_position_size_usd
        notional = min(max_pos, notional)

        long_ticker = await adapters[long_eid].get_ticker(symbol)
        price = Decimal(str(long_ticker.get("last", 0)))
        if price <= 0:
            return None
        quantity = notional / price

        # Compute the pure funding spread for this pair (always, regardless of mode)
        spread_info = calculate_funding_spread(
            long_rate, short_rate,
            long_interval_hours=long_interval_hours,
            short_interval_hours=short_interval_hours,
        )

        min_interval = min(long_interval_hours, short_interval_hours)
        # hourly rate based on immediate net (no 8h normalization)
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
            mode=mode,
            exit_before=exit_before,
            n_collections=n_collections,
        )

    # ── Helpers ──────────────────────────────────────────────────

    @staticmethod
    def _is_stale(funding: dict) -> bool:
        ts = funding.get("timestamp")
        if ts is None:
            return False                 # some exchanges don't provide it
        age = time.time() * 1000 - ts    # ccxt timestamps are in ms
        return age > _FUNDING_STALE_SEC * 1000


