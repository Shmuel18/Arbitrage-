"""Tests for scanner — opportunity detection."""

import time
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from src.core.contracts import InstrumentSpec
from src.discovery.scanner import Scanner


@pytest.fixture
def scanner(config, mock_exchange_mgr, mock_redis):
    return Scanner(config, mock_exchange_mgr, mock_redis)


# Helper: timestamp N hours from now (in ms)
def _future_ms(hours: float) -> float:
    return time.time() * 1000 + hours * 3_600_000


class TestScanAll:
    @pytest.mark.asyncio
    async def test_finds_opportunity_when_funding_spread_exists(self, scanner, config, mock_exchange_mgr):
        """High funding spread → should produce an opportunity."""
        config.trading_params.min_funding_spread = Decimal("0.01")
        config.trading_params.min_immediate_spread = Decimal("0.01")
        config.trading_params.min_net_pct = Decimal("0.01")

        adapter_a = mock_exchange_mgr.get("exchange_a")
        adapter_b = mock_exchange_mgr.get("exchange_b")

        # rate_a=0.0001, rate_b=0.0050 → both positive
        # Best direction: long A, short B (short_pnl=+0.005 income, long_pnl=-0.0001 cost)
        # Funding spread = (-0.0001) + 0.005 = 0.0049 → 0.49% (huge)
        funding_data_a = {
            "rate": Decimal("0.0001"), "timestamp": None, "datetime": None,
            "next_timestamp": _future_ms(8), "interval_hours": 8,
        }
        funding_data_b = {
            "rate": Decimal("0.0050"), "timestamp": None, "datetime": None,
            "next_timestamp": _future_ms(1), "interval_hours": 1,
        }
        
        # Set both cache and REST fallback
        adapter_a._funding_rate_cache["ETH/USDT"] = funding_data_a
        adapter_b._funding_rate_cache["ETH/USDT"] = funding_data_b
        adapter_a.get_funding_rate.return_value = funding_data_a
        adapter_b.get_funding_rate.return_value = funding_data_b
        
        adapter_a.get_ticker.return_value = {"last": 50000.0}
        adapter_b.get_ticker.return_value = {"last": 50000.0}
        adapter_a.get_balance.return_value = {"total": Decimal("10000"), "free": Decimal("8000"), "used": Decimal("2000")}
        adapter_b.get_balance.return_value = {"total": Decimal("10000"), "free": Decimal("8000"), "used": Decimal("2000")}

        results = await scanner.scan_all()
        assert len(results) >= 1
        assert results[0].funding_spread_pct > 0
        assert results[0].net_edge_pct > 0

    @pytest.mark.asyncio
    async def test_skips_when_funding_spread_below_threshold(self, scanner, config, mock_exchange_mgr):
        """Spread below min_funding_spread → no opportunity, regardless of other factors."""
        config.trading_params.min_funding_spread = Decimal("1.0")  # Very high threshold
        config.trading_params.min_net_pct = Decimal("0.01")

        adapter_a = mock_exchange_mgr.get("exchange_a")
        adapter_b = mock_exchange_mgr.get("exchange_b")

        # rate_a=0.0001, rate_b=0.0003 → spread = (-0.0001 + 0.0003) * 100 = 0.02%
        # This is below min_funding_spread of 1.0 → SKIP
        funding_data_a = {
            "rate": Decimal("0.0001"), "timestamp": None, "datetime": None,
            "next_timestamp": _future_ms(8), "interval_hours": 8,
        }
        funding_data_b = {
            "rate": Decimal("0.0003"), "timestamp": None, "datetime": None,
            "next_timestamp": _future_ms(8), "interval_hours": 8,
        }
        
        # Set cache
        adapter_a._funding_rate_cache["ETH/USDT"] = funding_data_a
        adapter_b._funding_rate_cache["ETH/USDT"] = funding_data_b
        adapter_a.get_funding_rate.return_value = funding_data_a
        adapter_b.get_funding_rate.return_value = funding_data_b

        results = await scanner.scan_all()
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_no_opportunity_when_rates_equal(self, scanner, mock_exchange_mgr):
        adapter_a = mock_exchange_mgr.get("exchange_a")
        adapter_b = mock_exchange_mgr.get("exchange_b")

        funding_data = {
            "rate": Decimal("0.0001"), "timestamp": None, "datetime": None, "next_timestamp": None,
        }
        
        # Set cache for both
        adapter_a._funding_rate_cache["ETH/USDT"] = funding_data
        adapter_b._funding_rate_cache["ETH/USDT"] = funding_data
        adapter_a.get_funding_rate.return_value = funding_data
        adapter_b.get_funding_rate.return_value = funding_data

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


class TestIntervalFromFunding:
    """Interval is now detected in adapter.get_funding_rate, not scanner."""

    @pytest.mark.asyncio
    async def test_interval_hours_used_in_spread_calc(self, scanner, config, mock_exchange_mgr):
        """Different intervals should affect funding spread calculation."""
        config.trading_params.min_funding_spread = Decimal("0.001")
        config.trading_params.min_immediate_spread = Decimal("0.001")
        config.trading_params.min_net_pct = Decimal("0.001")

        adapter_a = mock_exchange_mgr.get("exchange_a")
        adapter_b = mock_exchange_mgr.get("exchange_b")

        # rate_a=0.0001, rate_b=0.0050 → cherry-pick (short B is income)
        # B pays every 1h, A charges every 8h → 7 collections before cost
        funding_data_a = {
            "rate": Decimal("0.0001"), "timestamp": None,
            "datetime": None, "next_timestamp": _future_ms(8), "interval_hours": 8,
        }
        funding_data_b = {
            "rate": Decimal("0.0050"), "timestamp": None,
            "datetime": None, "next_timestamp": _future_ms(1), "interval_hours": 1,
        }
        
        # Set cache
        adapter_a._funding_rate_cache["ETH/USDT"] = funding_data_a
        adapter_b._funding_rate_cache["ETH/USDT"] = funding_data_b
        adapter_a.get_funding_rate.return_value = funding_data_a
        adapter_b.get_funding_rate.return_value = funding_data_b
        
        adapter_a.get_ticker.return_value = {"last": 50000.0}
        adapter_b.get_ticker.return_value = {"last": 50000.0}
        adapter_a.get_balance.return_value = {"total": Decimal("10000"), "free": Decimal("8000"), "used": Decimal("2000")}
        adapter_b.get_balance.return_value = {"total": Decimal("10000"), "free": Decimal("8000"), "used": Decimal("2000")}

        results = await scanner.scan_all()
        # With 1h interval on B (income side), we collect ~7 payments before
        # the 8h cost payment on A → huge cherry-pick edge
        assert len(results) >= 1
