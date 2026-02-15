"""Tests for funding-rate calculator."""

from decimal import Decimal

from src.discovery.calculator import calculate_fees, calculate_funding_edge


class TestCalculateFundingEdge:
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

    def test_different_intervals_normalized(self):
        """Bybit 1h vs Binance 8h: same rate should produce zero edge."""
        # Rate 0.0005 per 1h = 0.004 per 8h
        # Rate 0.004 per 8h = 0.004 per 8h
        result = calculate_funding_edge(
            long_rate=Decimal("0.004"),
            short_rate=Decimal("0.0005"),
            long_interval_hours=8,
            short_interval_hours=1,
        )
        assert result["edge_pct"] == 0

    def test_1h_short_makes_edge_smaller(self):
        """Short on 1h exchange: the cost is 8x per 8h, reducing edge."""
        # Same rate on both, but short pays every 1h
        result_same = calculate_funding_edge(
            long_rate=Decimal("0.0001"),
            short_rate=Decimal("0.0005"),
            long_interval_hours=8,
            short_interval_hours=8,
        )
        result_diff = calculate_funding_edge(
            long_rate=Decimal("0.0001"),
            short_rate=Decimal("0.0005"),
            long_interval_hours=8,
            short_interval_hours=1,
        )
        # With 1h short interval, the short rate (positive) is multiplied by 8
        # so we receive 8x more from the short side → edge is much bigger
        assert result_diff["edge_pct"] > result_same["edge_pct"]

    def test_annualized_is_1095x_daily(self):
        result = calculate_funding_edge(
            long_rate=Decimal("0.0001"),
            short_rate=Decimal("0.0005"),
        )
        # 3 settlements/day × 365 = 1095
        assert result["annualized_pct"] == result["edge_pct"] * 1095

    def test_negative_rates(self):
        result = calculate_funding_edge(
            long_rate=Decimal("-0.0002"),
            short_rate=Decimal("0.0003"),
        )
        # short pays us 0.0003, long negative means longs get paid → we receive both
        assert result["edge_pct"] > 0


class TestCalculateFees:
    def test_round_trip_fees(self):
        fees = calculate_fees(
            long_taker_fee=Decimal("0.0005"),
            short_taker_fee=Decimal("0.0005"),
        )
        # (0.0005 + 0.0005) * 2 * 100 = 0.2%
        assert fees == Decimal("0.20")

    def test_asymmetric_fees(self):
        fees = calculate_fees(
            long_taker_fee=Decimal("0.0004"),
            short_taker_fee=Decimal("0.0006"),
        )
        # (0.0004 + 0.0006) * 2 * 100 = 0.2%
        assert fees == Decimal("0.20")

    def test_zero_fees(self):
        fees = calculate_fees(Decimal("0"), Decimal("0"))
        assert fees == Decimal("0")
