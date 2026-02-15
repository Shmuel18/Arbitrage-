"""Tests for funding-rate calculator."""

from decimal import Decimal

from src.discovery.calculator import (
    calculate_fees,
    calculate_funding_edge,
    calculate_funding_spread,
)


class TestCalculateFundingSpread:
    """Tests for the PRIMARY funding-spread function."""

    def test_positive_spread_short_receives_long_pays(self):
        """Positive spread when short_rate > long_rate (we receive on short, pay on long)."""
        result = calculate_funding_spread(
            long_rate=Decimal("0.0001"),
            short_rate=Decimal("0.0005"),
        )
        assert result["funding_spread_pct"] > 0

    def test_formula_matches_user_spec(self):
        """Verify: spread = (-long_rate) + short_rate, in percent."""
        long_rate = Decimal("0.0003")
        short_rate = Decimal("0.0007")
        result = calculate_funding_spread(long_rate, short_rate)
        expected = (-long_rate + short_rate) * Decimal("100")
        assert result["funding_spread_pct"] == expected

    def test_negative_spread_when_long_rate_higher(self):
        result = calculate_funding_spread(
            long_rate=Decimal("0.0005"),
            short_rate=Decimal("0.0001"),
        )
        assert result["funding_spread_pct"] < 0

    def test_zero_spread_when_rates_equal(self):
        result = calculate_funding_spread(
            long_rate=Decimal("0.0003"),
            short_rate=Decimal("0.0003"),
        )
        assert result["funding_spread_pct"] == 0

    def test_negative_long_rate_increases_spread(self):
        """When long_rate is negative, longs receive → more profit for us."""
        result = calculate_funding_spread(
            long_rate=Decimal("-0.0002"),
            short_rate=Decimal("0.0003"),
        )
        # -(-0.0002) + 0.0003 = 0.0005 → 0.05%
        assert result["funding_spread_pct"] == Decimal("0.05")

    def test_different_intervals_normalized(self):
        """Bybit 1h vs Binance 8h: same rate should produce zero spread."""
        result = calculate_funding_spread(
            long_rate=Decimal("0.004"),
            short_rate=Decimal("0.0005"),
            long_interval_hours=8,
            short_interval_hours=1,
        )
        assert result["funding_spread_pct"] == 0

    def test_1h_short_makes_spread_bigger(self):
        """Short on 1h exchange: normalized rate is 8x, so we receive more."""
        result_8h = calculate_funding_spread(
            long_rate=Decimal("0.0001"),
            short_rate=Decimal("0.0005"),
            long_interval_hours=8,
            short_interval_hours=8,
        )
        result_1h = calculate_funding_spread(
            long_rate=Decimal("0.0001"),
            short_rate=Decimal("0.0005"),
            long_interval_hours=8,
            short_interval_hours=1,
        )
        assert result_1h["funding_spread_pct"] > result_8h["funding_spread_pct"]

    def test_annualized_is_1095x(self):
        result = calculate_funding_spread(
            long_rate=Decimal("0.0001"),
            short_rate=Decimal("0.0005"),
        )
        assert result["annualized_pct"] == result["funding_spread_pct"] * 1095

    def test_pnl_breakdown(self):
        """Verify long_pnl_pct and short_pnl_pct are correct."""
        result = calculate_funding_spread(
            long_rate=Decimal("0.0002"),
            short_rate=Decimal("0.0005"),
        )
        # long_pnl = -0.0002 → -0.02%
        assert result["long_pnl_pct"] == Decimal("-0.02")
        # short_pnl = +0.0005 → +0.05%
        assert result["short_pnl_pct"] == Decimal("0.05")


class TestCalculateFundingEdgeBackwardCompat:
    """Backward compatibility: calculate_funding_edge delegates to calculate_funding_spread."""

    def test_positive_edge_when_short_rate_higher(self):
        result = calculate_funding_edge(
            long_rate=Decimal("0.0001"),
            short_rate=Decimal("0.0005"),
        )
        assert result["edge_pct"] > 0

    def test_negative_edge_when_long_rate_higher(self):
        result = calculate_funding_edge(
            long_rate=Decimal("0.0005"),
            short_rate=Decimal("0.0001"),
        )
        assert result["edge_pct"] < 0

    def test_zero_edge_when_rates_equal(self):
        result = calculate_funding_edge(
            long_rate=Decimal("0.0003"),
            short_rate=Decimal("0.0003"),
        )
        assert result["edge_pct"] == 0

    def test_edge_matches_spread(self):
        """edge_pct from old function == funding_spread_pct from new function."""
        args = (Decimal("0.0001"), Decimal("0.0005"))
        edge = calculate_funding_edge(*args)
        spread = calculate_funding_spread(*args)
        assert edge["edge_pct"] == spread["funding_spread_pct"]

    def test_different_intervals_normalized(self):
        result = calculate_funding_edge(
            long_rate=Decimal("0.004"),
            short_rate=Decimal("0.0005"),
            long_interval_hours=8,
            short_interval_hours=1,
        )
        assert result["edge_pct"] == 0

    def test_annualized_is_1095x_daily(self):
        result = calculate_funding_edge(
            long_rate=Decimal("0.0001"),
            short_rate=Decimal("0.0005"),
        )
        assert result["annualized_pct"] == result["edge_pct"] * 1095

    def test_negative_rates(self):
        result = calculate_funding_edge(
            long_rate=Decimal("-0.0002"),
            short_rate=Decimal("0.0003"),
        )
        assert result["edge_pct"] > 0


class TestCalculateFees:
    def test_round_trip_fees(self):
        fees = calculate_fees(
            long_taker_fee=Decimal("0.0005"),
            short_taker_fee=Decimal("0.0005"),
        )
        assert fees == Decimal("0.20")

    def test_asymmetric_fees(self):
        fees = calculate_fees(
            long_taker_fee=Decimal("0.0004"),
            short_taker_fee=Decimal("0.0006"),
        )
        assert fees == Decimal("0.20")

    def test_zero_fees(self):
        fees = calculate_fees(Decimal("0"), Decimal("0"))
        assert fees == Decimal("0")
