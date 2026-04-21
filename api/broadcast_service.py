"""
BroadcastService — builds the full_update payload from Redis and broadcasts
it to all connected WebSocket clients every 2 seconds.

Extracted from api/main.py to keep that module under 300 lines and to
isolate the JSON-parsing / PnL-computation logic in a testable unit.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time as _time
from datetime import datetime, timezone
from typing import Any

from api.websocket_manager import ConnectionManager
from src.storage.redis_client import RedisClient

logger = logging.getLogger("trinity.api.broadcast")

_BROADCAST_INTERVAL_S = 2


def _build_heartbeat() -> str:
    """Build a heartbeat JSON payload."""
    return json.dumps(
        {
            "type": "heartbeat",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    )


class BroadcastService:
    """Reads Redis state and broadcasts full_update payloads over WS."""

    __slots__ = ("_manager", "_redis")

    def __init__(self, manager: ConnectionManager, redis: RedisClient) -> None:
        self._manager = manager
        self._redis = redis

    async def run_forever(self) -> None:
        """Run the broadcast cycle forever as a supervised background task."""
        while True:
            try:
                await self._broadcast_once()
            except Exception as exc:
                logger.error("Error in broadcast cycle: %s", exc)
                try:
                    if self._manager.active_connections:
                        await self._manager.broadcast(_build_heartbeat())
                except Exception as heartbeat_exc:
                    logger.debug("Heartbeat broadcast also failed: %s", heartbeat_exc)
            await asyncio.sleep(_BROADCAST_INTERVAL_S)

    async def _broadcast_once(self) -> None:
        if not self._manager.active_connections:
            return

        redis_client = self._redis

        results = await asyncio.gather(
            redis_client.get("trinity:status"),
            redis_client.get("trinity:positions"),
            redis_client.get("trinity:balances"),
            redis_client.get("trinity:opportunities"),
            redis_client.lrange("trinity:logs", 0, 19),
            redis_client.get("trinity:pnl:latest"),
            redis_client.get("trinity:summary"),
            redis_client.zrange("trinity:trades:history", 0, -1, withscores=True),
            redis_client.get("trinity:stats:trade_count"),
            redis_client.get("trinity:stats:total_pnl"),
            redis_client.get("trinity:stats:win_count"),
            return_exceptions=True,
        )

        (
            status_data,
            positions_data,
            balances_data,
            opportunities_data,
            logs_data,
            pnl_latest,
            summary_data,
            trades_history_raw,
            stats_trade_count,
            stats_total_pnl,
            stats_win_count,
        ) = [None if isinstance(result, Exception) else result for result in results]

        keys = (
            "status",
            "positions",
            "balances",
            "opportunities",
            "logs",
            "pnl_latest",
            "summary",
            "trades_history",
            "stats_trade_count",
            "stats_total_pnl",
            "stats_win_count",
        )
        for key, result in zip(keys, results):
            if isinstance(result, Exception):
                logger.warning("Redis read error for '%s': %s", key, result)

        all_trades = self._parse_trade_history(trades_history_raw)

        summary = self._build_summary(
            summary_data,
            all_trades,
            stats_trade_count,
            stats_total_pnl,
            stats_win_count,
        )
        pnl_struct = self._build_pnl(all_trades, pnl_latest)
        trades_list = self._build_trades_list(all_trades)

        positions_parsed: list[dict[str, Any]] | list[Any]
        positions_parsed = json.loads(positions_data) if positions_data else []
        if isinstance(positions_parsed, dict):
            positions_parsed = positions_parsed.get("positions", [])

        alerts_list: list[dict[str, Any]] = []
        try:
            raw_alerts = await redis_client.lrange("trinity:alerts", 0, 49)
            for item in raw_alerts or []:
                try:
                    alert = json.loads(item)
                    if isinstance(alert, dict):
                        alerts_list.append(alert)
                except (json.JSONDecodeError, TypeError):
                    # Ignore malformed alert entries; keep broadcast loop healthy.
                    continue
        except Exception as alerts_exc:
            logger.debug("Failed to read trinity:alerts: %s", alerts_exc)

        # When the bot key has expired from Redis (TTL=15s), send an explicit
        # "stopped" status so the frontend badge flips instead of staying stale.
        _status_payload: dict | None = None
        if status_data:
            _status_payload = json.loads(status_data)
        else:
            _status_payload = {
                "bot_running": False,
                "connected_exchanges": [],
                "active_positions": 0,
                "uptime": "—",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }

        update = {
            "type": "full_update",
            "schema_version": 1,
            "data": {
                "status": _status_payload,
                "positions": positions_parsed,
                "balances": json.loads(balances_data) if balances_data else None,
                "opportunities": json.loads(opportunities_data) if opportunities_data else None,
                "summary": summary,
                "pnl": pnl_struct,
                "logs": [json.loads(entry) for entry in logs_data] if logs_data else [],
                "trades": trades_list,
                "alerts": alerts_list,
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        await self._manager.broadcast(json.dumps(update))

    @staticmethod
    def _parse_trade_history(
        raw: list[tuple[str, float]] | None,
    ) -> list[tuple[dict[str, Any], float]]:
        if not raw:
            return []

        parsed: list[tuple[dict[str, Any], float]] = []
        for entry in raw:
            try:
                if not isinstance(entry, tuple):
                    continue
                raw_json, score = entry
                parsed.append((json.loads(raw_json), float(score)))
            except Exception as exc:
                logger.debug("Skipping malformed trade history entry: %s", exc)
        return parsed

    @staticmethod
    def _build_summary(
        summary_data: str | None,
        all_trades: list[tuple[dict[str, Any], float]],
        stats_trade_count: str | None,
        stats_total_pnl: str | None,
        stats_win_count: str | None,
    ) -> dict[str, Any]:
        base: dict[str, Any] = {
            "total_pnl": 0,
            "total_trades": 0,
            "win_rate": 0,
            "active_positions": 0,
            "uptime_hours": 0,
            "all_time_pnl": 0,
            "avg_pnl": 0,
        }

        try:
            if summary_data:
                base.update(json.loads(summary_data))
        except Exception as exc:
            logger.warning("Failed to parse summary_data from Redis: %s", exc)

        counter_trade_count = int(stats_trade_count) if stats_trade_count is not None else None
        history_len = len(all_trades)
        counter_is_stale = counter_trade_count is None or counter_trade_count < history_len

        if not counter_is_stale:
            trade_count = counter_trade_count
            all_time_pnl = float(stats_total_pnl or 0)
            winning = int(stats_win_count or 0)
        else:
            all_time_pnl = 0.0
            winning = 0
            for trade_data, _ in all_trades:
                pnl_value = float(trade_data.get("total_pnl", 0))
                all_time_pnl += pnl_value
                if pnl_value > 0:
                    winning += 1
            trade_count = history_len

        base["all_time_pnl"] = round(all_time_pnl, 4)
        base["avg_pnl"] = round(all_time_pnl / trade_count, 4) if trade_count > 0 else 0.0
        base["total_trades"] = trade_count
        base["win_rate"] = round(winning / trade_count, 3) if trade_count > 0 else 0.0
        return base

    @staticmethod
    def _build_pnl(
        all_trades: list[tuple[dict[str, Any], float]],
        pnl_latest: str | None,
    ) -> dict[str, Any] | None:
        try:
            cutoff = _time.time() - 86400
            data_points: list[dict[str, Any]] = []
            cumulative = 0.0

            for trade_data, timestamp in all_trades:
                if timestamp < cutoff:
                    continue
                pnl_value = float(trade_data.get("total_pnl") or trade_data.get("net_profit") or 0)
                cumulative += pnl_value
                data_points.append(
                    {
                        "pnl": pnl_value,
                        "cumulative_pnl": cumulative,
                        "timestamp": timestamp,
                        "symbol": trade_data.get("symbol", "?"),
                    }
                )

            unrealized = (
                float(json.loads(pnl_latest).get("unrealized_pnl", 0))
                if pnl_latest
                else 0.0
            )
            return {
                "data_points": data_points,
                "total_pnl": cumulative + unrealized,
                "realized_pnl": cumulative,
                "unrealized_pnl": unrealized,
            }
        except Exception as exc:
            logger.warning("PnL structure build error: %s", exc)
            return None

    @staticmethod
    def _build_trades_list(
        all_trades: list[tuple[dict[str, Any], float]],
    ) -> list[dict[str, Any]]:
        trades_list: list[dict[str, Any]] = []
        try:
            for trade_data, _ in reversed(all_trades[-20:]):
                invested = float(trade_data.get("invested") or 0)
                total_pnl = float(trade_data.get("total_pnl") or 0)
                pnl_pct = (total_pnl / invested) if invested > 0 else 0.0
                entry_edge = trade_data.get("entry_edge_pct")

                trades_list.append(
                    {
                        **trade_data,
                        "pnl": total_pnl,
                        "pnl_percentage": pnl_pct,
                        "open_time": trade_data.get("opened_at"),
                        "close_time": trade_data.get("closed_at"),
                        "exchanges": {
                            "long": trade_data.get("long_exchange"),
                            "short": trade_data.get("short_exchange"),
                        },
                        "size": f"${invested:,.0f}",
                        "entry_spread": (float(entry_edge) / 100) if entry_edge else None,
                        "entry_basis_pct": (
                            float(trade_data["entry_basis_pct"]) / 100
                            if trade_data.get("entry_basis_pct") is not None
                            else None
                        ),
                        "price_spread_pct": (
                            float(trade_data["price_spread_pct"]) / 100
                            if trade_data.get("price_spread_pct") is not None
                            else None
                        ),
                        "exit_spread": None,
                        "price_pnl": float(trade_data.get("price_pnl") or 0),
                        "funding_net": float(trade_data.get("funding_net") or 0),
                        "invested": invested,
                        "mode": trade_data.get("mode", "hold"),
                        "exit_reason": trade_data.get("exit_reason"),
                        "funding_collections": int(trade_data.get("funding_collections") or 0),
                        "funding_collected_usd": float(trade_data.get("funding_collected_usd") or 0),
                    }
                )
        except Exception as exc:
            logger.warning("Trades list build error: %s", exc)

        return trades_list
