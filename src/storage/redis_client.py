"""
Redis client — only the methods the bot actually calls.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import redis.asyncio as aioredis

from src.core.logging import get_logger

logger = get_logger("storage")


class RedisClient:
    def __init__(self, url: str = "redis://localhost:6379/0", prefix: str = "trinity:"):
        self._url = url
        self._prefix = prefix
        self._client: Optional[aioredis.Redis] = None

    def _key(self, *parts: str) -> str:
        return self._prefix + ":".join(parts)

    # ── Lifecycle ────────────────────────────────────────────────

    async def connect(self) -> None:
        self._client = aioredis.from_url(
            self._url, decode_responses=True, socket_timeout=5,
        )
        await self._client.ping()
        logger.info("Redis connected", extra={"action": "redis_connect"})

    async def disconnect(self) -> None:
        if self._client:
            await self._client.aclose()

    async def health_check(self) -> bool:
        try:
            return bool(await self._client.ping())
        except Exception:
            return False

    # ── Generic passthroughs (API publisher support) ────────────

    async def set(self, key: str, value: str, ex: Optional[int] = None) -> None:
        """Set a raw key/value (no prefix applied)."""
        await self._client.set(key, value, ex=ex)

    async def get(self, key: str) -> Optional[str]:
        """Get a raw key value (no prefix applied)."""
        val = await self._client.get(key)
        if val is None:
            return None
        return val if isinstance(val, str) else val.decode()

    async def zadd(self, key: str, mapping: Dict[str, float]) -> None:
        """Add to a sorted set (no prefix applied)."""
        await self._client.zadd(key, mapping)

    def pubsub(self):
        """Return a pubsub instance (no prefix applied)."""
        return self._client.pubsub()

    # ── Trade state (crash recovery) ─────────────────────────────

    async def set_trade_state(self, trade_id: str, state: Dict[str, Any]) -> None:
        key = self._key("trade", trade_id)
        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        await self._client.set(key, json.dumps(state, default=str), ex=86400 * 7)

    async def get_trade_state(self, trade_id: str) -> Optional[Dict[str, Any]]:
        raw = await self._client.get(self._key("trade", trade_id))
        return json.loads(raw) if raw else None

    async def get_all_trades(self) -> Dict[str, Dict[str, Any]]:
        """Return all active trade states (for crash recovery)."""
        pattern = self._key("trade", "*")
        result: Dict[str, Dict[str, Any]] = {}
        async for key in self._client.scan_iter(match=pattern, count=100):
            raw = await self._client.get(key)
            if raw:
                trade_id = key.replace(self._key("trade", ""), "")
                result[trade_id] = json.loads(raw)
        return result

    async def delete_trade_state(self, trade_id: str) -> None:
        await self._client.delete(self._key("trade", trade_id))

    # ── Exchange health ──────────────────────────────────────────

    async def set_exchange_health(self, exchange_id: str, data: Dict[str, Any]) -> None:
        key = self._key("health", exchange_id)
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        await self._client.set(key, json.dumps(data, default=str), ex=300)

    async def get_exchange_health(self, exchange_id: str) -> Optional[Dict[str, Any]]:
        raw = await self._client.get(self._key("health", exchange_id))
        return json.loads(raw) if raw else None

    # ── Position snapshot (for risk guard) ───────────────────────

    async def set_position_snapshot(self, exchange_id: str, positions: list) -> None:
        key = self._key("positions", exchange_id)
        await self._client.set(key, json.dumps(positions, default=str), ex=120)

    # ── Cooldown ─────────────────────────────────────────────────

    async def set_cooldown(self, symbol: str, seconds: int) -> None:
        key = self._key("cooldown", symbol)
        await self._client.set(key, "1", ex=seconds)
        logger.info(f"Cooldown set: {symbol} for {seconds}s",
                    extra={"symbol": symbol, "action": "cooldown_set"})

    async def is_cooled_down(self, symbol: str) -> bool:
        return bool(await self._client.exists(self._key("cooldown", symbol)))

    # ── Distributed lock ─────────────────────────────────────────

    async def acquire_lock(self, name: str, timeout: int = 10) -> bool:
        key = self._key("lock", name)
        return bool(await self._client.set(key, "1", nx=True, ex=timeout))

    async def release_lock(self, name: str) -> None:
        await self._client.delete(self._key("lock", name))
