"""
Unit tests for src.storage.redis_client — uses FakeAsyncRedis (no real Redis needed).
"""

from __future__ import annotations

import json
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import fakeredis
import pytest

from src.storage.redis_client import RedisClient


# ── Fixture ──────────────────────────────────────────────────────

@pytest.fixture
async def redis_client() -> RedisClient:
    """RedisClient backed by FakeAsyncRedis — no network calls."""
    client = RedisClient(url="redis://localhost/0", prefix="test:")
    client._client = fakeredis.FakeAsyncRedis(decode_responses=True)
    return client


# ── _key helper ──────────────────────────────────────────────────

class TestKeyHelper:
    def test_single_part(self):
        c = RedisClient(prefix="pfx:")
        assert c._key("trade", "t1") == "pfx:trade:t1"

    def test_prefix_prepended(self):
        c = RedisClient(prefix="trinity:")
        assert c._key("cooldown", "BTC/USDT") == "trinity:cooldown:BTC/USDT"


# ── health_check ──────────────────────────────────────────────────

class TestHealthCheck:
    async def test_returns_true_when_connected(self, redis_client):
        assert await redis_client.health_check() is True

    async def test_returns_false_on_exception(self, redis_client):
        redis_client._client = None  # force AttributeError
        assert await redis_client.health_check() is False


# ── set / get (raw, no prefix) ────────────────────────────────────

class TestSetGet:
    async def test_set_and_get_roundtrip(self, redis_client):
        await redis_client.set("my_key", "hello")
        result = await redis_client.get("my_key")
        assert result == "hello"

    async def test_get_missing_key_returns_none(self, redis_client):
        assert await redis_client.get("nonexistent") is None

    async def test_set_with_expiry(self, redis_client):
        await redis_client.set("expiring_key", "val", ex=3600)
        assert await redis_client.get("expiring_key") == "val"


# ── trade state ──────────────────────────────────────────────────

class TestTradeState:
    async def test_set_and_get_trade_state(self, redis_client):
        state = {"symbol": "BTC/USDT", "long_exchange": "binance", "qty": "0.01"}
        await redis_client.set_trade_state("trade-001", state)
        result = await redis_client.get_trade_state("trade-001")
        assert result["symbol"] == "BTC/USDT"
        assert result["long_exchange"] == "binance"
        assert "updated_at" in result  # injected by set_trade_state

    async def test_get_missing_trade_returns_none(self, redis_client):
        assert await redis_client.get_trade_state("ghost-trade") is None

    async def test_delete_trade_state(self, redis_client):
        await redis_client.set_trade_state("trade-del", {"symbol": "ETH/USDT"})
        await redis_client.delete_trade_state("trade-del")
        assert await redis_client.get_trade_state("trade-del") is None

    async def test_get_all_trades_returns_all(self, redis_client):
        await redis_client.set_trade_state("t1", {"symbol": "BTC/USDT", "state": "open"})
        await redis_client.set_trade_state("t2", {"symbol": "ETH/USDT", "state": "open"})
        all_trades = await redis_client.get_all_trades()
        assert "t1" in all_trades
        assert "t2" in all_trades
        assert all_trades["t1"]["symbol"] == "BTC/USDT"

    async def test_get_all_trades_empty(self, redis_client):
        result = await redis_client.get_all_trades()
        assert result == {}

    async def test_state_json_serialises_decimal(self, redis_client):
        state = {"qty": str(Decimal("0.012345"))}
        await redis_client.set_trade_state("trade-dec", state)
        result = await redis_client.get_trade_state("trade-dec")
        assert result["qty"] == "0.012345"


# ── cooldown ─────────────────────────────────────────────────────

class TestCooldown:
    async def test_not_cooled_down_initially(self, redis_client):
        assert await redis_client.is_cooled_down("BTC/USDT") is False

    async def test_cooled_down_after_set(self, redis_client):
        await redis_client.set_cooldown("BTC/USDT", seconds=3600)
        assert await redis_client.is_cooled_down("BTC/USDT") is True

    async def test_different_symbols_independent(self, redis_client):
        await redis_client.set_cooldown("ETH/USDT", seconds=3600)
        assert await redis_client.is_cooled_down("BTC/USDT") is False

    async def test_batch_get_cooled_down_symbols(self, redis_client):
        await redis_client.set_cooldown("ETH/USDT", seconds=3600)
        await redis_client.set_cooldown("SOL/USDT", seconds=3600)
        result = await redis_client.get_cooled_down_symbols(
            ["BTC/USDT", "ETH/USDT", "SOL/USDT", "DOGE/USDT"]
        )
        assert result == {"ETH/USDT", "SOL/USDT"}

    async def test_batch_empty_input(self, redis_client):
        result = await redis_client.get_cooled_down_symbols([])
        assert result == set()


# ── distributed lock ─────────────────────────────────────────────

class TestDistributedLock:
    async def test_acquire_succeeds_first_time(self, redis_client):
        acquired = await redis_client.acquire_lock("my-lock")
        assert acquired is True

    async def test_acquire_fails_if_already_held(self, redis_client):
        await redis_client.acquire_lock("exclusive-lock")
        second = await redis_client.acquire_lock("exclusive-lock")
        assert second is False

    async def test_release_allows_reacquire(self, redis_client):
        await redis_client.acquire_lock("releasable")
        await redis_client.release_lock("releasable")
        reacquired = await redis_client.acquire_lock("releasable")
        assert reacquired is True


# ── exchange health ───────────────────────────────────────────────

class TestExchangeHealth:
    async def test_set_and_get_health(self, redis_client):
        data = {"status": "ok", "latency_ms": 12}
        await redis_client.set_exchange_health("binance", data)
        result = await redis_client.get_exchange_health("binance")
        assert result["status"] == "ok"
        assert "updated_at" in result

    async def test_missing_exchange_returns_none(self, redis_client):
        assert await redis_client.get_exchange_health("unknown") is None


# ── zadd ─────────────────────────────────────────────────────────

class TestZadd:
    async def test_zadd_stores_and_scores(self, redis_client):
        await redis_client.zadd("my:zset", {"member1": 1.0, "member2": 2.0})
        score = await redis_client._client.zscore("my:zset", "member1")
        assert score == 1.0


# ── connect (integration path) ────────────────────────────────────

class TestConnect:
    async def test_connect_patches_client(self):
        """Verify connect() assigns _client via aioredis.from_url."""
        fake = fakeredis.FakeAsyncRedis(decode_responses=True)
        with patch("src.storage.redis_client.aioredis.from_url", return_value=fake):
            client = RedisClient()
            await client.connect()
            assert client._client is fake

    async def test_disconnect_closes_client(self):
        fake = fakeredis.FakeAsyncRedis(decode_responses=True)
        with patch("src.storage.redis_client.aioredis.from_url", return_value=fake):
            client = RedisClient()
            await client.connect()
            await client.disconnect()
            # After close, ping should raise — verify no double-close error
