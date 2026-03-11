"""
Unit tests for src.exchanges.adapter — pure/stateless methods only.

No real ccxt connection required; tests manipulate internal cache
attributes directly to isolate the logic under test.
"""

from __future__ import annotations

import time
from decimal import Decimal
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest

from src.core.contracts import InstrumentSpec, OrderSide
from src.exchanges.adapter import ExchangeAdapter, ExchangeManager


# ── Helpers ──────────────────────────────────────────────────────

def _adapter(exchange_id: str = "test_ex", cfg: dict | None = None) -> ExchangeAdapter:
    """Create an ExchangeAdapter without a real ccxt connection."""
    return ExchangeAdapter(exchange_id, cfg or {})


def _make_spec(
    symbol: str = "BTC/USDT",
    taker_fee: str = "0.0005",
    lot_size: str = "0.001",
) -> InstrumentSpec:
    return InstrumentSpec(
        exchange="test_ex",
        symbol=symbol,
        base="BTC",
        quote="USDT",
        contract_size=Decimal("1"),
        tick_size=Decimal("0.01"),
        lot_size=Decimal(lot_size),
        min_notional=Decimal("5"),
        maker_fee=Decimal("0.0002"),
        taker_fee=Decimal(taker_fee),
    )


# ── symbols / markets properties ─────────────────────────────────

class TestProperties:
    def test_symbols_empty_before_connect(self):
        a = _adapter()
        assert a.symbols == []

    def test_symbols_returns_cached_list(self):
        a = _adapter()
        a._symbols_list = ["BTC/USDT", "ETH/USDT"]
        assert a.symbols == ["BTC/USDT", "ETH/USDT"]

    def test_markets_empty_when_not_connected(self):
        a = _adapter()
        assert a.markets == {}

    def test_markets_returns_copy_of_exchange_markets(self):
        a = _adapter()
        mock_xch = MagicMock()
        mock_xch.markets = {"BTC/USDT": {"active": True}}
        a._exchange = mock_xch
        m = a.markets
        assert "BTC/USDT" in m
        # Must be a copy — mutation should not affect internal state
        m["NEW/USDT"] = {}
        assert "NEW/USDT" not in a._exchange.markets


# ── get_cached_instrument_spec ────────────────────────────────────

class TestGetCachedSpec:
    def test_returns_none_when_empty(self):
        a = _adapter()
        assert a.get_cached_instrument_spec("BTC/USDT") is None

    def test_returns_spec_when_cached(self):
        a = _adapter()
        spec = _make_spec()
        a._instrument_cache["BTC/USDT"] = spec
        assert a.get_cached_instrument_spec("BTC/USDT") is spec

    def test_independent_symbols(self):
        a = _adapter()
        a._instrument_cache["ETH/USDT"] = _make_spec("ETH/USDT")
        assert a.get_cached_instrument_spec("BTC/USDT") is None


# ── _resolve_symbol / _normalize_symbol ──────────────────────────

class TestSymbolMapping:
    def test_resolve_identity_when_no_mapping(self):
        a = _adapter()
        assert a._resolve_symbol("BTC/USDT:USDT") == "BTC/USDT:USDT"

    def test_resolve_maps_to_exchange_symbol(self):
        a = _adapter()
        a._symbol_map = {"BTC/USDT:USDT": "BTC/USD:USD"}
        assert a._resolve_symbol("BTC/USDT:USDT") == "BTC/USD:USD"

    def test_normalize_identity_when_no_mapping(self):
        a = _adapter()
        assert a._normalize_symbol("BTC/USDT:USDT") == "BTC/USDT:USDT"

    def test_normalize_reverses_resolve(self):
        a = _adapter()
        a._symbol_map = {"BTC/USDT:USDT": "BTC/USD:USD"}
        # normalize should go USD → USDT
        assert a._normalize_symbol("BTC/USD:USD") == "BTC/USDT:USDT"

    def test_normalize_builds_reverse_map_once(self):
        a = _adapter()
        a._symbol_map = {"X/USDT": "X/USD"}
        a._normalize_symbol("X/USD")
        a._normalize_symbol("X/USD")  # second call should use cached reverse map
        assert hasattr(a, "_reverse_symbol_map")


