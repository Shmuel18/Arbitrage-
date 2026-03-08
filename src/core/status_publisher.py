"""
StatusPublisher — extracted from main.py's nested ``publish_status_loop``.

Publishes bot status, balances, positions, and running PnL to Redis
every ~5 seconds so the web-interface (HTTP + WS) can display them.

Extracted into its own class so it can be:
  • unit-tested in isolation
  • injected/mocked easily
  • replaced without touching the main entry-point
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Dict, List

from src.core.logging import get_logger
from src.discovery.calculator import calculate_funding_spread

if TYPE_CHECKING:
    from src.core.config import Config
    from src.exchanges.adapter import ExchangeManager
    from src.execution.controller import ExecutionController
    from src.storage.redis_client import RedisClient
    from src.api.publisher import APIPublisher

logger = get_logger("status")


class StatusPublisher:
    """Periodically publish bot status + balances + PnL to Redis."""

    _CYCLE_SECONDS = 5

    def __init__(
        self,
        cfg: "Config",
        exchange_mgr: "ExchangeManager",
        controller: "ExecutionController",
        redis: "RedisClient",
        publisher: "APIPublisher",
        shutdown_event: asyncio.Event,
    ) -> None:
        self._cfg = cfg
        self._mgr = exchange_mgr
        self._controller = controller
        self._redis = redis
        self._publisher = publisher
        self._shutdown = shutdown_event

    # ── Public entry-point ───────────────────────────────────────

    async def run(self) -> None:
        """Main loop — call via ``asyncio.create_task(sp.run())``."""
        while not self._shutdown.is_set():
            try:
                await self._publish_cycle()
                await asyncio.sleep(self._CYCLE_SECONDS)
            except Exception as exc:
                logger.error(f"Error publishing status: {exc}")
                await asyncio.sleep(1)

    # ── Single cycle ─────────────────────────────────────────────

    async def _publish_cycle(self) -> None:
        ts_now = datetime.now(timezone.utc).timestamp()
        active_count = len(self._controller._active_trades)

        # 1. Bot status
        await self._publisher.publish_status(
            running=self._controller._running,
            exchanges=self._cfg.enabled_exchanges,
            positions_count=active_count,
            min_funding_spread=float(self._cfg.trading_params.min_funding_spread),
        )

        # 2. Balances (parallel fetch)
        balances = await self._fetch_balances()
        await self._publisher.publish_balances(balances)
        await self._publisher.publish_summary(balances, active_count)

        # 3. Active positions with live spread & PnL
        active_snapshot = list(self._controller._active_trades.items())
        ticker_cache, position_cache = await self._prefetch_market_data(active_snapshot)
        positions_data = self._build_positions(active_snapshot, ticker_cache, position_cache)
        await self._publisher.publish_positions(positions_data)

        # 4. Running PnL
        await self._publish_pnl(active_snapshot, position_cache, ts_now)

    # ── Balance fetcher ──────────────────────────────────────────

    _last_good_balances: Dict[str, float] = {}  # cache last-known-good per exchange

    async def _fetch_balances(self) -> Dict[str, float]:
        async def _one(eid: str) -> tuple[str, float]:
            adapter = self._mgr.get(eid)
            if not adapter:
                return eid, self._last_good_balances.get(eid, 0.0)
            try:
                bal = await adapter.get_balance()
                total_val = bal.get("total")
                if isinstance(total_val, dict):
                    total_val = total_val.get("USDT")
                if total_val is None:
                    total_val = bal.get("free", 0)
                value = float(total_val or 0)
                if value > 0:
                    self._last_good_balances[eid] = value
                return eid, value
            except Exception as exc:
                logger.debug(f"Balance fetch failed for {eid}: {exc}")
                # Fall back to last known good balance instead of 0
                cached = self._last_good_balances.get(eid, 0.0)
                if cached > 0:
                    logger.debug(f"Using cached balance for {eid}: {cached:.2f}")
                return eid, cached

        results = await asyncio.gather(
            *[_one(eid) for eid in self._cfg.enabled_exchanges],
            return_exceptions=True,
        )
        balances: Dict[str, float] = {}
        for res in results:
            if isinstance(res, Exception):
                continue
            eid, val = res
            balances[eid] = val
        return balances

    # ── Pre-fetch tickers & positions in parallel ────────────────

    async def _prefetch_market_data(
        self,
        snapshot: List[tuple[str, Any]],
    ) -> tuple[Dict[tuple[str, str], dict], Dict[tuple[str, str], list]]:
        ticker_cache: Dict[tuple[str, str], dict] = {}
        position_cache: Dict[tuple[str, str], list] = {}
        if not snapshot:
            return ticker_cache, position_cache

        async def _ft(eid: str, sym: str) -> tuple[tuple[str, str], dict]:
            ad = self._mgr.get(eid)
            return (eid, sym), await ad.get_ticker(sym)

        async def _fp(eid: str, sym: str) -> tuple[tuple[str, str], list]:
            ad = self._mgr.get(eid)
            return (eid, sym), await ad.get_positions(sym)

        keys: set[tuple[str, str]] = set()
        for _tid, tr in snapshot:
            keys.add((tr.long_exchange, tr.symbol))
            keys.add((tr.short_exchange, tr.symbol))

        fetches = [_ft(e, s) for e, s in keys] + [_fp(e, s) for e, s in keys]
        results = await asyncio.gather(*fetches, return_exceptions=True)
        for res in results:
            if isinstance(res, Exception):
                continue
            key, val = res
            if isinstance(val, dict):
                ticker_cache[key] = val
            elif isinstance(val, list):
                position_cache[key] = val
        return ticker_cache, position_cache

    # ── Build position entries ───────────────────────────────────

    def _build_positions(
        self,
        snapshot: List[tuple[str, Any]],
        ticker_cache: Dict[tuple[str, str], dict],
        position_cache: Dict[tuple[str, str], list],
    ) -> List[Dict[str, Any]]:
        positions_data: List[Dict[str, Any]] = []
        for tid, trade in snapshot:
            entry = self._build_one_position(trade, ticker_cache, position_cache)
            positions_data.append(entry)
        return positions_data

    def _build_one_position(
        self,
        trade: Any,
        ticker_cache: Dict[tuple[str, str], dict],
        position_cache: Dict[tuple[str, str], list],
    ) -> Dict[str, Any]:
        pos_entry: Dict[str, Any] = {
            "id": trade.trade_id,
            "symbol": trade.symbol,
            "long_exchange": trade.long_exchange,
            "short_exchange": trade.short_exchange,
            "long_qty": str(trade.long_qty),
            "short_qty": str(trade.short_qty),
            "entry_edge_pct": str(trade.entry_edge_pct),
            "long_funding_rate": str(trade.long_funding_rate) if trade.long_funding_rate is not None else None,
            "short_funding_rate": str(trade.short_funding_rate) if trade.short_funding_rate is not None else None,
            "mode": trade.mode,
            "opened_at": trade.opened_at.isoformat() if trade.opened_at else None,
            "state": trade.state.value,
            "immediate_spread_pct": None,
            "current_spread_pct": None,
            "current_long_rate": None,
            "current_short_rate": None,
            "entry_price_long": str(trade.entry_price_long) if trade.entry_price_long is not None else None,
            "entry_price_short": str(trade.entry_price_short) if trade.entry_price_short is not None else None,
            "next_funding_ms": None,
            "entry_tier": trade.entry_tier,
        }

        # Live funding spread
        self._enrich_funding_spread(pos_entry, trade)

        # Unrealized PnL from prices
        self._enrich_price_pnl(pos_entry, trade, ticker_cache)

        # Static trade fields
        pos_entry["entry_basis_pct"] = str(trade.entry_basis_pct) if trade.entry_basis_pct is not None else None
        pos_entry["price_spread_pct"] = str(trade.price_spread_pct) if trade.price_spread_pct is not None else None
        pos_entry["funding_collected_usd"] = str(trade.funding_collected_usd)
        pos_entry["fees_paid_total"] = str(trade.fees_paid_total) if trade.fees_paid_total is not None else None
        pos_entry["funding_collections"] = trade.funding_collections
        pos_entry["profit_target_pct"] = str(self._cfg.trading_params.profit_target_pct)

        return pos_entry

    def _enrich_funding_spread(self, pos_entry: Dict[str, Any], trade: Any) -> None:
        try:
            long_ad = self._mgr.get(trade.long_exchange)
            short_ad = self._mgr.get(trade.short_exchange)
            live_long = long_ad.get_funding_rate_cached(trade.symbol)
            live_short = short_ad.get_funding_rate_cached(trade.symbol)
            if not live_long or not live_short:
                raise ValueError("no cached rate")
            spread_info = calculate_funding_spread(
                live_long["rate"], live_short["rate"],
                long_interval_hours=live_long.get("interval_hours", 8),
                short_interval_hours=live_short.get("interval_hours", 8),
            )
            pos_entry["immediate_spread_pct"] = str(spread_info["immediate_spread_pct"])
            pos_entry["current_spread_pct"] = str(spread_info["funding_spread_pct"])
            pos_entry["current_long_rate"] = str(live_long["rate"])
            pos_entry["current_short_rate"] = str(live_short["rate"])
            if live_long.get("next_timestamp"):
                pos_entry["next_funding_ms"] = live_long["next_timestamp"]
            # Pending funding estimate
            try:
                _lr = float(live_long["rate"])
                _sr = float(live_short["rate"])
                _notional = float(trade.entry_price_long or 0) * float(trade.long_qty or 0)
                if _notional > 0:
                    _income = _notional * max(0.0, -_lr) + _notional * max(0.0, _sr)
                    _cost = _notional * max(0.0, _lr) + _notional * max(0.0, -_sr)
                    pos_entry["pending_income_usd"] = str(round(_income, 4))
                    pos_entry["pending_income_pct"] = str(round(_income / _notional * 100, 4))
                    pos_entry["pending_net_usd"] = str(round(_income - _cost, 4))
                    pos_entry["pending_net_pct"] = str(round((_income - _cost) / _notional * 100, 4))
            except Exception as exc:
                logger.debug(f"Pending funding calc failed for {trade.symbol}: {exc}")
        except Exception as exc:
            logger.debug(f"Live spread fetch failed for {trade.symbol}: {exc}")

    def _enrich_price_pnl(
        self,
        pos_entry: Dict[str, Any],
        trade: Any,
        ticker_cache: Dict[tuple[str, str], dict],
    ) -> None:
        try:
            _lt = ticker_cache.get((trade.long_exchange, trade.symbol), {})
            _st = ticker_cache.get((trade.short_exchange, trade.symbol), {})
            _lp = float(_lt.get("last") or _lt.get("close") or 0)
            _sp = float(_st.get("last") or _st.get("close") or 0)
            _elp = float(trade.entry_price_long or 0)
            _esp = float(trade.entry_price_short or 0)
            if _lp > 0 and _sp > 0 and _elp > 0 and _esp > 0:
                _notional = _elp * float(trade.long_qty)
                if _notional > 0:
                    _long_pnl = (_lp - _elp) * float(trade.long_qty)
                    _short_pnl = (_esp - _sp) * float(trade.short_qty)
                    _price_pnl = _long_pnl + _short_pnl
                    _fund_pnl = float(trade.funding_collected_usd or 0)
                    _fees = float(trade.fees_paid_total or 0)
                    _total_pnl_pct = (_price_pnl + _fund_pnl - _fees) / _notional * 100
                    pos_entry["unrealized_pnl_pct"] = str(round(_total_pnl_pct, 4))
                    pos_entry["price_pnl_pct"] = str(round(_price_pnl / _notional * 100, 4))
                    pos_entry["funding_pnl_pct"] = str(round(_fund_pnl / _notional * 100, 4))
                    pos_entry["fees_pct"] = str(round(_fees / _notional * 100, 4))
            pos_entry["live_price_long"] = str(_lp) if _lp > 0 else None
            pos_entry["live_price_short"] = str(_sp) if _sp > 0 else None
            if _lp > 0 and _sp > 0:
                pos_entry["current_basis_pct"] = str(round((_lp - _sp) / _sp * 100, 4))
        except Exception as exc:
            logger.debug(f"Price PnL calc failed for {trade.symbol}: {exc}")

    # ── PnL aggregation & publish ────────────────────────────────

    async def _publish_pnl(
        self,
        snapshot: List[tuple[str, Any]],
        position_cache: Dict[tuple[str, str], list],
        ts_now: float,
    ) -> None:
        # Unrealized from open positions
        unrealized_pnl = 0.0
        for _tid, trade in snapshot:
            try:
                for pos in position_cache.get((trade.long_exchange, trade.symbol), []):
                    unrealized_pnl += float(pos.unrealized_pnl)
                for pos in position_cache.get((trade.short_exchange, trade.symbol), []):
                    unrealized_pnl += float(pos.unrealized_pnl)
            except Exception as exc:
                logger.debug(f"Unrealized PnL read failed for {trade.symbol}: {exc}")

        # Realized from closed trades (last 24h)
        realized_pnl = await self._read_realized_pnl()

        running_pnl = realized_pnl + unrealized_pnl

        # Write running PnL snapshot for chart
        try:
            pnl_snapshot = json.dumps({
                "running": running_pnl,
                "unrealized": unrealized_pnl,
                "realized": realized_pnl,
            })
            await self._redis.zadd("trinity:pnl:running", {pnl_snapshot: ts_now})
            cutoff_trim = ts_now - 86400
            await self._redis.zremrangebyscore("trinity:pnl:running", 0, cutoff_trim)
        except Exception as exc:
            logger.debug(f"PnL snapshot write failed: {exc}")

        # Publish PnL payload for frontend
        try:
            cutoff_24h = ts_now - 86400
            running_data = await self._redis.zrangebyscore(
                "trinity:pnl:running", cutoff_24h, float("inf"), withscores=True,
            )
            data_points: List[Dict[str, Any]] = []
            if running_data:
                for member, score in running_data:
                    try:
                        point = json.loads(member)
                        data_points.append({
                            "pnl": point.get("running", 0),
                            "cumulative_pnl": point.get("running", 0),
                            "unrealized": point.get("unrealized", 0),
                            "realized": point.get("realized", 0),
                            "timestamp": float(score),
                        })
                    except Exception as exc:
                        logger.debug(f"PnL data-point parse failed: {exc}")
            pnl_payload = {
                "data_points": data_points,
                "total_pnl": running_pnl,
                "unrealized_pnl": unrealized_pnl,
                "realized_pnl": realized_pnl,
                "count": len(data_points),
            }
            await self._redis.set("trinity:pnl:latest", json.dumps(pnl_payload))
        except Exception as exc:
            logger.debug(f"PnL publish error: {exc}")

    async def _read_realized_pnl(self) -> float:
        try:
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).timestamp()
            closed_pnl = await self._redis.zrangebyscore(
                "trinity:pnl:timeseries", cutoff, float("inf"), withscores=True,
            )
            total = 0.0
            if closed_pnl:
                for member, _score in closed_pnl:
                    try:
                        data = json.loads(member)
                        total += float(data["pnl"])
                    except (json.JSONDecodeError, KeyError, TypeError):
                        total += float(member)
            return total
        except Exception as exc:
            logger.debug(f"Realized PnL read failed: {exc}")
            return 0.0
