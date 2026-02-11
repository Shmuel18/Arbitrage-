"""Tests for scanner — opportunity detection."""

from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from src.core.contracts import InstrumentSpec
from src.discovery.scanner import Scanner


@pytest.fixture
def scanner(config, mock_exchange_mgr, mock_redis):
    return Scanner(config, mock_exchange_mgr, mock_redis)


class TestScanAll:
    @pytest.mark.asyncio
    async def test_finds_opportunity_when_edge_exists(self, scanner, config, mock_exchange_mgr):
        """High funding diff → should produce an opportunity."""
        # Increase min so only big edges pass
        config.trading_params.min_net_bps = Decimal("1.0")

        adapter_a = mock_exchange_mgr.get("exchange_a")
        adapter_b = mock_exchange_mgr.get("exchange_b")

        adapter_a.get_funding_rate.return_value = {
            "rate": Decimal("0.0001"), "timestamp": None, "datetime": None, "next_timestamp": None,
        }
        adapter_b.get_funding_rate.return_value = {
            "rate": Decimal("0.0050"), "timestamp": None, "datetime": None, "next_timestamp": None,
        }
        adapter_a.get_ticker.return_value = {"last": 50000.0}
        adapter_b.get_ticker.return_value = {"last": 50000.0}
        adapter_a.get_balance.return_value = {"total": Decimal("10000"), "free": Decimal("8000"), "used": Decimal("2000")}
        adapter_b.get_balance.return_value = {"total": Decimal("10000"), "free": Decimal("8000"), "used": Decimal("2000")}

        results = await scanner.scan_all()
        assert len(results) >= 1
        assert results[0].net_edge_bps > 0

    @pytest.mark.asyncio
    async def test_no_opportunity_when_rates_equal(self, scanner, mock_exchange_mgr):
        adapter_a = mock_exchange_mgr.get("exchange_a")
        adapter_b = mock_exchange_mgr.get("exchange_b")

        adapter_a.get_funding_rate.return_value = {
            "rate": Decimal("0.0001"), "timestamp": None, "datetime": None, "next_timestamp": None,
        }
        adapter_b.get_funding_rate.return_value = {
            "rate": Decimal("0.0001"), "timestamp": None, "datetime": None, "next_timestamp": None,
        }

        results = await scanner.scan_all()
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_respects_cooldown(self, scanner, mock_redis):
        mock_redis.is_cooled_down.return_value = True

        results = await scanner.scan_all()
        assert len(results) == 0


class TestStaleness:
    def test_stale_data_detected(self, scanner):
        import time
        old_ts = (time.time() - 7200) * 1000  # 2 hours ago
        assert scanner._is_stale({"timestamp": old_ts}) is True

    def test_fresh_data_ok(self, scanner):
        import time
        fresh_ts = time.time() * 1000  # now
        assert scanner._is_stale({"timestamp": fresh_ts}) is False

    def test_none_timestamp_is_ok(self, scanner):
        assert scanner._is_stale({"timestamp": None}) is False


class TestDetectInterval:
    def test_detects_1h_interval(self, scanner):
        now = 1700000000000
        result = scanner._detect_interval({
            "timestamp": now,
            "next_timestamp": now + 3600 * 1000,  # +1h
        })
        assert result == 1

    def test_detects_8h_interval(self, scanner):
        now = 1700000000000
        result = scanner._detect_interval({
            "timestamp": now,
            "next_timestamp": now + 8 * 3600 * 1000,  # +8h
        })
        assert result == 8

    def test_defaults_to_8h_when_no_next(self, scanner):
        result = scanner._detect_interval({"timestamp": None, "next_timestamp": None})
        assert result == 8