# ── get_mark_price ────────────────────────────────────────────────

class TestGetMarkPrice:
    def test_returns_none_when_no_cache(self):
        a = _adapter()
        assert a.get_mark_price("BTC/USDT") is None

    def test_returns_mark_price_from_funding_cache(self):
        a = _adapter()
        a._funding_rate_cache["BTC/USDT"] = {"markPrice": 50000.0, "rate": Decimal("0.0001")}
        assert a.get_mark_price("BTC/USDT") == 50000.0

    def test_falls_back_to_index_price(self):
        a = _adapter()
        a._funding_rate_cache["BTC/USDT"] = {"markPrice": None, "indexPrice": 49900.0, "rate": Decimal("0")}
        assert a.get_mark_price("BTC/USDT") == 49900.0

    def test_falls_back_to_price_cache(self):
        a = _adapter()
        a._price_cache["BTC/USDT"] = 51000.0
        assert a.get_mark_price("BTC/USDT") == 51000.0

    def test_funding_cache_preferred_over_price_cache(self):
        a = _adapter()
        a._funding_rate_cache["BTC/USDT"] = {"markPrice": 50000.0, "rate": Decimal("0")}
        a._price_cache["BTC/USDT"] = 99999.0
        assert a.get_mark_price("BTC/USDT") == 50000.0


# ── get_funding_rate_cached ───────────────────────────────────────

class TestGetFundingRateCached:
    def test_returns_none_when_empty(self):
        a = _adapter()
        assert a.get_funding_rate_cached("BTC/USDT") is None

    def test_returns_cached_entry(self):
        a = _adapter()
        entry = {"rate": Decimal("0.0003"), "next_timestamp": 9999999, "interval_hours": 8}
        a._funding_rate_cache["BTC/USDT"] = entry
        result = a.get_funding_rate_cached("BTC/USDT")
        assert result["rate"] == Decimal("0.0003")

    def test_returns_none_for_unknown_symbol(self):
        a = _adapter()
        a._funding_rate_cache["ETH/USDT"] = {"rate": Decimal("0.0001")}
        assert a.get_funding_rate_cached("BTC/USDT") is None


# ── update_taker_fee_from_fill ────────────────────────────────────

class TestUpdateTakerFeeFromFill:
    def test_updates_taker_fee_in_cache(self):
        a = _adapter()
        a._instrument_cache["BTC/USDT"] = _make_spec(taker_fee="0.0005")
        fill = {"fee": {"rate": "0.00048", "cost": "1.2", "currency": "USDT"}}
        a.update_taker_fee_from_fill("BTC/USDT", fill)
        updated = a._instrument_cache["BTC/USDT"]
        assert updated.taker_fee == Decimal("0.00048")

    def test_no_update_when_symbol_not_cached(self):
        a = _adapter()
        fill = {"fee": {"rate": "0.00048"}}
        a.update_taker_fee_from_fill("BTC/USDT", fill)  # should not raise
        assert "BTC/USDT" not in a._instrument_cache

    def test_no_update_when_fee_missing(self):
        a = _adapter()
        a._instrument_cache["BTC/USDT"] = _make_spec(taker_fee="0.0005")
        a.update_taker_fee_from_fill("BTC/USDT", {})  # no fee key
        assert a._instrument_cache["BTC/USDT"].taker_fee == Decimal("0.0005")

    def test_no_update_when_rate_zero(self):
        a = _adapter()
        a._instrument_cache["BTC/USDT"] = _make_spec(taker_fee="0.0005")
        fill = {"fee": {"rate": "0"}}
        a.update_taker_fee_from_fill("BTC/USDT", fill)
        assert a._instrument_cache["BTC/USDT"].taker_fee == Decimal("0.0005")

    def test_no_update_when_same_rate(self):
        a = _adapter()
        a._instrument_cache["BTC/USDT"] = _make_spec(taker_fee="0.0005")
        fill = {"fee": {"rate": "0.0005"}}
        original_spec = a._instrument_cache["BTC/USDT"]
        a.update_taker_fee_from_fill("BTC/USDT", fill)
        # Object identity check — should be same object (no replacement)
        assert a._instrument_cache["BTC/USDT"] is original_spec

    def test_reads_rate_from_fees_list_when_fee_dict_missing_rate(self):
        a = _adapter()
        a._instrument_cache["BTC/USDT"] = _make_spec(taker_fee="0.0005")
        fill = {
            "fee": {"cost": "1.0"},  # no rate key
            "fees": [{"rate": "0.00046", "cost": "1.0", "currency": "USDT"}],
        }
        a.update_taker_fee_from_fill("BTC/USDT", fill)
        assert a._instrument_cache["BTC/USDT"].taker_fee == Decimal("0.00046")

    def test_ignores_non_dict_fill(self):
        a = _adapter()
        a._instrument_cache["BTC/USDT"] = _make_spec()
        a.update_taker_fee_from_fill("BTC/USDT", "not-a-dict")  # should not raise


