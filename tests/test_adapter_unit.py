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
        with patch("src.exchanges.adapter._time") as mock_time:
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
        with patch("src.exchanges.adapter._time") as mock_time:
            mock_time.time.return_value = now_ms / 1000
            a._update_funding_cache("BTC/USDT", data)
        assert a._funding_rate_cache["BTC/USDT"]["next_timestamp"] == future_ms


def _time_ms() -> float:
    return time.time() * 1000


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

    def test_all_returns_copy(self):
        mgr = ExchangeManager()
        mgr.register("exchange_a", {})
        snapshot = mgr.all()
        snapshot["injected"] = MagicMock()
        assert "injected" not in mgr._adapters

    def test_get_raises_on_unknown(self):
        mgr = ExchangeManager()
        with pytest.raises(KeyError):
            mgr.get("ghost")
