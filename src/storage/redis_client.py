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
    def __init__(
        self,
        url: str = "redis://localhost:6379/0",
        prefix: str = "trinity:",
        password: Optional[str] = None,
        tls: bool = False,
    ):
        self._url = url
        self._prefix = prefix
        self._password = password
        self._tls = tls
        self._client: Optional[aioredis.Redis] = None

    def _key(self, *parts: str) -> str:
        return self._prefix + ":".join(parts)

    @property
    def _c(self) -> aioredis.Redis:
        """Return the underlying aioredis client, or raise a clear error if not connected.

        Surfaces a ``ConnectionError`` with an actionable message instead of the
        cryptic ``AttributeError: 'NoneType' object has no attribute 'set'`` that
        would otherwise propagate when ``connect()`` was never called or failed.
        """
        if self._client is None:
            raise ConnectionError(
                "RedisClient is not connected — call await connect() first"
            )
        return self._client

    # ── Lifecycle ────────────────────────────────────────────

    async def connect(self) -> None:
        kwargs: dict[str, Any] = {
            "password": self._password,
            "decode_responses": True,
            "socket_timeout": 5,
        }
        if self._tls:
            kwargs["ssl"] = True
        self._client = aioredis.from_url(self._url, **kwargs)
        await self._client.ping()
        tls_str = " (TLS)" if self._tls else ""
        logger.info(f"Redis connected{tls_str}", extra={"action": "redis_connect"})

    async def disconnect(self) -> None:
        if self._client:
            await self._client.aclose()

    async def health_check(self) -> bool:
        try:
            return bool(await self._c.ping())
        except Exception as exc:
            # Health check failures are expected during reconnects;
            # callers retry automatically.
            logger.debug(f"Redis health check failed: {exc}")
            return False

    # ── Generic passthroughs (API publisher support) ────────────

    async def set(self, key: str, value: str, ex: Optional[int] = None) -> None:
        """Set a raw key/value (no prefix applied)."""
        await self._c.set(key, value, ex=ex)

    async def get(self, key: str) -> Optional[str]:
        """Get a raw key value (no prefix applied)."""
        val = await self._c.get(key)
        if val is None:
            return None
        return val if isinstance(val, str) else val.decode()

    async def zadd(self, key: str, mapping: Dict[str, float]) -> int:
        """Add to a sorted set (no prefix applied). Returns number of elements added."""
        return await self._c.zadd(key, mapping)

    async def incr(self, key: str) -> int:
        """Atomically increment an integer key by 1. Returns new value."""
        return await self._c.incr(key)

    async def incrbyfloat(self, key: str, amount: float) -> float:
        """Atomically increment a key by a float amount. Returns new value."""
        return await self._c.incrbyfloat(key, amount)

    async def zrangebyscore(
        self, key: str, min_score: float, max_score: float,
        *, withscores: bool = False,
    ) -> list[Any]:
        """Read members from a sorted set by score range (no prefix applied)."""
        return await self._c.zrangebyscore(
            key, min_score, max_score, withscores=withscores,
        )

    async def zremrangebyscore(
        self, key: str, min_score: float, max_score: float,
    ) -> int:
        """Remove members from a sorted set by score range (no prefix applied)."""
        return await self._c.zremrangebyscore(key, min_score, max_score)

    async def lpush(self, key: str, *values: str) -> int:
        """Prepend values to a list (no prefix applied)."""
        return await self._c.lpush(key, *values)

    async def lrange(self, key: str, start: int, stop: int) -> list[Any]:
        """Return a range of elements from a list (no prefix applied)."""
        return await self._c.lrange(key, start, stop)

    async def ltrim(self, key: str, start: int, stop: int) -> None:
        """Trim a list to the specified range (no prefix applied)."""
        await self._c.ltrim(key, start, stop)

    async def zrange(
        self, key: str, start: int, stop: int,
        *, withscores: bool = False,
    ) -> list[Any]:
        """Return a range of elements from a sorted set (no prefix applied)."""
        return await self._c.zrange(key, start, stop, withscores=withscores)

    async def publish(self, channel: str, message: str) -> int:
        """Publish a message to a Redis channel (no prefix applied)."""
        return await self._c.publish(channel, message)

    def pubsub(self) -> Any:
        """Return a pubsub instance (no prefix applied)."""
        return self._c.pubsub()

    # ── Trade state (crash recovery) ─────────────────────────────

    async def set_trade_state(self, trade_id: str, state: Dict[str, Any]) -> None:
        key = self._key("trade", trade_id)
        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        await self._c.set(key, json.dumps(state, default=str), ex=86400 * 7)

    async def get_trade_state(self, trade_id: str) -> Optional[Dict[str, Any]]:
        raw = await self._c.get(self._key("trade", trade_id))
        return json.loads(raw) if raw else None

    async def get_all_trades(self) -> Dict[str, Dict[str, Any]]:
        """Return all active trade states (for crash recovery)."""
        pattern = self._key("trade", "*")
        result: Dict[str, Dict[str, Any]] = {}
        async for key in self._c.scan_iter(match=pattern, count=100):
            raw = await self._c.get(key)
            if raw:
                trade_id = key.replace(self._key("trade", ""), "")
                result[trade_id] = json.loads(raw)
        return result

    async def delete_trade_state(self, trade_id: str) -> None:
        await self._c.delete(self._key("trade", trade_id))

    # ── Exchange health ──────────────────────────────────────────

    async def set_exchange_health(self, exchange_id: str, data: Dict[str, Any]) -> None:
        key = self._key("health", exchange_id)
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        await self._c.set(key, json.dumps(data, default=str), ex=300)

    async def get_exchange_health(self, exchange_id: str) -> Optional[Dict[str, Any]]:
        raw = await self._c.get(self._key("health", exchange_id))
        return json.loads(raw) if raw else None

    # ── Position snapshot (for risk guard) ───────────────────────

    async def set_position_snapshot(self, exchange_id: str, positions: list) -> None:
        key = self._key("positions", exchange_id)
        await self._c.set(key, json.dumps(positions, default=str), ex=120)

    # ── Cooldown ─────────────────────────────────────────────────

    async def set_cooldown(self, symbol: str, seconds: int) -> None:
        key = self._key("cooldown", symbol)
        await self._c.set(key, "1", ex=seconds)
        logger.info(f"Cooldown set: {symbol} for {seconds}s",
                    extra={"symbol": symbol, "action": "cooldown_set"})

    async def is_cooled_down(self, symbol: str) -> bool:
        return bool(await self._c.exists(self._key("cooldown", symbol)))

    async def get_cooled_down_symbols(self, symbols: list[str]) -> set[str]:
        """Batch check: return the subset of *symbols* currently in cooldown.

        Uses a Redis pipeline (one round-trip) instead of N individual EXISTS calls.
        """
        if not symbols:
            return set()
        pipe = self._c.pipeline()
        for s in symbols:
            pipe.exists(self._key("cooldown", s))
        results = await pipe.execute()
        return {s for s, exists in zip(symbols, results) if exists}

    # ── Distributed lock ─────────────────────────────────────────

    async def acquire_lock(self, name: str, timeout: int = 10) -> bool:
        key = self._key("lock", name)
        return bool(await self._c.set(key, "1", nx=True, ex=timeout))

    async def release_lock(self, name: str) -> None:
        await self._c.delete(self._key("lock", name))