# ── _update_funding_cache ─────────────────────────────────────────

class TestUpdateFundingCache:
    def test_stores_rate_and_interval(self):
        a = _adapter()
        now_ms = _time_ms()
        data = {
            "fundingRate": 0.0003,
            "fundingTimestamp": now_ms + 28_800_000,  # 8h from now
            "interval": "8h",  # CCXT normalised field; avoids exchange.markets fallback
        }
        with patch("src.exchanges._funding_mixin._time") as mock_time:
            mock_time.time.return_value = now_ms / 1000
            a._update_funding_cache("BTC/USDT", data)
        cached = a._funding_rate_cache.get("BTC/USDT")
        assert cached is not None
        assert cached["rate"] == Decimal("0.0003")

    def test_skips_insane_rate(self):
        """Rates exceeding MAX_SANE_RATE (0.10) are discarded."""
        a = _adapter()
        a._update_funding_cache("BTC/USDT", {"fundingRate": 0.5})
        assert "BTC/USDT" not in a._funding_rate_cache

    def test_skips_negative_insane_rate(self):
        a = _adapter()
        a._update_funding_cache("BTC/USDT", {"fundingRate": -0.15})
        assert "BTC/USDT" not in a._funding_rate_cache

    def test_uses_next_funding_timestamp_over_funding_timestamp(self):
        a = _adapter()
        now_ms = _time_ms()
        future_ms = now_ms + 3_600_000  # 1h from now
        data = {
            "fundingRate": 0.0001,
            "fundingTimestamp": now_ms - 5000,   # past — should not be preferred
            "nextFundingTimestamp": future_ms,
            "interval": "1h",  # CCXT normalised; avoids exchange.markets fallback
        }
        with patch("src.exchanges._funding_mixin._time") as mock_time:
            mock_time.time.return_value = now_ms / 1000
            a._update_funding_cache("BTC/USDT", data)
        assert a._funding_rate_cache["BTC/USDT"]["next_timestamp"] == future_ms


def _time_ms() -> float:
    return time.time() * 1000


# ── fetch_fill_details_from_trades ────────────────────────────────

