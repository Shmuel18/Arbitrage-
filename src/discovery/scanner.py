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
                
                # Sort by funding_spread_pct and display top 5 opportunities
                if opps:
                    opps.sort(key=lambda o: o.funding_spread_pct, reverse=True)
                    top_5 = opps[:5]

                    now_ts = time.time()
                    if now_ts - self._last_top_log_ts >= _TOP_OPPS_LOG_INTERVAL_SEC:
                        self._last_top_log_ts = now_ts
                        logger.info(
                            "📊 TOP 5 OPPORTUNITIES (by Funding Spread)",
                            extra={"action": "top_opportunities"},
                        )
                        for idx, opp in enumerate(top_5, 1):
                            immediate_spread = (
                                (-opp.long_funding_rate) + opp.short_funding_rate
                            ) * Decimal("100")
                            logger.info(
                                f"  {idx}. {opp.symbol} | {opp.long_exchange}↔{opp.short_exchange} | "
                                f"L={opp.long_funding_rate:.6f} S={opp.short_funding_rate:.6f} | "
                                f"Spread: {immediate_spread:.4f}% | Net: {opp.net_edge_pct:.4f}%",
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
                    
                    # Publish opportunities to Redis for frontend
                    if self._publisher:
                        opp_data = [
                            {
                                "symbol": o.symbol,
                                "long_exchange": o.long_exchange,
                                "short_exchange": o.short_exchange,
                                "net_pct": float(o.net_edge_pct),
                                "gross_pct": float(o.gross_edge_pct),
                                "funding_spread_pct": float(o.funding_spread_pct),
                                "long_rate": float(o.long_funding_rate),
                                "short_rate": float(o.short_funding_rate),
                                "price": float(o.reference_price),
                                "mode": o.mode,
                            }
                            for o in top_5
                        ]
                        await self._publisher.publish_opportunities(opp_data)
                        if now_ts - self._last_top_log_ts < 1:
                            await self._publisher.publish_log(
                                "INFO",
                                f"Top 5 updated: {opps[0].symbol} best spread={opps[0].funding_spread_pct:.4f}%"
                            )
                    
                    # Try top opportunities — controller filters blacklisted/duplicate/capped
                    for opp in top_5:
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
        adapters = self._exchanges.all()
        exchange_ids = list(adapters.keys())
        if len(exchange_ids) < 2:
            return []

        # Get all symbols available on at least 2 exchanges (not just ALL)
        symbol_sets = [set(adapters[eid]._exchange.markets.keys()) for eid in exchange_ids]
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

        if results:
            results.sort(key=lambda o: o.funding_spread_pct, reverse=True)
            logger.debug(
                f"Found {len(results)} opportunities, best spread={results[0].funding_spread_pct:.4f}%",
                extra={"action": "scan_result", "data": {"count": len(results)}},
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
        eligible_eids = [eid for eid in exchange_ids if symbol in adapters[eid]._exchange.markets]
        if len(eligible_eids) < 2:
            return []
        
        for eid in eligible_eids:
            cached = adapters[eid].get_funding_rate_cached(symbol)
            if cached:
                funding[eid] = cached
            else:
                # Fallback: fetch from REST if cache empty (e.g., on first run)
                try:
                    data = await adapters[eid].get_funding_rate(symbol)
                    if not self._is_stale(data):
                        funding[eid] = data
                except Exception as e:
                    logger.debug(f"Funding fetch failed {eid}/{symbol}: {e}")
                    continue

        if len(funding) < 2:
            return []

        # Debug: show all available funding rates for this symbol
        funding_display = " | ".join(
            f"{eid}={funding[eid]['rate']:.6f} ({funding[eid].get('interval_hours', 8)}h)"
            for eid in sorted(funding.keys())
        )
        logger.debug(f"[{symbol}] Available rates: {funding_display}")

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
            if opp and (best is None or opp.funding_spread_pct > best.funding_spread_pct):
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
        
        # Log funding rates for clarity
        logger.debug(
            f"[{symbol}] Pair evaluation: "
            f"LONG({long_eid}, {long_interval}h)={long_rate:.6f} | "
            f"SHORT({short_eid}, {short_interval}h)={short_rate:.6f} | "
            f"Spread={immediate_spread:.4f}% (immediate), {funding_spread:.4f}% (8h)"
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

        if pnl["both_income"]:
            # ── HOLD mode: both sides are income ────────────────
            # Gate: funding spread must pass threshold
            if funding_spread < tp.min_funding_spread:
                logger.debug(
                    f"[{symbol}] Rejected (HOLD): spread={funding_spread:.4f}% < "
                    f"min_threshold={tp.min_funding_spread:.4f}%"
                )
                return None

            net_pct = funding_spread - total_cost_pct
            if net_pct < tp.min_net_pct:
                logger.debug(
                    f"[{symbol}] Rejected (HOLD): net={net_pct:.4f}% < "
                    f"min_net={tp.min_net_pct:.4f}% (spread={funding_spread:.4f}%, fees={total_cost_pct:.4f}%)"
                )
                return None

            opp = await self._build_opportunity(
                symbol, long_eid, short_eid,
                long_rate, short_rate,
                funding_spread, fees_pct, net_pct,
                adapters, mode="hold",
                long_interval_hours=long_interval,
                short_interval_hours=short_interval,
            )
            logger.info(
                f"🎯 [{symbol}] OPPORTUNITY FOUND (HOLD): "
                f"L({long_eid}) @ {long_rate:.6f} | S({short_eid}) @ {short_rate:.6f} | "
                f"SPREAD={immediate_spread:.4f}% (immediate), {funding_spread:.4f}% (8h) | "
                f"FEES={total_cost_pct:.4f}% | NET={net_pct:.4f}%"
            )
            return opp

        else:
            # ── One side income, one side cost ───────────────────
            # First check: is a plain HOLD still net-positive?
            if funding_spread >= tp.min_funding_spread and funding_spread - total_cost_pct >= tp.min_net_pct:
                net_pct = funding_spread - total_cost_pct
                opp = await self._build_opportunity(
                    symbol, long_eid, short_eid,
                    long_rate, short_rate,
                    funding_spread, fees_pct, net_pct,
                    adapters, mode="hold",
                    long_interval_hours=long_interval,
                    short_interval_hours=short_interval,
                )
                logger.info(
                    f"🎯 [{symbol}] OPPORTUNITY FOUND (HOLD, mixed): "
                    f"L({long_eid}) @ {long_rate:.6f} | S({short_eid}) @ {short_rate:.6f} | "
                    f"SPREAD={immediate_spread:.4f}% (immediate), {funding_spread:.4f}% (8h) | "
                    f"FEES={total_cost_pct:.4f}% | NET={net_pct:.4f}%"
                )
                return opp

            # ── CHERRY_PICK: income arrives faster than cost ─────
            # Example: short on Bybit (1h) receives, long on Binance (8h) pays
            #   → collect 7 income payments before the 1 cost payment
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

            # How many income payments can we collect before cost?
            hours_until_cost = ms_until_cost / 3_600_000
            n_collections = int(hours_until_cost / income_interval)
            if n_collections < 1:
                return None

            # Total cherry-pick edge = sum of all income collections
            gross_pct = calculate_cherry_pick_edge(income_pnl, n_collections)

            # Gate: cherry-pick total must beat min_funding_spread
            if gross_pct < tp.min_funding_spread:
                return None

            net_pct = gross_pct - total_cost_pct
            if net_pct < tp.min_net_pct:
                return None

            # Exit 2 minutes before cost payment for safety
            exit_before = datetime.fromtimestamp(
                (cost_next_ts - 120_000) / 1000, tz=timezone.utc
            )

            logger.info(
                f"🍒 Cherry-pick {symbol}: collect {n_collections}× every {income_interval}h "
                f"(gross={gross_pct:.4f}%, net={net_pct:.4f}%) — "
                f"exit before {exit_before.strftime('%H:%M UTC')}",
                extra={"action": "cherry_pick_found", "symbol": symbol},
            )

            opp = await self._build_opportunity(
                symbol, long_eid, short_eid,
                long_rate, short_rate,
                gross_pct, fees_pct, net_pct,
                adapters, mode="cherry_pick",
                exit_before=exit_before,
                n_collections=n_collections,
                long_interval_hours=long_interval,
                short_interval_hours=short_interval,
            )
            return opp

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

        return OpportunityCandidate(
            symbol=symbol,
            long_exchange=long_eid,
            short_exchange=short_eid,
            long_funding_rate=long_rate,
            short_funding_rate=short_rate,
            funding_spread_pct=spread_info["funding_spread_pct"],
            gross_edge_pct=gross_pct,
            fees_pct=fees_pct,
            net_edge_pct=net_pct,
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


