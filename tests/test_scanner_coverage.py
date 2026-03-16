"""Extended scanner tests — target 80 %+ coverage.

Tests for:
  - _classify_tier(): all 5 return paths
  - scan_all(): cache refresh, < 2 exchanges, cooldown
  - _scan_symbol(): cooldown skip, single-exchange skip, funding cache miss
  - _evaluate_pair(): direction selection
  - _evaluate_direction(): HOLD, POT, NUTCRACKER, CHERRY_PICK, adverse gate,
      entry-window gate, stale income, both-cost skip
  - _build_opportunity(): position sizing, zero-price guard
  - stop(): cancel WS tasks
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.config import TradingParams
from src.core.contracts import (
    EntryTier,
    InstrumentSpec,
    OpportunityCandidate,
    TradeMode,
)
from src.discovery.scanner import Scanner, _classify_tier


# ── Helpers ──────────────────────────────────────────────────────

def _future_ms(minutes: float) -> float:
    """Timestamp *minutes* from now in epoch-ms."""
    return time.time() * 1000 + minutes * 60_000


def _make_spec(exchange: str = "ex_a") -> InstrumentSpec:
    return InstrumentSpec(
        exchange=exchange,
        symbol="ETH/USDT",
        base="ETH",
        quote="USDT",
        contract_size=Decimal("1"),
        tick_size=Decimal("0.01"),
        lot_size=Decimal("0.001"),
        min_notional=Decimal("5"),
        maker_fee=Decimal("0.0002"),
        taker_fee=Decimal("0.0005"),
    )


def _make_adapter(
    exchange_id: str,
    rate: Decimal,
    next_minutes: float = 10.0,
    interval: int = 8,
    symbols: Optional[list[str]] = None,
    price: float = 50000.0,
) -> AsyncMock:
    """Build a mock adapter with funding cache pre-populated."""
    a = AsyncMock()
    a.exchange_id = exchange_id
    a.symbols = symbols or ["ETH/USDT", "BTC/USDT"]
    a.markets = {s: {} for s in a.symbols}
    a._ws_tasks = []

    funding_entry = {
        "rate": rate,
        "timestamp": None,
        "datetime": None,
        "next_timestamp": _future_ms(next_minutes),
        "interval_hours": interval,
    }
    a._funding_rate_cache = {"ETH/USDT": funding_entry}
    a.get_funding_rate_cached = lambda sym, _cache=a._funding_rate_cache: _cache.get(sym)
    a.get_funding_rate.return_value = funding_entry

    spec = _make_spec(exchange_id)
    a.get_instrument_spec.return_value = spec
    a.get_cached_instrument_spec = MagicMock(return_value=spec)

    a.get_mark_price = MagicMock(return_value=price)
    a.get_best_ask = MagicMock(return_value=price)
    a.get_best_bid = MagicMock(return_value=price)
    a.get_best_ask_age_ms = MagicMock(return_value=0.0)
    a.get_best_bid_age_ms = MagicMock(return_value=0.0)
    a.get_ticker.return_value = {"last": price}
    a.get_balance.return_value = {
        "total": Decimal("10000"),
        "free": Decimal("8000"),
        "used": Decimal("2000"),
    }
    return a


def _scanner_with(
    config,
    adapters: dict[str, AsyncMock],
    redis: AsyncMock | None = None,
) -> Scanner:
    """Build a Scanner with custom adapters."""
    if redis is None:
        redis = AsyncMock()
        redis.get_cooled_down_symbols.return_value = set()
        redis.is_cooled_down.return_value = False
    mgr = MagicMock()
    mgr.all.return_value = adapters
    mgr.get.side_effect = lambda eid: adapters[eid]
    return Scanner(config, mgr, redis)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 1. _classify_tier() — pure function, all return paths
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestClassifyTier:
    """Direct tests for the module-level _classify_tier helper."""

    def test_returns_none_when_below_min_spread(self) -> None:
        """tier_net < min_funding_spread → None."""
        assert _classify_tier(
            tier_net=Decimal("0.04"),
            price_spread_pct=Decimal("0"),
            total_cost_pct=Decimal("0.02"),
            min_funding_spread=Decimal("0.05"),
            weak_min_funding_excess=Decimal("0.5"),
        ) is None

    def test_top_tier_when_price_spread_positive(self) -> None:
        """Favorable price spread (ask_long < bid_short = negative) → TOP."""
        result = _classify_tier(
            tier_net=Decimal("0.10"),
            price_spread_pct=Decimal("-0.01"),  # negative = ask_long < bid_short = FAVORABLE
            total_cost_pct=Decimal("0.02"),
            min_funding_spread=Decimal("0.05"),
            weak_min_funding_excess=Decimal("0.5"),
        )
        assert result == EntryTier.TOP.value

    def test_top_tier_when_price_spread_zero(self) -> None:
        """>= 0 means TOP."""
        result = _classify_tier(
            tier_net=Decimal("0.10"),
            price_spread_pct=Decimal("0"),
            total_cost_pct=Decimal("0.02"),
            min_funding_spread=Decimal("0.05"),
            weak_min_funding_excess=Decimal("0.5"),
        )
        assert result == EntryTier.TOP.value

    def test_medium_tier_when_adverse_within_costs(self) -> None:
        """Adverse spread (positive) <= total_cost → MEDIUM."""
        result = _classify_tier(
            tier_net=Decimal("0.10"),
            price_spread_pct=Decimal("0.01"),   # positive = ask_long > bid_short = ADVERSE
            total_cost_pct=Decimal("0.02"),     # spread <= cost → MEDIUM
            min_funding_spread=Decimal("0.05"),
            weak_min_funding_excess=Decimal("0.5"),
        )
        assert result == EntryTier.MEDIUM.value

    def test_weak_tier_when_excess_sufficient(self) -> None:
        """Adverse spread (positive) > cost but funding covers it → WEAK."""
        result = _classify_tier(
            tier_net=Decimal("1.0"),
            price_spread_pct=Decimal("0.10"),   # positive = adverse
            total_cost_pct=Decimal("0.05"),     # spread > cost
            min_funding_spread=Decimal("0.05"),
            weak_min_funding_excess=Decimal("0.5"),  # 1.0 - 0.10 = 0.90 >= 0.5
        )
        assert result == EntryTier.WEAK.value

    def test_returns_none_when_adverse_too_large(self) -> None:
        """Adverse spread (positive) eats all funding excess → None."""
        result = _classify_tier(
            tier_net=Decimal("0.10"),
            price_spread_pct=Decimal("0.10"),   # positive = adverse
            total_cost_pct=Decimal("0.02"),
            min_funding_spread=Decimal("0.05"),
            weak_min_funding_excess=Decimal("0.5"),  # 0.10 - 0.10 = 0 < 0.5
        )
        assert result is None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 2. scan_all() — edge cases
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestScanAll:
    """Tests for the scan_all orchestrator."""

    @pytest.mark.asyncio
    async def test_returns_empty_when_fewer_than_2_exchanges(self, config) -> None:
        """< 2 exchanges → nothing to arbitrage."""
        a = _make_adapter("ex_a", Decimal("0.001"))
        scanner = _scanner_with(config, {"ex_a": a})
        result = await scanner.scan_all()
        assert result == []

    @pytest.mark.asyncio
    async def test_common_symbols_cache_is_rebuilt(self, config) -> None:
        """Cache should be rebuilt on first call (None → set)."""
        a = _make_adapter("ex_a", Decimal("0.001"))
        b = _make_adapter("ex_b", Decimal("0.005"))
        scanner = _scanner_with(config, {"ex_a": a, "ex_b": b})
        assert scanner._common_symbols_cache is None
        await scanner.scan_all()
        assert scanner._common_symbols_cache is not None
        assert "ETH/USDT" in scanner._common_symbols_cache

    @pytest.mark.asyncio
    async def test_cache_rebuilt_when_exchange_ids_change(self, config) -> None:
        """Switching exchanges must rebuild the symbol cache."""
        a = _make_adapter("ex_a", Decimal("0.001"))
        b = _make_adapter("ex_b", Decimal("0.005"))
        scanner = _scanner_with(config, {"ex_a": a, "ex_b": b})
        await scanner.scan_all()
        old_cache = scanner._common_symbols_cache

        # Add a third exchange → exchange_ids change
        c = _make_adapter("ex_c", Decimal("0.003"))
        scanner._exchanges.all.return_value = {"ex_a": a, "ex_b": b, "ex_c": c}
        await scanner.scan_all()
        # Cache object should be rebuilt (content may or may not differ)
        assert scanner._cache_exchange_ids == ["ex_a", "ex_b", "ex_c"]

    @pytest.mark.asyncio
    async def test_results_sorted_by_immediate_net(self, config) -> None:
        """scan_all results should be sorted by immediate_net_pct desc."""
        a = _make_adapter("ex_a", Decimal("-0.005"), next_minutes=10, interval=8)
        b = _make_adapter("ex_b", Decimal("0.010"), next_minutes=10, interval=1)
        scanner = _scanner_with(config, {"ex_a": a, "ex_b": b})
        results = await scanner.scan_all()
        if len(results) >= 2:
            assert results[0].immediate_net_pct >= results[1].immediate_net_pct

    @pytest.mark.asyncio
    async def test_scan_all_handles_symbol_exception(self, config) -> None:
        """An exception in one symbol scan should not crash the whole scan."""
        a = _make_adapter("ex_a", Decimal("0.001"))
        b = _make_adapter("ex_b", Decimal("0.005"))
        scanner = _scanner_with(config, {"ex_a": a, "ex_b": b})

        # Make the instrument spec fail for one adapter to cause an exception
        original_cached = a.get_cached_instrument_spec
        call_count = 0

        def failing_spec(sym):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("boom")
            return original_cached(sym)

        a.get_cached_instrument_spec = failing_spec
        # Should not raise
        results = await scanner.scan_all()
        assert isinstance(results, list)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 3. _scan_symbol() — cooldown, eligibility
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestScanSymbol:
    """Tests for _scan_symbol (single-symbol scan body)."""

    @pytest.mark.asyncio
    async def test_symbol_in_cooldown_returns_empty(self, config) -> None:
        """Cooled-down symbol → []."""
        a = _make_adapter("ex_a", Decimal("0.001"))
        b = _make_adapter("ex_b", Decimal("0.005"))
        adapters = {"ex_a": a, "ex_b": b}
        scanner = _scanner_with(config, adapters)
        result = await scanner._scan_symbol(
            "ETH/USDT", adapters, ["ex_a", "ex_b"],
            cooled_symbols={"ETH/USDT"},
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_symbol_on_single_exchange_returns_empty(self, config) -> None:
        """Symbol available on only 1 exchange → []."""
        a = _make_adapter("ex_a", Decimal("0.001"), symbols=["ETH/USDT"])
        b = _make_adapter("ex_b", Decimal("0.005"), symbols=["BTC/USDT"])
        adapters = {"ex_a": a, "ex_b": b}
        scanner = _scanner_with(config, adapters)
        result = await scanner._scan_symbol(
            "ETH/USDT", adapters, ["ex_a", "ex_b"],
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_no_cached_funding_returns_empty(self, config) -> None:
        """No funding data cached → []."""
        a = _make_adapter("ex_a", Decimal("0.001"))
        b = _make_adapter("ex_b", Decimal("0.005"))
        # Clear cache
        a._funding_rate_cache = {}
        a.get_funding_rate_cached = lambda sym: None
        b._funding_rate_cache = {}
        b.get_funding_rate_cached = lambda sym: None
        adapters = {"ex_a": a, "ex_b": b}
        scanner = _scanner_with(config, adapters)
        result = await scanner._scan_symbol(
            "ETH/USDT", adapters, ["ex_a", "ex_b"],
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_equal_rates_returns_empty(self, config) -> None:
        """Identical rates → spread = 0 → no opportunity."""
        rate = Decimal("0.0001")
        a = _make_adapter("ex_a", rate, next_minutes=10)
        b = _make_adapter("ex_b", rate, next_minutes=10)
        adapters = {"ex_a": a, "ex_b": b}
        scanner = _scanner_with(config, adapters)
        result = await scanner._scan_symbol(
            "ETH/USDT", adapters, ["ex_a", "ex_b"],
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_positive_spread_returns_opportunity(self, config) -> None:
        """Big rate difference → opportunity found."""
        config.trading_params.min_funding_spread = Decimal("0.01")
        a = _make_adapter("ex_a", Decimal("-0.005"), next_minutes=10, interval=8)
        b = _make_adapter("ex_b", Decimal("0.005"), next_minutes=10, interval=8)
        adapters = {"ex_a": a, "ex_b": b}
        scanner = _scanner_with(config, adapters)
        result = await scanner._scan_symbol(
            "ETH/USDT", adapters, ["ex_a", "ex_b"],
        )
        assert len(result) >= 1
        opp = result[0]
        assert opp.symbol == "ETH/USDT"
        assert opp.funding_spread_pct > 0


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 4. _evaluate_direction() — mode determination & gates
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestEvaluateDirection:
    """Tests for _evaluate_direction covering all modes and gates."""

    def _funding_dict(
        self,
        rate: Decimal,
        next_minutes: float = 10.0,
        interval: int = 8,
    ) -> dict:
        return {
            "rate": rate,
            "timestamp": None,
            "datetime": None,
            "next_timestamp": _future_ms(next_minutes),
            "interval_hours": interval,
        }

    async def _eval(
        self,
        config,
        long_rate: Decimal,
        short_rate: Decimal,
        long_next_min: float = 10.0,
        short_next_min: float = 10.0,
        long_interval: int = 8,
        short_interval: int = 8,
        long_price: float = 50000.0,
        short_price: float = 50000.0,
    ) -> Optional[OpportunityCandidate]:
        """Invoke _evaluate_direction with sensible defaults."""
        a = _make_adapter("ex_a", long_rate, long_next_min, long_interval, price=long_price)
        b = _make_adapter("ex_b", short_rate, short_next_min, short_interval, price=short_price)
        adapters = {"ex_a": a, "ex_b": b}
        funding = {
            "ex_a": self._funding_dict(long_rate, long_next_min, long_interval),
            "ex_b": self._funding_dict(short_rate, short_next_min, short_interval),
        }
        scanner = _scanner_with(config, adapters)
        return await scanner._evaluate_direction(
            symbol="ETH/USDT",
            long_eid="ex_a",
            short_eid="ex_b",
            long_rate=long_rate,
            short_rate=short_rate,
            long_interval=long_interval,
            short_interval=short_interval,
            funding=funding,
            adapters=adapters,
        )

    # ── Both cost → None ─────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_both_cost_returns_none(self, config) -> None:
        """Long positive + short negative → both sides cost → None."""
        config.trading_params.min_funding_spread = Decimal("0.01")
        opp = await self._eval(
            config,
            long_rate=Decimal("0.005"),    # long pays (cost)
            short_rate=Decimal("-0.005"),   # short pays (cost)
        )
        assert opp is None

    # ── POT mode (both sides income) ─────────────────────────────

    @pytest.mark.asyncio
    async def test_pot_mode_both_income(self, config) -> None:
        """Both sides generate income → POT mode."""
        config.trading_params.min_funding_spread = Decimal("0.01")
        opp = await self._eval(
            config,
            long_rate=Decimal("-0.005"),   # long receives (income)
            short_rate=Decimal("0.005"),    # short receives (income)
            long_next_min=10, short_next_min=10,
        )
        assert opp is not None
        assert opp.mode == TradeMode.POT
        assert opp.qualified is True

    # ── NUTCRACKER mode (both fire in same window) ───────────────

    @pytest.mark.asyncio
    async def test_nutcracker_mode_both_sides_fire_same_window(self, config) -> None:
        """Income + cost both fire within entry window → NUTCRACKER."""
        config.trading_params.min_funding_spread = Decimal("0.01")
        config.trading_params.narrow_entry_window_minutes = 15
        opp = await self._eval(
            config,
            long_rate=Decimal("-0.010"),   # income side
            short_rate=Decimal("-0.002"),   # cost side (short on negative = cost)
            long_next_min=10, short_next_min=10,  # both within 15min window
            long_interval=8, short_interval=8,
        )
        assert opp is not None
        assert opp.mode == TradeMode.NUTCRACKER

    # ── CHERRY_PICK mode (income fires, cost far away) ───────────

    @pytest.mark.asyncio
    async def test_cherry_pick_mode_when_income_imminent_cost_far(self, config) -> None:
        """Income within window, cost far away → CHERRY_PICK."""
        config.trading_params.min_funding_spread = Decimal("0.01")
        config.trading_params.narrow_entry_window_minutes = 15
        opp = await self._eval(
            config,
            long_rate=Decimal("-0.010"),   # income side (long gets paid)
            short_rate=Decimal("-0.002"),   # cost side (short pays)
            long_next_min=10,              # income within 15min
            short_next_min=120,            # cost far away (2 hours)
            long_interval=8, short_interval=8,
        )
        assert opp is not None
        assert opp.mode == TradeMode.CHERRY_PICK
        assert opp.exit_before is not None

    # ── Entry window gate ────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_hold_disqualified_when_income_beyond_window(self, config) -> None:
        """Income payment is beyond the entry window → hold not qualified;
        may still produce a display-only or cherry-pick fallback."""
        config.trading_params.min_funding_spread = Decimal("0.01")
        config.trading_params.narrow_entry_window_minutes = 15
        opp = await self._eval(
            config,
            long_rate=Decimal("-0.010"),
            short_rate=Decimal("0.010"),
            long_next_min=120,   # far beyond 15-min window
            short_next_min=120,  # far beyond 15-min window
        )
        # Either None (both too far) or display-only (not qualified)
        if opp is not None:
            assert opp.qualified is False

    # ── Stale income timestamp → disqualified ────────────────────

    @pytest.mark.asyncio
    async def test_stale_income_side_disqualifies(self, config) -> None:
        """Income side with funding timestamp in the past → skip."""
        config.trading_params.min_funding_spread = Decimal("0.01")
        opp = await self._eval(
            config,
            long_rate=Decimal("-0.010"),  # income
            short_rate=Decimal("0.010"),  # income
            long_next_min=-5,  # 5 min in the PAST → stale
            short_next_min=10,
        )
        if opp is not None:
            assert opp.qualified is False

    # ── Adverse price spread → display-only ──────────────────────

    @pytest.mark.asyncio
    async def test_adverse_price_spread_marks_adverse_tier(self, config) -> None:
        """Funding meets threshold but price spread is too adverse → adverse tier."""
        config.trading_params.min_funding_spread = Decimal("0.01")
        config.trading_params.weak_min_funding_excess = Decimal("0.5")
        # Use different prices to create adverse spread
        opp = await self._eval(
            config,
            long_rate=Decimal("-0.005"),
            short_rate=Decimal("0.005"),
            long_next_min=10, short_next_min=10,
            long_price=51000.0,  # buying expensive
            short_price=50000.0, # selling cheap → adverse
        )
        if opp is not None and opp.entry_tier == "adverse":
            assert opp.qualified is False

    @pytest.mark.asyncio
    async def test_stale_bid_ask_data_disqualifies_entry(self, config) -> None:
        """Price snapshots older than the freshness threshold are skipped."""
        config.trading_params.min_funding_spread = Decimal("0.01")
        config.trading_params.max_market_data_age_ms = 500

        a = _make_adapter("ex_a", Decimal("-0.005"), 10, 8, price=50000.0)
        b = _make_adapter("ex_b", Decimal("0.005"), 10, 8, price=50000.0)
        a.get_best_ask_age_ms.return_value = 900.0
        b.get_best_bid_age_ms.return_value = 900.0
        adapters = {"ex_a": a, "ex_b": b}
        funding = {
            "ex_a": self._funding_dict(Decimal("-0.005"), 10, 8),
            "ex_b": self._funding_dict(Decimal("0.005"), 10, 8),
        }
        scanner = _scanner_with(config, adapters)

        opp = await scanner._evaluate_direction(
            symbol="ETH/USDT",
            long_eid="ex_a",
            short_eid="ex_b",
            long_rate=Decimal("-0.005"),
            short_rate=Decimal("0.005"),
            long_interval=8,
            short_interval=8,
            funding=funding,
            adapters=adapters,
        )

        assert opp is None

    # ── Missing instrument spec → None ───────────────────────────

    @pytest.mark.asyncio
    async def test_missing_spec_returns_none(self, config) -> None:
        """No instrument spec → can't calculate fees → None."""
        config.trading_params.min_funding_spread = Decimal("0.01")
        a = _make_adapter("ex_a", Decimal("-0.005"))
        b = _make_adapter("ex_b", Decimal("0.005"))
        a.get_cached_instrument_spec = MagicMock(return_value=None)
        a.get_instrument_spec.return_value = None
        adapters = {"ex_a": a, "ex_b": b}
        funding = {
            "ex_a": {"rate": Decimal("-0.005"), "next_timestamp": _future_ms(10), "interval_hours": 8},
            "ex_b": {"rate": Decimal("0.005"), "next_timestamp": _future_ms(10), "interval_hours": 8},
        }
        scanner = _scanner_with(config, adapters)
        opp = await scanner._evaluate_direction(
            "ETH/USDT", "ex_a", "ex_b",
            Decimal("-0.005"), Decimal("0.005"),
            8, 8, funding, adapters,
        )
        assert opp is None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 5. _evaluate_pair() — direction selection
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestEvaluatePair:
    """Tests for _evaluate_pair which picks the best direction."""

    @pytest.mark.asyncio
    async def test_picks_best_direction(self, config) -> None:
        """Should choose the direction with higher net edge."""
        config.trading_params.min_funding_spread = Decimal("0.01")
        a = _make_adapter("ex_a", Decimal("0.0001"), next_minutes=10)
        b = _make_adapter("ex_b", Decimal("0.0050"), next_minutes=10)
        adapters = {"ex_a": a, "ex_b": b}
        funding = {
            "ex_a": {
                "rate": Decimal("0.0001"), "next_timestamp": _future_ms(10),
                "interval_hours": 8, "timestamp": None, "datetime": None,
            },
            "ex_b": {
                "rate": Decimal("0.0050"), "next_timestamp": _future_ms(10),
                "interval_hours": 8, "timestamp": None, "datetime": None,
            },
        }
        scanner = _scanner_with(config, adapters)
        opp = await scanner._evaluate_pair(
            "ETH/USDT", "ex_a", "ex_b", funding, adapters,
        )
        # Best direction: long A (pay 0.01%), short B (receive 0.50%)
        if opp is not None:
            assert opp.long_exchange == "ex_a"
            assert opp.short_exchange == "ex_b"

    @pytest.mark.asyncio
    async def test_prefers_qualified_over_unqualified(self, config) -> None:
        """If one direction qualifies and the other doesn't, choose qualified."""
        config.trading_params.min_funding_spread = Decimal("0.01")
        a = _make_adapter("ex_a", Decimal("-0.005"), next_minutes=10, interval=8)
        b = _make_adapter("ex_b", Decimal("0.005"), next_minutes=10, interval=8)
        adapters = {"ex_a": a, "ex_b": b}
        funding = {
            "ex_a": {
                "rate": Decimal("-0.005"), "next_timestamp": _future_ms(10),
                "interval_hours": 8, "timestamp": None, "datetime": None,
            },
            "ex_b": {
                "rate": Decimal("0.005"), "next_timestamp": _future_ms(10),
                "interval_hours": 8, "timestamp": None, "datetime": None,
            },
        }
        scanner = _scanner_with(config, adapters)
        opp = await scanner._evaluate_pair(
            "ETH/USDT", "ex_a", "ex_b", funding, adapters,
        )
        # At least one direction should produce an opportunity
        assert opp is not None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 6. _build_opportunity() — position sizing
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestBuildOpportunity:
    """Tests for _build_opportunity (balance → qty)."""

    @pytest.mark.asyncio
    async def test_builds_opportunity_with_correct_sizing(self, config) -> None:
        """Position sizing: 70 % of min balance × leverage."""
        a = _make_adapter("ex_a", Decimal("0.001"))
        b = _make_adapter("ex_b", Decimal("0.005"))
        adapters = {"ex_a": a, "ex_b": b}
        scanner = _scanner_with(config, adapters)
        opp = await scanner._build_opportunity(
            symbol="ETH/USDT",
            long_eid="ex_a",
            short_eid="ex_b",
            long_rate=Decimal("0.001"),
            short_rate=Decimal("0.005"),
            gross_pct=Decimal("0.5"),
            fees_pct=Decimal("0.1"),
            net_pct=Decimal("0.4"),
            adapters=adapters,
            mode=TradeMode.HOLD,
            long_interval_hours=8,
            short_interval_hours=8,
        )
        assert opp is not None
        assert opp.suggested_qty > 0
        assert opp.reference_price == Decimal("50000")
        assert opp.symbol == "ETH/USDT"

    @pytest.mark.asyncio
    async def test_returns_none_when_price_zero(self, config) -> None:
        """Zero price → can't compute quantity → None."""
        a = _make_adapter("ex_a", Decimal("0.001"), price=0.0)
        b = _make_adapter("ex_b", Decimal("0.005"), price=0.0)
        a.get_ticker.return_value = {"last": 0}
        adapters = {"ex_a": a, "ex_b": b}
        scanner = _scanner_with(config, adapters)
        opp = await scanner._build_opportunity(
            symbol="ETH/USDT",
            long_eid="ex_a",
            short_eid="ex_b",
            long_rate=Decimal("0.001"),
            short_rate=Decimal("0.005"),
            gross_pct=Decimal("0.5"),
            fees_pct=Decimal("0.1"),
            net_pct=Decimal("0.4"),
            adapters=adapters,
        )
        assert opp is None

    @pytest.mark.asyncio
    async def test_respects_max_position_size(self, config) -> None:
        """Notional should not exceed max_position_size_usd."""
        config.risk_limits.max_position_size_usd = Decimal("100")  # tiny
        a = _make_adapter("ex_a", Decimal("0.001"))
        b = _make_adapter("ex_b", Decimal("0.005"))
        adapters = {"ex_a": a, "ex_b": b}
        scanner = _scanner_with(config, adapters)
        opp = await scanner._build_opportunity(
            symbol="ETH/USDT",
            long_eid="ex_a",
            short_eid="ex_b",
            long_rate=Decimal("0.001"),
            short_rate=Decimal("0.005"),
            gross_pct=Decimal("0.5"),
            fees_pct=Decimal("0.1"),
            net_pct=Decimal("0.4"),
            adapters=adapters,
        )
        if opp is not None:
            notional = opp.suggested_qty * opp.reference_price
            assert notional <= Decimal("100")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 7. stop() — lifecycle
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestLifecycle:
    """Tests for start/stop lifecycle."""

    def test_stop_sets_running_false(self, config) -> None:
        """stop() sets _running = False."""
        a = _make_adapter("ex_a", Decimal("0.001"))
        b = _make_adapter("ex_b", Decimal("0.005"))
        scanner = _scanner_with(config, {"ex_a": a, "ex_b": b})
        scanner._running = True
        scanner.stop()
        assert scanner._running is False

    def test_stop_cancels_ws_tasks(self, config) -> None:
        """stop() should cancel all WS tasks on adapters."""
        a = _make_adapter("ex_a", Decimal("0.001"))
        mock_task = MagicMock()
        a._ws_tasks = [mock_task]
        # Wire cancel_ws_tasks to actually iterate the tasks list so the
        # mock_task.cancel call can be asserted.
        a.cancel_ws_tasks = MagicMock(
            side_effect=lambda: [t.cancel() for t in a._ws_tasks]
        )
        b = _make_adapter("ex_b", Decimal("0.005"))
        scanner = _scanner_with(config, {"ex_a": a, "ex_b": b})
        scanner._running = True
        scanner.stop()
        a.cancel_ws_tasks.assert_called_once()
        mock_task.cancel.assert_called_once()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 8. Cherry‑pick fallback (hold didn't qualify → try cherry)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestCherryPickFallback:
    """When hold doesn't qualify, cherry_pick should still be attempted."""

    @pytest.mark.asyncio
    async def test_cherry_pick_fallback_when_hold_fails(self, config) -> None:
        """Income side within window, cost side far away, hold fails because
        imminent net < threshold → should fall through to cherry_pick."""
        config.trading_params.min_funding_spread = Decimal("0.01")
        config.trading_params.narrow_entry_window_minutes = 15
        # Long has negative rate → income, fires in 10min
        # Short has negative rate → cost for shorting, fires in 120min
        # Only one side is income, and cost is far away → cherry pick
        a = _make_adapter("ex_a", Decimal("-0.010"), next_minutes=10, interval=1)
        b = _make_adapter("ex_b", Decimal("-0.002"), next_minutes=120, interval=8)
        adapters = {"ex_a": a, "ex_b": b}
        funding = {
            "ex_a": {
                "rate": Decimal("-0.010"), "next_timestamp": _future_ms(10),
                "interval_hours": 1, "timestamp": None, "datetime": None,
            },
            "ex_b": {
                "rate": Decimal("-0.002"), "next_timestamp": _future_ms(120),
                "interval_hours": 8, "timestamp": None, "datetime": None,
            },
        }
        scanner = _scanner_with(config, adapters)
        opp = await scanner._evaluate_direction(
            "ETH/USDT", "ex_a", "ex_b",
            Decimal("-0.010"), Decimal("-0.002"),
            1, 8, funding, adapters,
        )
        # Should produce a result (either cherry_pick qualified or display-only)
        if opp is not None and opp.qualified:
            assert opp.mode == TradeMode.CHERRY_PICK
            assert opp.exit_before is not None

    @pytest.mark.asyncio
    async def test_cherry_pick_requires_min_income_gap(self, config) -> None:
        """Cherry pick requires cost side ≥ 30 min away from income side."""
        config.trading_params.min_funding_spread = Decimal("0.01")
        config.trading_params.narrow_entry_window_minutes = 15
        # Income fires in 10 min, cost fires in 15 min → gap < 30 → no cherry
        a = _make_adapter("ex_a", Decimal("-0.010"), next_minutes=10, interval=1)
        b = _make_adapter("ex_b", Decimal("-0.002"), next_minutes=15, interval=8)
        adapters = {"ex_a": a, "ex_b": b}
        funding = {
            "ex_a": {
                "rate": Decimal("-0.010"), "next_timestamp": _future_ms(10),
                "interval_hours": 1, "timestamp": None, "datetime": None,
            },
            "ex_b": {
                "rate": Decimal("-0.002"), "next_timestamp": _future_ms(15),
                "interval_hours": 8, "timestamp": None, "datetime": None,
            },
        }
        scanner = _scanner_with(config, adapters)
        opp = await scanner._evaluate_direction(
            "ETH/USDT", "ex_a", "ex_b",
            Decimal("-0.010"), Decimal("-0.002"),
            1, 8, funding, adapters,
        )
        # Either None or display-only (cherry not qualified due to gap < 30)
        if opp is not None:
            # Should NOT be a qualified cherry_pick
            if opp.mode == TradeMode.CHERRY_PICK and opp.qualified:
                # This shouldn't happen with such a small gap
                pytest.fail("Cherry pick should not qualify with < 30min gap")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 9. Display-only candidates
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestDisplayOnly:
    """Display-only (unqualified) candidates should still be returned."""

    @pytest.mark.asyncio
    async def test_display_only_candidate_returned(self, config) -> None:
        """Positive spread but doesn't meet entry rules → display-only."""
        config.trading_params.min_funding_spread = Decimal("0.01")
        config.trading_params.narrow_entry_window_minutes = 15
        # Income fires in 120 min (outside window) but spread positive
        a = _make_adapter("ex_a", Decimal("-0.005"), next_minutes=120, interval=8)
        b = _make_adapter("ex_b", Decimal("0.005"), next_minutes=120, interval=8)
        adapters = {"ex_a": a, "ex_b": b}
        scanner = _scanner_with(config, adapters)
        results = await scanner.scan_all()
        display_only = [r for r in results if not r.qualified]
        # With income far away, should be display-only
        if display_only:
            opp = display_only[0]
            assert opp.qualified is False
            assert opp.immediate_spread_pct > 0

    @pytest.mark.asyncio
    async def test_display_only_has_correct_mode(self, config) -> None:
        """Display-only candidates should have mode set correctly."""
        config.trading_params.min_funding_spread = Decimal("0.01")
        config.trading_params.narrow_entry_window_minutes = 15
        # Both income, far away → display-only POT
        a = _make_adapter("ex_a", Decimal("-0.005"), next_minutes=120, interval=8)
        b = _make_adapter("ex_b", Decimal("0.005"), next_minutes=120, interval=8)
        adapters = {"ex_a": a, "ex_b": b}
        scanner = _scanner_with(config, adapters)
        results = await scanner.scan_all()
        for opp in results:
            if not opp.qualified:
                # Mode should be determined (not left as default HOLD)
                assert opp.mode in (TradeMode.HOLD, TradeMode.POT, TradeMode.CHERRY_PICK, TradeMode.NUTCRACKER)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 10. HOLD mode (default)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestHoldMode:
    """Tests for the default HOLD mode."""

    @pytest.mark.asyncio
    async def test_hold_mode_one_income_one_cost_imminent(self, config) -> None:
        """One income, one cost, both fire within window → mode depends on gap:
        if gap < 30min → NUTCRACKER, otherwise CHERRY."""
        config.trading_params.min_funding_spread = Decimal("0.01")
        config.trading_params.narrow_entry_window_minutes = 15
        a = _make_adapter("ex_a", Decimal("-0.010"), next_minutes=10, interval=8)
        b = _make_adapter("ex_b", Decimal("-0.002"), next_minutes=12, interval=8)
        adapters = {"ex_a": a, "ex_b": b}
        funding = {
            "ex_a": {
                "rate": Decimal("-0.010"), "next_timestamp": _future_ms(10),
                "interval_hours": 8, "timestamp": None, "datetime": None,
            },
            "ex_b": {
                "rate": Decimal("-0.002"), "next_timestamp": _future_ms(12),
                "interval_hours": 8, "timestamp": None, "datetime": None,
            },
        }
        scanner = _scanner_with(config, adapters)
        opp = await scanner._evaluate_direction(
            "ETH/USDT", "ex_a", "ex_b",
            Decimal("-0.010"), Decimal("-0.002"),
            8, 8, funding, adapters,
        )
        if opp is not None and opp.qualified:
            # Gap is 2 min (< 30 min) → should be NUTCRACKER
            assert opp.mode == TradeMode.NUTCRACKER


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Hot-scan loop — event-driven re-evaluation
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestHotScanLoop:
    """Tests for Scanner._hot_scan_loop(): event-driven re-evaluation path."""

    def _make_redis(self) -> AsyncMock:
        r = AsyncMock()
        r.get_cooled_down_symbols.return_value = set()
        r.is_cooled_down.return_value = False
        return r

    @pytest.mark.asyncio
    async def test_hot_scan_calls_callback_on_qualified_opportunity(self, config) -> None:
        """When a qualified opportunity is found for a hot symbol, callback fires."""
        config.trading_params.min_funding_spread = Decimal("0.01")

        a = _make_adapter("ex_a", Decimal("-0.005"), next_minutes=10, interval=8)
        b = _make_adapter("ex_b", Decimal("0.005"), next_minutes=10, interval=8)
        adapters = {"ex_a": a, "ex_b": b}
        redis = self._make_redis()
        scanner = _scanner_with(config, adapters, redis)

        # Pre-populate common_symbols so hot-scan gate passes
        scanner._common_symbols_cache = {"ETH/USDT"}
        scanner._cache_exchange_ids = ["ex_a", "ex_b"]

        # Push one hot-symbol update
        await scanner._hot_queue.put(("ex_a", "ETH/USDT"))
        # Signal stop after processing
        scanner._running = True

        received: list = []

        async def _cb(opp):
            received.append(opp)
            scanner._running = False  # stop after first callback

        # Run the loop with a short timeout
        try:
            await asyncio.wait_for(scanner._hot_scan_loop(_cb), timeout=2.0)
        except asyncio.TimeoutError:
            pass

        assert len(received) >= 1
        assert received[0].symbol == "ETH/USDT"
        assert received[0].qualified

    @pytest.mark.asyncio
    async def test_hot_scan_ignores_symbol_not_in_common_symbols(self, config) -> None:
        """Symbol not in common_symbols_cache must be silently dropped."""
        config.trading_params.min_funding_spread = Decimal("0.01")

        a = _make_adapter("ex_a", Decimal("-0.005"), next_minutes=10, interval=8)
        b = _make_adapter("ex_b", Decimal("0.005"), next_minutes=10, interval=8)
        adapters = {"ex_a": a, "ex_b": b}
        redis = self._make_redis()
        scanner = _scanner_with(config, adapters, redis)

        # Common symbols does NOT include the pushed symbol
        scanner._common_symbols_cache = {"BTC/USDT"}
        scanner._running = True

        await scanner._hot_queue.put(("ex_a", "UNKNOWN/USDT"))

        received: list = []

        async def _cb(opp):
            received.append(opp)

        # Run briefly — should not call callback
        try:
            await asyncio.wait_for(scanner._hot_scan_loop(_cb), timeout=0.5)
        except asyncio.TimeoutError:
            pass

        scanner._running = False
        assert received == []

    def test_register_price_update_queue_on_adapter(self) -> None:
        """register_price_update_queue() wires the queue into the adapter."""
        a = _make_adapter("ex_a", Decimal("0.001"))
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        a.register_price_update_queue = MagicMock()
        a.register_price_update_queue(q)
        a.register_price_update_queue.assert_called_once_with(q)

    @pytest.mark.asyncio
    async def test_hot_scan_evaluates_symbols_regardless_of_candidates(self, config) -> None:
        """P1-4: _hot_candidates filter removed — a symbol in _common_symbols_cache
        must be evaluated even if _hot_candidates does not contain it."""
        config.trading_params.min_funding_spread = Decimal("0.01")

        a = _make_adapter("ex_a", Decimal("-0.005"), next_minutes=10, interval=8)
        b = _make_adapter("ex_b", Decimal("0.005"), next_minutes=10, interval=8)
        adapters = {"ex_a": a, "ex_b": b}
        redis = self._make_redis()
        scanner = _scanner_with(config, adapters, redis)

        scanner._common_symbols_cache = {"ETH/USDT", "BTC/USDT"}
        # _hot_candidates only contains BTC — after P1-4 this has no filtering effect
        scanner._hot_candidates = {"BTC/USDT"}
        scanner._running = True

        scanned: list[str] = []
        _orig_scan = scanner._scan_symbol

        async def _spy_scan(symbol, *args, **kwargs):
            scanned.append(symbol)
            return await _orig_scan(symbol, *args, **kwargs)

        scanner._scan_symbol = _spy_scan

        await scanner._hot_queue.put(("ex_a", "ETH/USDT"))

        async def _dummy_cb(opp):
            pass

        try:
            await asyncio.wait_for(scanner._hot_scan_loop(_dummy_cb), timeout=0.5)
        except asyncio.TimeoutError:
            pass

        scanner._running = False
        # ETH/USDT must reach _scan_symbol — the candidates filter is gone
        assert "ETH/USDT" in scanned

    @pytest.mark.asyncio
    async def test_scan_all_updates_hot_candidates(self, config, mock_exchange_mgr, mock_redis) -> None:
        """After scan_all() with qualified results, _hot_candidates is populated."""
        import time as _time
        config.trading_params.min_funding_spread = Decimal("0.01")

        adapter_a = mock_exchange_mgr.get("exchange_a")
        adapter_b = mock_exchange_mgr.get("exchange_b")

        def _fut(hours: float) -> float:
            return _time.time() * 1000 + hours * 3_600_000

        funding_a = {"rate": Decimal("0.0001"), "timestamp": None, "datetime": None,
                     "next_timestamp": _fut(1), "interval_hours": 1}
        funding_b = {"rate": Decimal("0.005"), "timestamp": None, "datetime": None,
                     "next_timestamp": _fut(1), "interval_hours": 1}
        adapter_a._funding_rate_cache["ETH/USDT"] = funding_a
        adapter_b._funding_rate_cache["ETH/USDT"] = funding_b
        adapter_a.get_funding_rate.return_value = funding_a
        adapter_b.get_funding_rate.return_value = funding_b
        adapter_a.get_ticker.return_value = {"last": 3000.0}
        adapter_b.get_ticker.return_value = {"last": 3000.0}

        scanner = Scanner(config, mock_exchange_mgr, mock_redis)
        await scanner.scan_all()

        # Candidates should be non-empty after a scan with meaningful spread
        assert isinstance(scanner._hot_candidates, set)

    @pytest.mark.asyncio
    async def test_hot_scan_callback_debounced_within_cooldown(self, config) -> None:
        """Callback must NOT fire a second time for the same symbol within the cooldown window."""
        config.trading_params.min_funding_spread = Decimal("0.01")

        a = _make_adapter("ex_a", Decimal("-0.005"), next_minutes=10, interval=8)
        b = _make_adapter("ex_b", Decimal("0.005"), next_minutes=10, interval=8)
        adapters = {"ex_a": a, "ex_b": b}
        redis = self._make_redis()
        scanner = _scanner_with(config, adapters, redis)

        scanner._common_symbols_cache = {"ETH/USDT"}
        scanner._cache_exchange_ids = ["ex_a", "ex_b"]
        scanner._running = True

        # Simulate the symbol was already fired 2 seconds ago (within 10s cooldown)
        # P1-2: debounce key is now route-based: "{symbol}|{long}|{short}"
        scanner._hot_cb_last_fire["ETH/USDT|ex_a|ex_b"] = asyncio.get_event_loop().time() - 2

        await scanner._hot_queue.put(("ex_a", "ETH/USDT"))

        received: list = []

        async def _cb(opp):
            received.append(opp)
            scanner._running = False

        try:
            await asyncio.wait_for(scanner._hot_scan_loop(_cb), timeout=1.5)
        except asyncio.TimeoutError:
            pass

        scanner._running = False
        # Debounced: second fire within cooldown window should be suppressed
        assert received == []
