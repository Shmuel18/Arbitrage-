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
        self._fallback: bool = False          # True ⇒ in-memory mode
        self._mem: Dict[str, Any] = {}        # in-memory store

    def _key(self, *parts: str) -> str:
        return self._prefix + ":".join(parts)

    # ── Lifecycle ────────────────────────────────────────────────

    async def connect(self) -> None:
        try:
            self._client = aioredis.from_url(
                self._url, decode_responses=True, socket_timeout=5,
            )
            await self._client.ping()
            self._fallback = False
            logger.info("Redis connected", extra={"action": "redis_connect"})
        except Exception as exc:
            logger.warning(
                f"Redis unavailable ({exc}) — running with in-memory fallback",
                extra={"action": "redis_fallback"},
            )
            self._client = None
            self._fallback = True

    async def disconnect(self) -> None:
        if self._client:
            await self._client.aclose()

    async def health_check(self) -> bool:
        if self._fallback:
            return True
        try:
            return bool(await self._client.ping())
        except Exception:
            return False

    # ── Trade state (crash recovery) ─────────────────────────────

    async def set_trade_state(self, trade_id: str, state: Dict[str, Any]) -> None:
        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        if self._fallback:
            self._mem[self._key("trade", trade_id)] = json.dumps(state, default=str)
            return
        key = self._key("trade", trade_id)
        await self._client.set(key, json.dumps(state, default=str), ex=86400 * 7)

    async def get_trade_state(self, trade_id: str) -> Optional[Dict[str, Any]]:
        if self._fallback:
            raw = self._mem.get(self._key("trade", trade_id))
            return json.loads(raw) if raw else None
        raw = await self._client.get(self._key("trade", trade_id))
        return json.loads(raw) if raw else None

    async def get_all_trades(self) -> Dict[str, Dict[str, Any]]:
        """Return all active trade states (for crash recovery)."""
        prefix = self._key("trade", "")
        if self._fallback:
            result: Dict[str, Dict[str, Any]] = {}
            for k, v in self._mem.items():
                if k.startswith(prefix):
                    result[k.replace(prefix, "")] = json.loads(v)
            return result
        pattern = self._key("trade", "*")
        result = {}
        async for key in self._client.scan_iter(match=pattern, count=100):
            raw = await self._client.get(key)
            if raw:
                trade_id = key.replace(prefix, "")
                result[trade_id] = json.loads(raw)
        return result

    async def delete_trade_state(self, trade_id: str) -> None:
        if self._fallback:
            self._mem.pop(self._key("trade", trade_id), None)
            return
        await self._client.delete(self._key("trade", trade_id))

    # ── Exchange health ──────────────────────────────────────────

    async def set_exchange_health(self, exchange_id: str, data: Dict[str, Any]) -> None:
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        if self._fallback:
            self._mem[self._key("health", exchange_id)] = json.dumps(data, default=str)
            return
        key = self._key("health", exchange_id)
        await self._client.set(key, json.dumps(data, default=str), ex=300)

    async def get_exchange_health(self, exchange_id: str) -> Optional[Dict[str, Any]]:
        if self._fallback:
            raw = self._mem.get(self._key("health", exchange_id))
            return json.loads(raw) if raw else None
        raw = await self._client.get(self._key("health", exchange_id))
        return json.loads(raw) if raw else None

    # ── Position snapshot (for risk guard) ───────────────────────

    async def set_position_snapshot(self, exchange_id: str, positions: list) -> None:
        if self._fallback:
            self._mem[self._key("positions", exchange_id)] = json.dumps(positions, default=str)
            return
        key = self._key("positions", exchange_id)
        await self._client.set(key, json.dumps(positions, default=str), ex=120)

    # ── Cooldown ─────────────────────────────────────────────────

    async def set_cooldown(self, symbol: str, seconds: int) -> None:
        if self._fallback:
            self._mem[self._key("cooldown", symbol)] = "1"
            logger.info(f"Cooldown set: {symbol} for {seconds}s",
                        extra={"symbol": symbol, "action": "cooldown_set"})
            return
        key = self._key("cooldown", symbol)
        await self._client.set(key, "1", ex=seconds)
        logger.info(f"Cooldown set: {symbol} for {seconds}s",
                    extra={"symbol": symbol, "action": "cooldown_set"})

    async def is_cooled_down(self, symbol: str) -> bool:
        if self._fallback:
            return self._key("cooldown", symbol) in self._mem
        return bool(await self._client.exists(self._key("cooldown", symbol)))

    # ── Distributed lock ─────────────────────────────────────────

    async def acquire_lock(self, name: str, timeout: int = 10) -> bool:
        if self._fallback:
            key = self._key("lock", name)
            if key in self._mem:
                return False
            self._mem[key] = "1"
            return True
        key = self._key("lock", name)
        return bool(await self._client.set(key, "1", nx=True, ex=timeout))

    async def release_lock(self, name: str) -> None:
        if self._fallback:
            self._mem.pop(self._key("lock", name), None)
            return
        await self._client.delete(self._key("lock", name))
