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
    calculate_funding_edge,
)

if TYPE_CHECKING:
    from src.core.config import Config
    from src.exchanges.adapter import ExchangeAdapter, ExchangeManager
    from src.storage.redis_client import RedisClient

logger = get_logger("scanner")

_FUNDING_STALE_SEC = 3600
_MIN_WINDOW_MINUTES = 30


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

    # ── Lifecycle ────────────────────────────────────────────────

    async def start(self, callback) -> None:
        """Continuously scan; call *callback(opp)* when an opportunity is found."""
        self._running = True
        scan_interval = getattr(self._cfg.risk_guard, 'scanner_interval_sec', 30)
        logger.info(f"Scanner started (interval: {scan_interval}s)", extra={"action": "scanner_start"})

        while self._running:
            try:
                opps = await self.scan_all()
                
                # Sort by net_edge_bps and display top 5 opportunities
                if opps:
                    opps.sort(key=lambda o: o.net_edge_bps, reverse=True)
                    top_5 = opps[:5]
                    
                    logger.info("📊 TOP 5 OPPORTUNITIES", extra={"action": "top_opportunities"})
                    for idx, opp in enumerate(top_5, 1):
                        logger.info(
                            f"  {idx}. {opp.symbol} | {opp.long_exchange}↔{opp.short_exchange} | "
                            f"Edge: {opp.net_edge_bps:.2f} bps",
                            extra={
                                "action": "opportunity",
                                "data": {
                                    "rank": idx,
                                    "symbol": opp.symbol,
                                    "edge_bps": opp.net_edge_bps,
                                    "pair": f"{opp.long_exchange}_{opp.short_exchange}"
                                }
                            }
                        )
                    
                    # Publish opportunities to Redis for frontend
                    if self._publisher:
                        opp_data = [
                            {
                                "symbol": o.symbol,
                                "long_exchange": o.long_exchange,
                                "short_exchange": o.short_exchange,
                                "net_bps": float(o.net_edge_bps),
                                "gross_bps": float(o.gross_edge_bps),
                                "long_rate": float(o.long_funding_rate),
                                "short_rate": float(o.short_funding_rate),
                                "price": float(o.reference_price),
                                "mode": o.mode,
                            }
                            for o in top_5
                        ]
                        await self._publisher.publish_opportunities(opp_data)
                        await self._publisher.publish_log(
                            "INFO",
                            f"Scan complete: {len(opps)} opportunities found. Best: {opps[0].symbol} {opps[0].net_edge_bps:.2f} bps"
                        )
                    
                    # Only execute the best opportunity
                    best_opp = opps[0]
                    await callback(best_opp)
                else:
                    if self._publisher:
                        await self._publisher.publish_opportunities([])
                        await self._publisher.publish_log("INFO", "Scan complete: 0 opportunities found")
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

    # ── Scan logic ───────────────────────────────────────────────

    async def scan_all(self) -> List[OpportunityCandidate]:
        """Scan every (symbol × exchange-pair) for funding edge."""
        adapters = self._exchanges.all()
        exchange_ids = list(adapters.keys())
        if len(exchange_ids) < 2:
            return []

        # Get all common symbols across exchanges
        symbol_sets = [set(adapters[eid]._exchange.markets.keys()) for eid in exchange_ids]
        common_symbols = set.intersection(*symbol_sets)
        
        # Parallelism for faster scanning
        parallelism = getattr(self._cfg.execution, 'scan_parallelism', 10)
        logger.debug(f"Scanning {len(common_symbols)} common symbols across {len(exchange_ids)} exchanges (parallelism={parallelism})")

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

        if results:
            results.sort(key=lambda o: o.net_edge_bps, reverse=True)
            logger.debug(
                f"Found {len(results)} opportunities, best={results[0].net_edge_bps:.1f} bps",
                extra={"action": "scan_result", "data": {"count": len(results)}},
            )
        return results

    async def _scan_symbol(
        self, symbol: str, adapters: Dict[str, "ExchangeAdapter"], exchange_ids: List[str]
    ) -> List[OpportunityCandidate]:
        """Scan a single symbol for opportunities."""
        # Cooldown check
        if await self._redis.is_cooled_down(symbol):
            return []

        # Fetch funding across all exchanges in parallel
        funding: Dict[str, dict] = {}
        tasks = {eid: adapters[eid].get_funding_rate(symbol) for eid in exchange_ids}
        gathered = await asyncio.gather(*tasks.values(), return_exceptions=True)

        for eid, result in zip(tasks.keys(), gathered):
            if isinstance(result, Exception):
                logger.debug(f"Funding fetch failed {eid}/{symbol}: {result}")
                continue
            if self._is_stale(result):
                logger.debug(f"Stale funding data for {eid}/{symbol}")
                continue
            funding[eid] = result

        if len(funding) < 2:
            return []

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

        # Try both directions, pick the better one
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
            if opp and (best is None or opp.net_edge_bps > best.net_edge_bps):
                best = opp

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
        """Evaluate one direction (long on A, short on B)."""
        pnl = analyze_per_payment_pnl(long_rate, short_rate)

        # Both sides cost us → skip
        if pnl["both_cost"]:
            return None

        # Fees
        long_spec = await adapters[long_eid].get_instrument_spec(symbol)
        short_spec = await adapters[short_eid].get_instrument_spec(symbol)
        if not long_spec or not short_spec:
            return None
        fees_bps = calculate_fees(long_spec.taker_fee, short_spec.taker_fee)
        tp = self._cfg.trading_params
        buffers_bps = tp.slippage_buffer_bps + tp.safety_buffer_bps + tp.basis_buffer_bps
        total_cost_bps = fees_bps + buffers_bps

        if pnl["both_income"]:
            # ── HOLD mode: both sides are income ────────────────
            edge_info = calculate_funding_edge(
                long_rate, short_rate,
                long_interval_hours=long_interval,
                short_interval_hours=short_interval,
            )
            net_bps = edge_info["edge_bps"] - total_cost_bps
            if net_bps < tp.min_net_bps:
                return None

            opp = await self._build_opportunity(
                symbol, long_eid, short_eid,
                long_rate, short_rate,
                edge_info["edge_bps"], fees_bps, net_bps,
                adapters, mode="hold",
            )
            return opp

        else:
            # One side income, one side cost — check net first
            edge_info = calculate_funding_edge(
                long_rate, short_rate,
                long_interval_hours=long_interval,
                short_interval_hours=short_interval,
            )

            if edge_info["edge_bps"] - total_cost_bps >= tp.min_net_bps:
                # ── HOLD mode: net-positive classic arbitrage ───
                net_bps = edge_info["edge_bps"] - total_cost_bps
                opp = await self._build_opportunity(
                    symbol, long_eid, short_eid,
                    long_rate, short_rate,
                    edge_info["edge_bps"], fees_bps, net_bps,
                    adapters, mode="hold",
                )
                return opp

            # ── CHERRY_PICK mode: net-negative, but we can dodge cost ─
            # Identify income vs cost sides
            if pnl["long_is_income"]:
                income_pnl = pnl["long_pnl_per_payment"]
                income_interval = long_interval
                cost_eid = short_eid
            else:
                income_pnl = pnl["short_pnl_per_payment"]
                income_interval = short_interval
                cost_eid = long_eid

            # How long until the COST side charges us?
            cost_next_ts = funding[cost_eid].get("next_timestamp")
            if not cost_next_ts:
                return None

            now_ms = time.time() * 1000
            ms_until_cost = cost_next_ts - now_ms
            minutes_until_cost = ms_until_cost / 60_000

            if minutes_until_cost < _MIN_WINDOW_MINUTES:
                return None  # Too close to cost payment

            # How many income payments can we collect?
            hours_until_cost = ms_until_cost / 3_600_000
            n_collections = int(hours_until_cost / income_interval)
            if n_collections < 1:
                return None

            # Edge = total income we'll collect (minus open/close fees)
            gross_bps = calculate_cherry_pick_edge(income_pnl, n_collections)
            net_bps = gross_bps - total_cost_bps

            if net_bps < tp.min_net_bps:
                return None

            # Exit 2 minutes before cost payment for safety
            exit_before = datetime.fromtimestamp(
                (cost_next_ts - 120_000) / 1000, tz=timezone.utc
            )

            opp = await self._build_opportunity(
                symbol, long_eid, short_eid,
                long_rate, short_rate,
                gross_bps, fees_bps, net_bps,
                adapters, mode="cherry_pick",
                exit_before=exit_before,
                n_collections=n_collections,
            )
            return opp

    async def _build_opportunity(
        self,
        symbol: str,
        long_eid: str, short_eid: str,
        long_rate: Decimal, short_rate: Decimal,
        gross_bps: Decimal, fees_bps: Decimal, net_bps: Decimal,
        adapters: Dict[str, "ExchangeAdapter"],
        mode: str = "hold",
        exit_before: Optional[datetime] = None,
        n_collections: int = 0,
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

        return OpportunityCandidate(
            symbol=symbol,
            long_exchange=long_eid,
            short_exchange=short_eid,
            long_funding_rate=long_rate,
            short_funding_rate=short_rate,
            gross_edge_bps=gross_bps,
            fees_bps=fees_bps,
            net_edge_bps=net_bps,
            suggested_qty=quantity,
            reference_price=price,
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