class TestFetchFillDetails:
    """Tests for the new fetch_fill_details_from_trades method."""

    @pytest.mark.asyncio
    async def test_returns_exact_fee_and_price(self):
        """When myTrades returns fills with fee data, extract exact values."""
        import asyncio

        a = _adapter()
        mock_xch = MagicMock()
        a._exchange = mock_xch
        a._rest_semaphore = asyncio.Semaphore(10)

        mock_trades = [
            {
                "order": "order123",
                "price": 0.05512,
                "amount": 1000,
                "fee": {"cost": 0.05512, "currency": "USDT"},
                "timestamp": _time_ms(),
            },
        ]

        async def _mock_fetch_my_trades(*args, **kwargs):
            return mock_trades

        mock_xch.fetch_my_trades = _mock_fetch_my_trades

        result = await a.fetch_fill_details_from_trades("TEST/USDT:USDT", "order123")
        assert result is not None
        assert result["avg_price"] == Decimal("0.05512")
        assert result["total_fee"] == Decimal("0.05512")
        assert result["filled"] == Decimal("1000")

    @pytest.mark.asyncio
    async def test_converts_base_currency_fee(self):
        """Fees in base currency should be converted to USDT using fill price."""
        import asyncio

        a = _adapter()
        mock_xch = MagicMock()
        a._exchange = mock_xch
        a._rest_semaphore = asyncio.Semaphore(10)

        mock_trades = [
            {
                "order": "order456",
                "price": 100.0,
                "amount": 10,
                "fee": {"cost": 0.01, "currency": "ETH"},
                "timestamp": _time_ms(),
            },
        ]

        async def _mock_fetch_my_trades(*args, **kwargs):
            return mock_trades

        mock_xch.fetch_my_trades = _mock_fetch_my_trades

        result = await a.fetch_fill_details_from_trades("ETH/USDT:USDT", "order456")
        assert result is not None
        # 0.01 ETH × $100 = $1.00
        assert result["total_fee"] == Decimal("0.01") * Decimal("100.0")

    @pytest.mark.asyncio
    async def test_returns_none_when_no_trades(self):
        """Returns None when no trades are found."""
        import asyncio

        a = _adapter()
        mock_xch = MagicMock()
        a._exchange = mock_xch
        a._rest_semaphore = asyncio.Semaphore(10)

        async def _mock_fetch_my_trades(*args, **kwargs):
            return []

        mock_xch.fetch_my_trades = _mock_fetch_my_trades

        result = await a.fetch_fill_details_from_trades("BTC/USDT:USDT", "order789")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_api_error(self):
        """Returns None gracefully when API call fails."""
        import asyncio

        a = _adapter()
        mock_xch = MagicMock()
        a._exchange = mock_xch
        a._rest_semaphore = asyncio.Semaphore(10)

        async def _mock_fetch_my_trades(*args, **kwargs):
            raise Exception("Network timeout")

        mock_xch.fetch_my_trades = _mock_fetch_my_trades

        result = await a.fetch_fill_details_from_trades("BTC/USDT:USDT", "order999")
        assert result is None

    @pytest.mark.asyncio
    async def test_multiple_fills_aggregated(self):
        """Multiple partial fills for same order should be aggregated."""
        import asyncio

        a = _adapter()
        mock_xch = MagicMock()
        a._exchange = mock_xch
        a._rest_semaphore = asyncio.Semaphore(10)

        mock_trades = [
            {
                "order": "order_multi",
                "price": 50.0,
                "amount": 100,
                "fee": {"cost": 0.025, "currency": "USDT"},
                "timestamp": _time_ms(),
            },
            {
                "order": "order_multi",
                "price": 50.1,
                "amount": 100,
                "fee": {"cost": 0.026, "currency": "USDT"},
                "timestamp": _time_ms(),
            },
        ]

        async def _mock_fetch_my_trades(*args, **kwargs):
            return mock_trades

        mock_xch.fetch_my_trades = _mock_fetch_my_trades

        result = await a.fetch_fill_details_from_trades("DOT/USDT:USDT", "order_multi")
        assert result is not None
        assert result["filled"] == Decimal("200")
        # VWAP: (100*50 + 100*50.1) / 200 = 50.05
        assert result["avg_price"] == Decimal("10010.0") / Decimal("200")
        # Total fee: 0.025 + 0.026 = 0.051
        assert result["total_fee"] == Decimal("0.051")


# ── ExchangeManager ───────────────────────────────────────────────

class TestExchangeManager:
    def test_register_creates_adapter(self):
        mgr = ExchangeManager()
        adapter = mgr.register("binance", {})
        assert isinstance(adapter, ExchangeAdapter)
        assert adapter.exchange_id == "binance"

    def test_get_returns_registered_adapter(self):
        mgr = ExchangeManager()
        registered = mgr.register("bybit", {})
        assert mgr.get("bybit") is registered

    def test_all_returns_all_registered(self):
        mgr = ExchangeManager()
        a = mgr.register("exchange_a", {})
        b = mgr.register("exchange_b", {})
        result = mgr.all()
        assert set(result.keys()) == {"exchange_a", "exchange_b"}
        assert result["exchange_a"] is a

    def test_all_returns_safe_snapshot(self):
        """all() returns a shallow dict copy — safe to iterate across await boundaries.

        Returning a copy (not a live MappingProxyType) means concurrent mutations
        to the internal registry (e.g. verify_all removing a failed adapter) cannot
        raise RuntimeError: dictionary changed size during iteration.
        """
        mgr = ExchangeManager()
        mgr.register("exchange_a", {})
        snapshot = mgr.all()
        # Must be a plain dict, not a live proxy
        assert isinstance(snapshot, dict)
        # Contents should mirror the internal adapters dict
        assert snapshot == mgr._adapters
        # Must be a COPY — mutating the snapshot must not affect the registry
        snapshot["exchange_z"] = None  # type: ignore[assignment]
        assert "exchange_z" not in mgr._adapters

    def test_get_raises_on_unknown(self):
        mgr = ExchangeManager()
        with pytest.raises(KeyError):
            mgr.get("ghost")


