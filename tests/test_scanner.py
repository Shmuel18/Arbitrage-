"""Tests for scanner — opportunity detection."""

import time
from dataclasses import replace
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
    def test_opportunity_fingerprint_changes_when_funding_cycle_rolls(self, scanner):
        """A rolled funding timestamp must trigger a fresh publish fingerprint."""
        from src.core.contracts import OpportunityCandidate

        current = OpportunityCandidate(
            symbol="ETH/USDT",
            long_exchange="exchange_a",
            short_exchange="exchange_b",
            long_funding_rate=Decimal("0.0001"),
            short_funding_rate=Decimal("0.0005"),
            funding_spread_pct=Decimal("0.06"),
            immediate_spread_pct=Decimal("0.9"),
            immediate_net_pct=Decimal("0.7"),
            gross_edge_pct=Decimal("1.2"),
            fees_pct=Decimal("0.2"),
            net_edge_pct=Decimal("0.7"),
            suggested_qty=Decimal("0.01"),
            reference_price=Decimal("50000"),
            next_funding_ms=1_000_000,
            long_next_funding_ms=1_000_000,
            short_next_funding_ms=1_000_000,
        )
        rolled = replace(
            current,
            next_funding_ms=1_360_000,
            long_next_funding_ms=1_360_000,
            short_next_funding_ms=1_360_000,
        )

        assert scanner._opportunity_fingerprint([current]) != scanner._opportunity_fingerprint([rolled])

    @pytest.mark.asyncio
    async def test_finds_opportunity_when_funding_spread_exists(self, scanner, config, mock_exchange_mgr):
        """High funding spread → should produce an opportunity."""
        config.trading_params.min_funding_spread = Decimal("0.01")

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
        # Below threshold: returned as display-only (qualified=False)
        qualified = [r for r in results if r.qualified]
        assert len(qualified) == 0
        for r in results:
            assert r.qualified is False

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


class TestIntervalFromFunding:
    """Interval is now detected in adapter.get_funding_rate, not scanner."""

    @pytest.mark.asyncio
    async def test_interval_hours_used_in_spread_calc(self, scanner, config, mock_exchange_mgr):
        """Different intervals should affect funding spread calculation."""
        config.trading_params.min_funding_spread = Decimal("0.001")

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
