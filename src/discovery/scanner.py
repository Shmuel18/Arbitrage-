"""
Scanner — find funding-rate arbitrage opportunities across exchange pairs.

Safety features retained from review:
  • staleness check on funding timestamps
  • cooldown check per symbol (Redis)
  • slippage + safety + basis buffers subtracted from edge
  • funding normalization to 8 h
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

from src.core.contracts import OpportunityCandidate, OrderSide
from src.core.logging import get_logger
from src.discovery.calculator import calculate_fees, calculate_funding_edge

if TYPE_CHECKING:
    from src.core.config import Config
    from src.exchanges.adapter import ExchangeAdapter, ExchangeManager
    from src.storage.redis_client import RedisClient

logger = get_logger("scanner")

# How old a funding timestamp can be before we discard it (seconds)
_FUNDING_STALE_SEC = 3600


class Scanner:
    def __init__(
        self,
        config: "Config",
        exchange_mgr: "ExchangeManager",
        redis: "RedisClient",
    ):
        self._cfg = config
        self._exchanges = exchange_mgr
        self._redis = redis
        self._running = False

    # ── Lifecycle ────────────────────────────────────────────────

    async def start(self, callback) -> None:
        """Continuously scan; call *callback(opp)* when an opportunity is found."""
        self._running = True
        logger.info("Scanner started", extra={"action": "scanner_start"})

        while self._running:
            try:
                opps = await self.scan_all()
                for opp in opps:
                    await callback(opp)
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.error(f"Scan cycle error: {e}")
            await asyncio.sleep(30)

    def stop(self) -> None:
        self._running = False

    # ── Scan logic ───────────────────────────────────────────────

    async def scan_all(self) -> List[OpportunityCandidate]:
        """Scan every (symbol × exchange-pair) for funding edge."""
        adapters = self._exchanges.all()
        exchange_ids = list(adapters.keys())
        if len(exchange_ids) < 2:
            return []

        results: List[OpportunityCandidate] = []

        for symbol in self._cfg.watchlist:
            # Cooldown check
            if await self._redis.is_cooled_down(symbol):
                continue

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
                continue

            # Try every pair
            eids = list(funding.keys())
            for i in range(len(eids)):
                for j in range(i + 1, len(eids)):
                    opp = await self._evaluate_pair(
                        symbol, eids[i], eids[j], funding, adapters,
                    )
                    if opp:
                        results.append(opp)

        if results:
            results.sort(key=lambda o: o.net_edge_bps, reverse=True)
            logger.info(
                f"Found {len(results)} opportunities, best={results[0].net_edge_bps:.1f} bps",
                extra={"action": "scan_result", "data": {"count": len(results)}},
            )
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

        # Determine direction: short the higher-rate exchange
        if rate_a >= rate_b:
            short_eid, long_eid = eid_a, eid_b
            short_rate, long_rate = rate_a, rate_b
        else:
            short_eid, long_eid = eid_b, eid_a
            short_rate, long_rate = rate_b, rate_a

        # Funding interval: Binance/OKX = 8h, Bybit/Gate = 8h too in most cases
        # Some exchanges do 1h; detect from next-funding timestamp gap
        interval_h = self._detect_interval(funding.get(short_eid, {}))

        edge_info = calculate_funding_edge(long_rate, short_rate, interval_h)
        edge_bps = edge_info["edge_bps"]

        # Fees
        long_spec = await adapters[long_eid].get_instrument_spec(symbol)
        short_spec = await adapters[short_eid].get_instrument_spec(symbol)
        if not long_spec or not short_spec:
            return None

        fees_bps = calculate_fees(long_spec.taker_fee, short_spec.taker_fee)

        # Buffers (slippage + safety + basis)
        tp = self._cfg.trading_params
        buffers_bps = tp.slippage_buffer_bps + tp.safety_buffer_bps + tp.basis_buffer_bps

        net_bps = edge_bps - fees_bps - buffers_bps

        if net_bps < tp.min_net_bps:
            return None

        # Position size from balance (use the smaller free balance)
        long_bal = await adapters[long_eid].get_balance()
        short_bal = await adapters[short_eid].get_balance()
        free_usd = min(long_bal["free"], short_bal["free"])
        max_pos = self._cfg.risk_limits.max_position_size_usd
        margin_cap = free_usd * self._cfg.risk_limits.max_margin_usage
        notional = min(max_pos, margin_cap)

        # Convert to quantity
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
            gross_edge_bps=edge_bps,
            fees_bps=fees_bps,
            net_edge_bps=net_bps,
            suggested_qty=quantity,
            reference_price=price,
        )

    # ── Helpers ──────────────────────────────────────────────────

    @staticmethod
    def _is_stale(funding: dict) -> bool:
        ts = funding.get("timestamp")
        if ts is None:
            return False                 # some exchanges don't provide it
        age = time.time() * 1000 - ts    # ccxt timestamps are in ms
        return age > _FUNDING_STALE_SEC * 1000

    @staticmethod
    def _detect_interval(funding: dict) -> int:
        """Guess funding interval from next-funding timestamp."""
        ts = funding.get("timestamp")
        nxt = funding.get("next_timestamp")
        if ts and nxt and nxt > ts:
            gap_hours = (nxt - ts) / (3600 * 1000)
            if gap_hours <= 1.5:
                return 1
        return 8
