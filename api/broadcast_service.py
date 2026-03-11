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
    return json.dumps({
        "type": "heartbeat",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


class BroadcastService:
    """Reads Redis state and broadcasts full_update payloads over WS."""

    __slots__ = ("_manager", "_redis")

    def __init__(self, manager: ConnectionManager, redis: RedisClient) -> None:
        self._manager = manager
        self._redis = redis

    # ── Public entry point ─────────────────────────────────────

    async def run_forever(self) -> None:
        """Loop that runs as a supervised background task."""
        while True:
            try:
                await self._broadcast_once()
            except Exception as exc:
                logger.error("Error in broadcast cycle: %s", exc)
                # Even on error — heartbeat so WS AGE stays green.
                try:
                    if self._manager.active_connections:
                        await self._manager.broadcast(_build_heartbeat())
                except Exception:
                    pass
            await asyncio.sleep(_BROADCAST_INTERVAL_S)

    # ── Single broadcast cycle ─────────────────────────────────

    async def _broadcast_once(self) -> None:
        if not self._manager.active_connections:
            return

        rc = self._redis

        # Parallel Redis reads — return_exceptions prevents a single
        # Redis hiccup from crashing the entire broadcast cycle.
        results = await asyncio.gather(
            rc.get("trinity:status"),
            rc.get("trinity:positions"),
            rc.get("trinity:balances"),
            rc.get("trinity:opportunities"),
            rc.lrange("trinity:logs", 0, 19),
            rc.get("trinity:pnl:latest"),
            rc.get("trinity:summary"),
            rc.zrange("trinity:trades:history", 0, -1, withscores=True),
            rc.get("trinity:stats:trade_count"),
            rc.get("trinity:stats:total_pnl"),
            rc.get("trinity:stats:win_count"),
            return_exceptions=True,
        )
        (
            status_data, positions_data, balances_data,
            opportunities_data, logs_data, pnl_latest,
            summary_data, trades_history_raw,
            stats_trade_count, stats_total_pnl, stats_win_count,
        ) = [None if isinstance(r, Exception) else r for r in results]

        # Log any gather errors so operators notice Redis issues.
        _keys = (
            "status", "positions", "balances", "opportunities", "logs",
            "pnl_latest", "summary", "trades_history",
            "stats_trade_count", "stats_total_pnl", "stats_win_count",
        )
        for key, res in zip(_keys, results):
            if isinstance(res, Exception):
                logger.warning("Redis read error for '%s': %s", key, res)

        # Parse trade history ONCE — reuse for summary, pnl, trades_list.
        all_trades = self._parse_trade_history(trades_history_raw)

        summary = self._build_summary(
            summary_data, all_trades,
            stats_trade_count, stats_total_pnl, stats_win_count,
        )

        pnl_struct = self._build_pnl(all_trades, pnl_latest)
        trades_list = self._build_trades_list(all_trades)

        # Normalize positions to always be a flat list.
        positions_parsed = json.loads(positions_data) if positions_data else []
        if isinstance(positions_parsed, dict):
            positions_parsed = positions_parsed.get("positions", [])

        update = {
            "type": "full_update",
            "schema_version": 1,
            "data": {
                "status": json.loads(status_data) if status_data else None,
                "positions": positions_parsed,
                "balances": json.loads(balances_data) if balances_data else None,
                "opportunities": json.loads(opportunities_data) if opportunities_data else None,
                "summary": summary,
                "pnl": pnl_struct,
                "logs": [json.loads(entry) for entry in logs_data] if logs_data else [],
                "trades": trades_list,
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        await self._manager.broadcast(json.dumps(update))

    # ── Helpers ────────────────────────────────────────────────

    @staticmethod
    def _parse_trade_history(
        raw: list[tuple[str, float]] | None,
    ) -> list[tuple[dict[str, Any], float]]:
        if not raw:
            return []
        parsed: list[tuple[dict[str, Any], float]] = []
        for entry in raw:
            try:
                if isinstance(entry, tuple):
                    raw_json, score = entry
                else:
                    continue
                parsed.append((json.loads(raw_json), float(score)))
            except Exception:
                pass
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
            "total_pnl": 0, "total_trades": 0, "win_rate": 0,
            "active_positions": 0, "uptime_hours": 0,
            "all_time_pnl": 0, "avg_pnl": 0,
        }
        try:
            if summary_data:
                base.update(json.loads(summary_data))
        except Exception:
            pass

        # Prefer incremental Redis counters (O(1)), but only when they are in
        # sync with the authoritative history (sorted-set).  If the counter is
        # lower than the actual history length it means the bot restarted and
        # the counters were reset — in that case ALL three counter values
        # (trade_count, total_pnl, win_count) are stale, so we must do a full
        # scan of the history to get consistent numbers.
        counter_trade_count = int(stats_trade_count) if stats_trade_count is not None else None
        history_len = len(all_trades)
        counter_is_stale = counter_trade_count is None or counter_trade_count < history_len

        if not counter_is_stale:
            # Counters are fresh — cheap O(1) path.
            trade_count = counter_trade_count  # type: ignore[assignment]
            all_time_pnl = float(stats_total_pnl or 0)
            winning = int(stats_win_count or 0)
        else:
            # Counters were reset (restart) — recompute from actual history.
            all_time_pnl = 0.0
            winning = 0
            for td, _ in all_trades:
                pnl_v = float(td.get("total_pnl", 0))
                all_time_pnl += pnl_v
                if pnl_v > 0:
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
            dp: list[dict[str, Any]] = []
            cumulative = 0.0
            for td, ts in all_trades:
                if ts < cutoff:
                    continue
                pnl_val = float(td.get("total_pnl") or td.get("net_profit") or 0)
                cumulative += pnl_val
                dp.append({
                    "pnl": pnl_val,
                    "cumulative_pnl": cumulative,
                    "timestamp": ts,
                    "symbol": td.get("symbol", "?"),
                })
            unrealized = (
                float(json.loads(pnl_latest).get("unrealized_pnl", 0))
                if pnl_latest
                else 0.0
            )
            return {
                "data_points": dp,
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
            for td, _ in reversed(all_trades[-20:]):
                invested = float(td.get("invested") or 0)
                total_pnl_t = float(td.get("total_pnl") or 0)
                pnl_pct = (total_pnl_t / invested) if invested > 0 else 0.0
                entry_edge = td.get("entry_edge_pct")
                trades_list.append({
                    **td,
                    "pnl": total_pnl_t,
                    "pnl_percentage": pnl_pct,
                    "open_time": td.get("opened_at"),
                    "close_time": td.get("closed_at"),
                    "exchanges": {
                        "long": td.get("long_exchange"),
                        "short": td.get("short_exchange"),
                    },
                    "size": f"${invested:,.0f}",
                    "entry_spread": (
                        float(entry_edge) / 100 if entry_edge else None
                    ),
                    "entry_basis_pct": (
                        float(td["entry_basis_pct"]) / 100
                        if td.get("entry_basis_pct") is not None
                        else None
                    ),
                    "exit_spread": None,
                    "price_pnl": float(td.get("price_pnl") or 0),
                    "funding_net": float(td.get("funding_net") or 0),
                    "invested": invested,
                    "mode": td.get("mode", "hold"),
                    "exit_reason": td.get("exit_reason"),
                    "funding_collections": int(td.get("funding_collections") or 0),
                    "funding_collected_usd": float(td.get("funding_collected_usd") or 0),
                })
        except Exception as exc:
            logger.warning("Trades list build error: %s", exc)
        return trades_list
