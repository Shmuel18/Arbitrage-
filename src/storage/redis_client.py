"""
Redis client — only the methods the bot actually calls.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
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

    async def expire(self, key: str, seconds: int) -> bool:
        """Set a TTL (seconds) on a raw key. Returns True if timeout was set."""
        return bool(await self._c.expire(key, seconds))

    async def incrbyfloat(self, key: str, amount: Decimal) -> Decimal:
        """Atomically increment a key by a Decimal amount. Returns new value.

        The amount is passed to Redis as a string to avoid float precision loss
        at the boundary.  The result is converted back via ``str()`` →
        ``Decimal`` for the same reason, preventing IEEE 754 rounding errors
        from accumulating in financial counters.
        """
        raw = await self._c.incrbyfloat(key, str(amount))
        # aioredis returns float; re-parse via str to preserve precision.
        return Decimal(str(raw))

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

    async def get_alerts(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return recent structured alerts from trinity:alerts (newest first)."""
        safe_limit = max(1, min(limit, 200))
        raw_items = await self._c.lrange("trinity:alerts", 0, safe_limit - 1)
        alerts: list[dict[str, Any]] = []
        for item in raw_items:
            try:
                alerts.append(json.loads(item))
            except (json.JSONDecodeError, TypeError) as exc:
                logger.debug(f"Skipping malformed alert JSON: {exc}")
        return alerts

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
        """Legacy lock — no ownership token; kept for backward compatibility."""
        key = self._key("lock", name)
        return bool(await self._c.set(key, "1", nx=True, ex=timeout))

    async def release_lock(self, name: str) -> None:
        """Legacy release — no ownership check; kept for backward compatibility."""
        await self._c.delete(self._key("lock", name))

    async def acquire_lock_with_token(self, name: str, token: str, ttl: int = 60) -> bool:
        """Acquire a distributed lock with a caller-owned token.

        Uses the token as the Redis value so only the holder can release or
        renew it.  TTL of 60 s covers the full entry path; the heartbeat task
        renews it every 15 s to handle slow exchanges and partial fills.

        Returns True if acquired, False if the key already exists (another
        instance or a leftover from a previous crash holds the lock).
        """
        key = self._key("lock", name)
        return bool(await self._c.set(key, token, nx=True, ex=ttl))

    async def release_lock_if_owner(self, name: str, token: str) -> bool:
        """Release the lock only if the stored token matches ours.

        Plain DEL would silently release a lock acquired by a different instance
        after our TTL expired — this atomic Lua compare-and-delete prevents that.

        Returns True if released, False if not the owner (already expired or
        stolen by another instance).
        """
        key = self._key("lock", name)
        # Atomic: read token, compare, delete if match — all in one round-trip.
        script = (
            "if redis.call('get', KEYS[1]) == ARGV[1] then "
            "  return redis.call('del', KEYS[1]) "
            "else "
            "  return 0 "
            "end"
        )
        result = await self._c.eval(script, 1, key, token)
        return bool(result)

    async def extend_lock(self, name: str, token: str, ttl: int = 60) -> bool:
        """Renew the lock TTL only if we still own it.

        Called by the lock heartbeat task every 15 s.  If the lock was lost
        (expired or stolen) this returns False and the heartbeat shuts down,
        giving the caller a chance to log and handle the race condition.
        """
        key = self._key("lock", name)
        script = (
            "if redis.call('get', KEYS[1]) == ARGV[1] then "
            "  return redis.call('expire', KEYS[1], ARGV[2]) "
            "else "
            "  return 0 "
            "end"
        )
        result = await self._c.eval(script, 1, key, token, str(ttl))
        return bool(result)

    # ── Route-level cooldown ──────────────────────────────────────
    # Finer-grained than symbol-only cooldown: a failed entry on
    # exchange_a → exchange_b should not suppress exchange_c → exchange_d
    # for the same symbol.  Route key: "symbol|long_exchange|short_exchange".

    async def set_route_cooldown(
        self,
        symbol: str,
        long_exchange: str,
        short_exchange: str,
        seconds: int,
        reason: str = "",
    ) -> None:
        """Set a per-route (symbol|long|short) cooldown."""
        route_key = f"{symbol}|{long_exchange}|{short_exchange}"
        key = self._key("cooldown_route", route_key)
        await self._c.set(key, reason or "1", ex=seconds)
        logger.info(
            f"Route cooldown set: {route_key} for {seconds}s "
            f"reason={reason or 'unspecified'}",
            extra={"symbol": symbol, "action": "route_cooldown_set"},
        )

    async def is_route_cooled_down(
        self,
        symbol: str,
        long_exchange: str,
        short_exchange: str,
    ) -> bool:
        """Check if this specific route (symbol|long|short) is in cooldown."""
        route_key = f"{symbol}|{long_exchange}|{short_exchange}"
        return bool(await self._c.exists(self._key("cooldown_route", route_key)))
