"""
Unit tests for src.api.publisher — APIPublisher.

Redis is mocked with AsyncMock so no network I/O occurs.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, call

import pytest

from src.api.publisher import APIPublisher


# ── Fixture ──────────────────────────────────────────────────────

@pytest.fixture
def mock_redis():
    """AsyncMock Redis with _client sub-mock for lpush/ltrim."""
    r = AsyncMock()
    r._client = AsyncMock()
    return r


@pytest.fixture
def publisher(mock_redis):
    return APIPublisher(mock_redis)


# ── publish_status ────────────────────────────────────────────────

class TestPublishStatus:
    async def test_calls_redis_set(self, publisher, mock_redis):
        await publisher.publish_status(
            running=True, exchanges=["binance", "bybit"], positions_count=2
        )
        mock_redis.set.assert_called_once()
        key, payload = mock_redis.set.call_args[0][:2]
        assert key == "trinity:status"
        data = json.loads(payload)
        assert data["bot_running"] is True
        assert data["active_positions"] == 2
        assert "binance" in data["connected_exchanges"]

    async def test_status_includes_uptime(self, publisher, mock_redis):
        await publisher.publish_status(running=False, exchanges=[], positions_count=0)
        _, payload = mock_redis.set.call_args[0][:2]
        data = json.loads(payload)
        assert "uptime" in data
        assert data["uptime"] >= 0

    async def test_status_expires_in_15s(self, publisher, mock_redis):
        await publisher.publish_status(running=True, exchanges=[], positions_count=0)
        kwargs = mock_redis.set.call_args.kwargs
        assert kwargs.get("ex") == 15

    async def test_custom_min_funding_spread(self, publisher, mock_redis):
        await publisher.publish_status(
            running=True, exchanges=[], positions_count=0, min_funding_spread=0.8
        )
        _, payload = mock_redis.set.call_args[0][:2]
        data = json.loads(payload)
        assert data["min_funding_spread"] == 0.8


# ── publish_balances ──────────────────────────────────────────────

class TestPublishBalances:
    async def test_computes_total(self, publisher, mock_redis):
        await publisher.publish_balances({"binance": 500.0, "bybit": 300.0})
        _, payload = mock_redis.set.call_args[0][:2]
        data = json.loads(payload)
        assert data["total"] == 800.0
        assert data["balances"]["binance"] == 500.0

    async def test_stores_at_correct_key(self, publisher, mock_redis):
        await publisher.publish_balances({})
        key = mock_redis.set.call_args[0][0]
        assert key == "trinity:balances"


# ── publish_opportunities ─────────────────────────────────────────

class TestPublishOpportunities:
    async def test_stores_count(self, publisher, mock_redis):
        opps = [{"symbol": "BTC/USDT"}, {"symbol": "ETH/USDT"}]
        await publisher.publish_opportunities(opps)
        _, payload = mock_redis.set.call_args[0][:2]
        data = json.loads(payload)
        assert data["count"] == 2
        assert len(data["opportunities"]) == 2

    async def test_empty_list(self, publisher, mock_redis):
        await publisher.publish_opportunities([])
        _, payload = mock_redis.set.call_args[0][:2]
        data = json.loads(payload)
        assert data["count"] == 0


# ── publish_log ───────────────────────────────────────────────────

class TestPublishLog:
    async def test_pushes_to_list(self, publisher, mock_redis):
        await publisher.publish_log("INFO", "Bot started")
        mock_redis.lpush.assert_called_once()
        list_key = mock_redis.lpush.call_args[0][0]
        assert list_key == "trinity:logs"

    async def test_trims_to_200(self, publisher, mock_redis):
        await publisher.publish_log("DEBUG", "test")
        mock_redis.ltrim.assert_called_once_with("trinity:logs", 0, 199)

    async def test_log_entry_is_valid_json(self, publisher, mock_redis):
        await publisher.publish_log("WARNING", "high spread")
        entry_str = mock_redis.lpush.call_args[0][1]
        entry = json.loads(entry_str)
        assert entry["message"] == "high spread"
        assert entry["level"] == "WARNING"
        assert "timestamp" in entry


# ── push_alert ────────────────────────────────────────────────────

class TestPushAlert:
    async def test_push_alert_delegates_to_publish_log(self, publisher, mock_redis):
        await publisher.push_alert("orphan detected!")
        entry_str = mock_redis.lpush.call_args[0][1]
        entry = json.loads(entry_str)
        assert entry["level"] == "CRITICAL"
        assert entry["message"] == "orphan detected!"


# ── publish_summary ───────────────────────────────────────────────

class TestPublishSummary:
    async def test_zero_trades_win_rate_is_zero(self, publisher, mock_redis):
        await publisher.publish_summary({"binance": 1000.0}, positions_count=1)
        _, payload = mock_redis.set.call_args[0][:2]
        data = json.loads(payload)
        assert data["win_rate"] == 0
        assert data["total_trades"] == 0

    async def test_win_rate_after_trades(self, publisher, mock_redis):
        publisher.record_trade(is_win=True)
        publisher.record_trade(is_win=True)
        publisher.record_trade(is_win=False)
        await publisher.publish_summary({}, positions_count=0)
        _, payload = mock_redis.set.call_args[0][:2]
        data = json.loads(payload)
        assert abs(data["win_rate"] - 0.667) < 0.001


# ── record_trade ──────────────────────────────────────────────────

class TestRecordTrade:
    def test_increments_total(self, publisher):
        publisher.record_trade(is_win=True)
        publisher.record_trade(is_win=False)
        assert publisher._total_trades == 2

    def test_increments_wins(self, publisher):
        publisher.record_trade(is_win=True)
        publisher.record_trade(is_win=True)
        publisher.record_trade(is_win=False)
        assert publisher._winning_trades == 2


# ── publish_positions ─────────────────────────────────────────────

class TestPublishPositions:
    async def test_stores_positions(self, publisher, mock_redis):
        positions = [{"symbol": "BTC/USDT", "qty": "0.01"}]
        await publisher.publish_positions(positions)
        key, payload = mock_redis.set.call_args[0][:2]
        assert key == "trinity:positions"
        assert json.loads(payload)[0]["symbol"] == "BTC/USDT"


# ── publish_trade ─────────────────────────────────────────────────

class TestPublishTrade:
    async def test_adds_to_sorted_set(self, publisher, mock_redis):
        trade = {"trade_id": "t1", "pnl": 12.5}
        await publisher.publish_trade(trade)
        mock_redis.zadd.assert_called_once()
        zadd_key = mock_redis.zadd.call_args[0][0]
        assert zadd_key == "trinity:trades:history"


# ── publish_exchanges ─────────────────────────────────────────────

class TestPublishExchanges:
    async def test_stores_exchanges(self, publisher, mock_redis):
        exchanges = [{"id": "binance", "status": "ok"}]
        await publisher.publish_exchanges(exchanges)
        key, payload = mock_redis.set.call_args[0][:2]
        assert key == "trinity:exchanges"
        data = json.loads(payload)
        assert data["exchanges"][0]["id"] == "binance"