# ── MRO structural guards ────────────────────────────────────────────────

class TestExchangeAdapterMRO:
    """Guard against silent method shadowing between ExchangeAdapter mixins.

    Python's C3 linearisation silently resolves name collisions — if two
    mixins define the same method the MRO winner is picked without any
    warning.  These tests make that resolution explicit and will fail
    loudly if a new mixin accidentally shadows an existing one.
    """

    def test_mro_order_is_stable(self):
        """MRO must list mixins in the expected composition order."""
        from src.exchanges._fill_recovery_mixin import _FillRecoveryMixin
        from src.exchanges._funding_cache_mixin import _FundingCacheMixin
        from src.exchanges._funding_mixin import _FundingMixin
        from src.exchanges._lifecycle_mixin import _LifecycleMixin
        from src.exchanges._market_data_mixin import _MarketDataMixin
        from src.exchanges._order_mixin import _OrderMixin

        mro = ExchangeAdapter.__mro__
        mro_names = [c.__name__ for c in mro]

        # Concrete class is first
        assert mro_names[0] == "ExchangeAdapter"
        # Top-level mixins appear before their bases
        assert mro_names.index("_LifecycleMixin") < mro_names.index("object")
        assert mro_names.index("_OrderMixin") < mro_names.index("_FillRecoveryMixin")
        assert mro_names.index("_FundingMixin") < mro_names.index("_FundingCacheMixin")

    def test_no_public_method_shadowed_between_mixins(self):
        """No two sibling mixins define the same public method.

        Shadowing inside a parent–child mixin chain is intentional (an
        override), but two *sibling* mixins sharing a name is almost always
        a copy-paste bug.
        """
        from src.exchanges._fill_recovery_mixin import _FillRecoveryMixin
        from src.exchanges._funding_cache_mixin import _FundingCacheMixin
        from src.exchanges._funding_mixin import _FundingMixin
        from src.exchanges._lifecycle_mixin import _LifecycleMixin
        from src.exchanges._market_data_mixin import _MarketDataMixin
        from src.exchanges._order_mixin import _OrderMixin

        # Sibling mixins at the same composition level (not parent–child)
        siblings = [
            _LifecycleMixin,
            _FundingMixin,
            _MarketDataMixin,
            _OrderMixin,
        ]

        seen: dict[str, str] = {}  # method_name → first mixin that defined it
        conflicts: list[str] = []
        for mixin in siblings:
            # Only methods defined *directly* on this class (not inherited)
            own_methods = {
                name for name, val in vars(mixin).items()
                if callable(val) and not name.startswith("__")
            }
            for name in own_methods:
                if name in seen:
                    conflicts.append(
                        f"{name!r} defined in both {seen[name]} and {mixin.__name__}"
                    )
                else:
                    seen[name] = mixin.__name__

        assert not conflicts, (
            "Silent method shadowing detected between sibling mixins:\n"
            + "\n".join(f"  • {c}" for c in conflicts)
        )

    def test_connect_owned_by_lifecycle_mixin(self):
        """connect() must resolve to _LifecycleMixin, not any other mixin."""
        from src.exchanges._lifecycle_mixin import _LifecycleMixin
        assert ExchangeAdapter.connect is _LifecycleMixin.connect

    def test_place_order_owned_by_order_mixin(self):
        """place_order() must resolve to _OrderMixin."""
        from src.exchanges._order_mixin import _OrderMixin
        assert ExchangeAdapter.place_order is _OrderMixin.place_order
