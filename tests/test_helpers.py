"""
Unit tests for src.execution.helpers — pure, side-effect-free functions.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.core.contracts import TradeRecord, TradeState
from src.execution.helpers import (
    estimate_funding_totals,
    extract_avg_price,
    extract_fee,
)


# ── extract_avg_price ────────────────────────────────────────────

class TestExtractAvgPrice:
    def test_returns_average_key_first(self):
        order = {"average": "50000.5", "price": "49999"}
        assert extract_avg_price(order) == Decimal("50000.5")

    def test_falls_back_to_avg_price(self):
        order = {"avg_price": "51000"}
        assert extract_avg_price(order) == Decimal("51000")

    def test_falls_back_to_price(self):
        order = {"price": "52000"}
        assert extract_avg_price(order) == Decimal("52000")

    def test_falls_back_to_avgPrice_camel_case(self):
        order = {"avgPrice": "53000"}
        assert extract_avg_price(order) == Decimal("53000")

    def test_returns_none_when_no_price_key(self):
        assert extract_avg_price({}) is None
        assert extract_avg_price({"bid": 100, "ask": 101}) is None

    def test_skips_none_values(self):
        order = {"average": None, "price": "54000"}
        assert extract_avg_price(order) == Decimal("54000")

    def test_skips_invalid_then_continues(self):
        order = {"average": "not-a-number", "price": "55000"}
        # "average" fails to parse → falls through to "price"
        assert extract_avg_price(order) == Decimal("55000")

    def test_handles_numeric_value(self):
        order = {"average": 60000.25}
        assert extract_avg_price(order) == Decimal("60000.25")


# ── extract_fee ──────────────────────────────────────────────────

class TestExtractFee:
    def test_usdt_fee_from_fee_dict(self):
        order = {
            "average": "50000",
            "fee": {"cost": "1.5", "currency": "USDT"},
        }
        assert extract_fee(order) == Decimal("1.5")

    def test_fee_in_base_currency_converted_to_usdt(self):
        # Fee is 0.001 BTC at avg price 50 000 → 50 USDT
        order = {
            "average": "50000",
            "fee": {"cost": "0.001", "currency": "BTC"},
        }
        assert extract_fee(order) == Decimal("0.001") * Decimal("50000")

    def test_fees_list_accumulated(self):
        order = {
            "average": "1000",
            "fees": [
                {"cost": "0.5", "currency": "USDT"},
                {"cost": "0.3", "currency": "USDT"},
            ],
        }
        assert extract_fee(order) == Decimal("0.8")

    def test_both_fee_and_fees_summed(self):
        order = {
            "average": "1000",
            "fee": {"cost": "1.0", "currency": "USDT"},
            "fees": [{"cost": "0.5", "currency": "USDT"}],
        }
        assert extract_fee(order) == Decimal("1.5")

    def test_fallback_rate_used_when_no_fee_data(self):
        # filled=0.01 BTC at 50 000 USDT × 0.0005 taker = 0.25 USDT
        order = {"average": "50000", "filled": "0.01"}
        result = extract_fee(order, fallback_rate=Decimal("0.0005"))
        assert result == Decimal("0.01") * Decimal("50000") * Decimal("0.0005")

    def test_fallback_uses_amount_when_filled_zero(self):
        order = {"average": "50000", "filled": "0", "amount": "0.02"}
        result = extract_fee(order, fallback_rate=Decimal("0.001"))
        assert result == Decimal("0.02") * Decimal("50000") * Decimal("0.001")

    def test_returns_zero_when_no_data_and_no_fallback(self):
        assert extract_fee({}) == Decimal("0")

    def test_ignores_none_fee_cost(self):
        order = {
            "average": "1000",
            "fee": {"cost": None, "currency": "USDT"},
        }
        assert extract_fee(order) == Decimal("0")

    def test_usd_variant_currencies_not_converted(self):
        for currency in ("BUSD", "USDC", "USD"):
            order = {
                "average": "99999",
                "fee": {"cost": "2.0", "currency": currency},
            }
            assert extract_fee(order) == Decimal("2.0"), f"Failed for {currency}"


# ── estimate_funding_totals ──────────────────────────────────────

def _make_trade(**overrides) -> TradeRecord:
    defaults = dict(
        trade_id="t1",
        symbol="BTC/USDT",
        state=TradeState.OPEN,
        long_exchange="exchange_a",
        short_exchange="exchange_b",
        long_qty=Decimal("0.1"),
        short_qty=Decimal("0.1"),
        entry_edge_pct=Decimal("0.3"),
        entry_price_long=Decimal("50000"),
        entry_price_short=Decimal("50010"),
        long_funding_rate=Decimal("0.0001"),   # long pays
        short_funding_rate=Decimal("0.0003"),  # short receives
    )
    defaults.update(overrides)
    return TradeRecord(**defaults)


class TestEstimateFundingTotals:
    def test_basic_long_pays_short_receives(self):
        trade = _make_trade()
        paid, received = estimate_funding_totals(trade)
        # long_rate > 0 → paid by long leg
        expected_paid = Decimal("50000") * Decimal("0.1") * Decimal("0.0001")
        # short_rate > 0 → received on short leg
        expected_received = Decimal("50010") * Decimal("0.1") * Decimal("0.0003")
        assert paid == expected_paid
        assert received == expected_received

    def test_negative_long_rate_adds_to_received(self):
        # Isolate long-leg behaviour by zeroing out the short rate
        trade = _make_trade(
            long_funding_rate=Decimal("-0.0002"),
            short_funding_rate=Decimal("0"),
        )
        paid, received = estimate_funding_totals(trade)
        # negative long_rate → long leg receives
        expected_received = Decimal("50000") * Decimal("0.1") * Decimal("0.0002")
        assert paid == Decimal("0")
        assert received == expected_received

    def test_both_rates_positive(self):
        # long pays & short receives
        trade = _make_trade(
            long_funding_rate=Decimal("0.0002"),
            short_funding_rate=Decimal("0.0004"),
        )
        paid, received = estimate_funding_totals(trade)
        assert paid == Decimal("50000") * Decimal("0.1") * Decimal("0.0002")
        assert received == Decimal("50010") * Decimal("0.1") * Decimal("0.0004")

    def test_missing_entry_prices_returns_zeros(self):
        trade = _make_trade(entry_price_long=None, entry_price_short=None)
        paid, received = estimate_funding_totals(trade)
        assert paid == Decimal("0")
        assert received == Decimal("0")

    def test_none_funding_rates_treated_as_zero(self):
        trade = _make_trade(long_funding_rate=None, short_funding_rate=None)
        paid, received = estimate_funding_totals(trade)
        assert paid == Decimal("0")
        assert received == Decimal("0")
