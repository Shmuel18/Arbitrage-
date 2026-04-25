"""
Trinity Bot - API Publisher
Publishes bot data to Redis for API consumption
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Dict, List

if TYPE_CHECKING:
    from src.notifications.telegram_notifier import TelegramNotifier
    from src.storage.redis_client import RedisClient

logger = logging.getLogger("trinity.publisher")


class APIPublisher:
    """Publishes bot data to Redis for web interface"""

    def __init__(
        self,
        redis_client: "RedisClient",
        telegram: "TelegramNotifier | None" = None,
    ) -> None:
        self.redis = redis_client
        self.start_time = datetime.now(timezone.utc)
        self._total_trades: int = 0
        self._winning_trades: int = 0
        self._total_realized_pnl: Decimal = Decimal("0")
        # Optional Telegram fan-out. When None, publish_alert is
        # indistinguishable from the pre-Telegram behavior.
        self._telegram = telegram
    
    async def publish_status(self, running: bool, exchanges: List[str], positions_count: int, min_funding_spread: float = 0.5):
        """Publish bot status"""
        status = {
            "bot_running": running,
            "connected_exchanges": exchanges,
            "active_positions": positions_count,
            "uptime": round((datetime.now(timezone.utc) - self.start_time).total_seconds() / 3600, 2),
            "min_funding_spread": min_funding_spread,
        }
        await self.redis.set("trinity:status", json.dumps(status), ex=15)
    
    async def publish_balances(self, balances: Dict[str, float]):
        """Publish exchange balances"""
        data = {
            "balances": balances,
            "total": sum(balances.values()),
            "updated_at": datetime.now(timezone.utc).isoformat()
        }
        await self.redis.set("trinity:balances", json.dumps(data))
    
    async def publish_opportunities(self, opportunities: List[Dict[str, Any]]):
        """Publish top opportunities from scanner"""
        data = {
            "opportunities": opportunities,
            "count": len(opportunities),
            "updated_at": datetime.now(timezone.utc).isoformat()
        }
        await self.redis.set("trinity:opportunities", json.dumps(data))
    
    async def publish_log(self, level: str, message: str) -> None:
        """Publish a log entry."""
        entry = json.dumps({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "message": message,
            "level": level
        })
        # Push to a list, keep last 200
        await self.redis.lpush("trinity:logs", entry)
        await self.redis.ltrim("trinity:logs", 0, 199)
    
    async def publish_summary(self, balances: Dict[str, float], positions_count: int) -> None:
        """Publish overall summary. total_pnl is accumulated realized PnL, not equity."""
        win_rate = (self._winning_trades / self._total_trades) if self._total_trades > 0 else 0
        uptime = round((datetime.now(timezone.utc) - self.start_time).total_seconds() / 3600, 2)
        
        summary = {
            "total_pnl": float(self._total_realized_pnl),
            "total_trades": self._total_trades,
            "win_rate": round(win_rate, 3),
            "active_positions": positions_count,
            "uptime_hours": uptime
        }
        await self.redis.set("trinity:summary", json.dumps(summary))
    
    def record_trade(self, is_win: bool, pnl: Decimal = Decimal("0")) -> None:
        """Record a trade result for win rate and PnL tracking.

        Also persists incremental counters to Redis so the broadcast
        loop can read them without recomputing from full history.
        """
        self._total_trades += 1
        self._total_realized_pnl += pnl
        if is_win:
            self._winning_trades += 1
        # Supervised fire-and-forget counter updates in Redis.
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # Called from sync/test context with no active event loop.
            logger.debug("Skipping Redis counter update: no running event loop")
            return

        task = loop.create_task(self._update_redis_counters(pnl, is_win))
        task.add_done_callback(self._counter_task_done)

    @staticmethod
    def _counter_task_done(t: asyncio.Task) -> None:
        """Log failures from fire-and-forget Redis counter updates."""
        if t.cancelled():
            return
        exc = t.exception()
        if exc:
            logger.debug("Redis counter update task failed: %s", exc)

    async def _update_redis_counters(self, pnl: Decimal, is_win: bool) -> None:
        """Atomically increment trade counters in Redis."""
        try:
            await asyncio.gather(
                self.redis.incr("trinity:stats:trade_count"),
                self.redis.incrbyfloat("trinity:stats:total_pnl", pnl),
                *(
                    [self.redis.incr("trinity:stats:win_count")]
                    if is_win
                    else []
                ),
                return_exceptions=True,
            )
        except Exception as exc:
            logger.debug("Failed to update Redis counters: %s", exc)
    
    async def publish_positions(self, positions: List[Dict[str, Any]]):
        """Publish active positions"""
        await self.redis.set("trinity:positions", json.dumps(positions))
    
    async def publish_trade(self, trade: Dict[str, Any]):
        """Publish completed trade to history"""
        timestamp = datetime.now(timezone.utc).timestamp()
        await self.redis.zadd(
            "trinity:trades:history",
            {json.dumps(trade): timestamp}
        )
    
    async def publish_exchanges(self, exchanges: List[Dict[str, Any]]):
        """Publish exchange statuses"""
        await self.redis.set("trinity:exchanges", json.dumps({"exchanges": exchanges}))

    async def push_alert(self, message: str) -> None:
        """Push an operator alert (backward-compat wrapper).

        Delegates to publish_alert() with severity="critical" so that all
        existing call-sites automatically populate trinity:alerts.
        """
        await self.publish_alert(message, severity="critical", alert_type="system")

    async def publish_alert(
        self,
        message: str,
        *,
        severity: str = "critical",
        alert_type: str = "system",
        symbol: str | None = None,
        exchange: str | None = None,
        payload: Dict[str, Any] | None = None,
    ) -> None:
        """Publish a structured alert to trinity:alerts (24 h TTL, max 200).

        Also writes a matching log entry so the signal tape is updated.

        Args:
            message:    Human-readable description of the event.
            severity:   One of "critical" | "warning" | "info".
            alert_type: Machine-readable category, e.g. "orphan", "trade_open",
                        "trade_close", "error_state", "system".
            symbol:     Optional trading symbol (e.g. "BTC/USDT:USDT").
            exchange:   Optional exchange name (e.g. "binance").
            payload:    Optional structured fields used by Telegram formatters
                        for trade_open / trade_close to render rich messages.
                        Must be JSON-serializable.
        """
        entry = {
            "id": str(uuid.uuid4()),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "severity": severity,
            "type": alert_type,
            "message": message,
            "symbol": symbol,
            "exchange": exchange,
            "payload": payload,
        }
        raw = json.dumps(entry)
        try:
            await self.redis.lpush("trinity:alerts", raw)
            await self.redis.ltrim("trinity:alerts", 0, 199)
            await self.redis.expire("trinity:alerts", 86400)  # 24 h TTL
        except Exception as e:
            logger.debug(f"publish_alert Redis write failed: {e}")
        # Also mirror to the signal tape so the log panel reflects the event.
        log_level = "CRITICAL" if severity == "critical" else "WARNING" if severity == "warning" else "INFO"
        await self.publish_log(log_level, message)

        # Fan out to Telegram — strictly fire-and-forget so a slow/failed
        # sendMessage never blocks the caller (which may be inside the
        # hot trading path). Errors are swallowed inside send_alert().
        if self._telegram is not None:
            try:
                asyncio.create_task(self._telegram.send_alert(entry))
            except RuntimeError:
                # No running loop (sync test context). Skip — Redis write
                # above is the durable record; Telegram is best-effort.
                pass

