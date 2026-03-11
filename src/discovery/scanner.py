"""Scanner — find funding-rate arbitrage opportunities across exchange pairs.

Two modes:
  HOLD:        both sides are income -> hold until edge reverses
  CHERRY_PICK: one side is income, one is cost -> collect income payments,
               exit BEFORE the next costly payment
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Dict, List, Optional

from src.core.contracts import EntryTier, OpportunityCandidate, OrderSide, TradeMode
from src.core.logging import get_logger
from src.discovery._scanner_evaluator import _ScannerEvaluatorMixin, _classify_tier
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

# Re-export for backward compatibility (tests import from src.discovery.scanner)
__all__ = ["Scanner", "_classify_tier"]

logger = get_logger("scanner")

_FUNDING_STALE_SEC = 3600
_TOP_OPPS_LOG_INTERVAL_SEC = 300


class Scanner(_ScannerEvaluatorMixin):
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
        # Cache for common_symbols — rebuilt every 60 scans or when exchanges change
        self._common_symbols_cache: Optional[set] = None
        self._cache_exchange_ids: List[str] = []
        self._cache_scan_count: int = 0

    # ── Lifecycle ────────────────────────────────────────────────

    async def start(self, callback) -> None:
        """Continuously scan; call *callback(opp)* when an opportunity is found."""
        self._running = True
        scan_interval = self._cfg.risk_guard.scanner_interval_sec

        # Start WebSocket watchers for all symbols
        adapters = self._exchanges.all()
        all_symbols = set()
        for adapter in adapters.values():
            all_symbols.update(adapter.symbols)

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
                # Refresh market data (fees, specs) if stale — no-op on most cycles
                await asyncio.gather(
                    *[a.maybe_reload_markets() for a in self._exchanges.all().values()],
                    return_exceptions=True,
                )
                opps = await self.scan_all()

                # Split qualified (tradeable) and display-only
                qualified_opps = [o for o in opps if o.qualified]
                all_opps = list(opps)

                # Sort for DISPLAY: near-term opportunities (payment within 1h) first.
                _now_ms = time.time() * 1000
                _one_hour_ms = 3600_000
                _tier_rank = {"top": 3, "medium": 2, "weak": 1, "adverse": -1}
                all_opps.sort(
                    key=lambda o: (
                        0 if o.entry_tier == "adverse" else 1,
                        1 if (o.next_funding_ms is not None and (o.next_funding_ms - _now_ms) <= _one_hour_ms) else 0,
                        _tier_rank.get(o.entry_tier or "", 0),
                        float(o.immediate_net_pct),
                        float(o.price_spread_pct),
                    ),
                    reverse=True,
                )
                qualified_opps.sort(
                    key=lambda o: (_tier_rank.get(o.entry_tier or "", 0), float(o.net_edge_pct), float(o.price_spread_pct)),
                    reverse=True,
                )

                # Display top 5: qualified first, then fill with display-only
                display_qualified = [o for o in all_opps if o.qualified][:5]
                remaining_slots = 5 - len(display_qualified)
                display_unqualified = [o for o in all_opps if not o.qualified][:remaining_slots] if remaining_slots > 0 else []
                display_top = display_qualified + display_unqualified

                if display_top:
                    now_ts = time.time()
                    if now_ts - self._last_top_log_ts >= _TOP_OPPS_LOG_INTERVAL_SEC:
                        self._last_top_log_ts = now_ts
                        if display_qualified:
                            logger.info(
                                "📊 TOP 5 OPPORTUNITIES (near-term first, then by Net)",
                                extra={"action": "top_opportunities"},
                            )
                        else:
                            best_net = float(all_opps[0].net_edge_pct) if all_opps else 0.0
                            logger.info(
                                f"⚠️ No qualified opportunities now (best display net={best_net:+.4f}%). Showing display-only top 5.",
                                extra={"action": "top_opportunities_empty"},
                            )
                        for idx, opp in enumerate(display_top, 1):
                            immediate_spread = (
                                (-opp.long_funding_rate) + opp.short_funding_rate
                            ) * Decimal("100")
                            q_mark = "✅" if opp.qualified else "○ "
                            reject_reason = ""
                            if not opp.qualified:
                                if opp.net_edge_pct <= Decimal("0"):
                                    reject_reason = " [REJECT: NET<=0]"
                                elif opp.entry_tier == "adverse":
                                    reject_reason = " [REJECT: ADVERSE]"
                                else:
                                    reject_reason = " [REJECT: RULES]"
                            tier_mark = f" [{opp.entry_tier.upper()}]" if opp.entry_tier else ""
                            price_mark = f" P={float(opp.price_spread_pct):+.2f}%" if opp.price_spread_pct else ""
                            logger.info(
                                f"  {idx}. {q_mark} {opp.symbol} | {opp.long_exchange}↔{opp.short_exchange} | "
                                f"L={opp.long_funding_rate:.6f} S={opp.short_funding_rate:.6f} | "
                                f"Spread: {immediate_spread:.4f}% | Net: {opp.net_edge_pct:.4f}%{tier_mark}{price_mark}{reject_reason} | "
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
                                "immediate_net_pct": float(o.immediate_net_pct),
                                "hourly_rate_pct": float(o.hourly_rate_pct),
                                "min_interval_hours": o.min_interval_hours,
                                "next_funding_ms": o.next_funding_ms,
                                "long_next_funding_ms": o.long_next_funding_ms,
                                "short_next_funding_ms": o.short_next_funding_ms,
                                "long_rate": float(o.long_funding_rate),
                                "short_rate": float(o.short_funding_rate),
                                "price": float(o.reference_price),
                                "mode": o.mode,
                                "qualified": o.qualified,
                                "long_interval_hours": o.long_interval_hours,
                                "short_interval_hours": o.short_interval_hours,
                                "entry_tier": o.entry_tier,
                                "price_spread_pct": float(o.price_spread_pct),
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
                    execute_only_best = self._cfg.trading_params.execute_only_best_opportunity

                    if execute_only_best and qualified_opps:
                        # Send best opportunity PER exchange pair
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
                    except Exception as exc:
                        logger.debug(f"Scan error log publish failed: {exc}")
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

        # Common symbols set is stable between scans.
        # Rebuild only every 60 calls (~5 min at 5 s intervals) or when exchanges change.
        self._cache_scan_count += 1
        if (
            self._common_symbols_cache is None
            or exchange_ids != self._cache_exchange_ids
            or self._cache_scan_count % 60 == 0
        ):
            symbol_sets = [set(adapters[eid].symbols) for eid in exchange_ids]
            all_symbols = set.union(*symbol_sets)
            symbol_counts = {s: sum(1 for ss in symbol_sets if s in ss) for s in all_symbols}
            self._common_symbols_cache = {s for s, c in symbol_counts.items() if c >= 2}
            self._cache_exchange_ids = exchange_ids
        common_symbols = self._common_symbols_cache

        # Batch cooldown check: one Redis pipeline instead of N round-trips
        cooled_symbols = await self._redis.get_cooled_down_symbols(list(common_symbols))

        parallelism = self._cfg.execution.scan_parallelism
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(f"Scanning {len(common_symbols)} symbols (on 2+ exchanges) across {len(exchange_ids)} exchanges (parallelism={parallelism})")

        results: List[OpportunityCandidate] = []

        symbol_list = list(common_symbols)
        semaphore = asyncio.Semaphore(parallelism)

        async def bounded_scan(symbol):
            async with semaphore:
                return await self._scan_symbol(symbol, adapters, exchange_ids, cooled_symbols)

        gathered = await asyncio.gather(*[bounded_scan(s) for s in symbol_list], return_exceptions=True)

        for symbol_results in gathered:
            if isinstance(symbol_results, Exception):
                logger.debug(f"Symbol scan error: {symbol_results}")
                continue
            if symbol_results:
                results.extend(symbol_results)

        elapsed = time.monotonic() - t0
        if results:
            results.sort(key=lambda o: o.immediate_net_pct, reverse=True)
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
        self, symbol: str, adapters: Dict[str, "ExchangeAdapter"], exchange_ids: List[str],
        cooled_symbols: set[str] = frozenset(),
    ) -> List[OpportunityCandidate]:
        """Scan a single symbol for opportunities using WebSocket-cached rates."""
        if symbol in cooled_symbols:
            return []

        funding: Dict[str, dict] = {}
        eligible_eids = [eid for eid in exchange_ids if symbol in adapters[eid].symbols]
        if len(eligible_eids) < 2:
            return []

        for eid in eligible_eids:
            cached = adapters[eid].get_funding_rate_cached(symbol)
            if cached:
                funding[eid] = cached

        if len(funding) < 2:
            return []

        if logger.isEnabledFor(logging.DEBUG):
            funding_detail = " | ".join(
                f"{eid}: rate={funding[eid]['rate']:.8f} ({funding[eid]['rate']*100:.6f}%, interval={funding[eid].get('interval_hours', 8)}h"
                for eid in sorted(funding.keys())
            )
            logger.debug(
                f"[ALL_RATES] [{symbol}] SCANNER RETRIEVED RATES: {funding_detail}",
                extra={
                    "action": "scanner_rates_retrieved",
                    "symbol": symbol,
                },
            )

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




