"""Tests for funding-rate calculator."""

from decimal import Decimal

from src.discovery.calculator import calculate_fees, calculate_funding_edge


class TestCalculateFundingEdge:
    def test_positive_edge_when_short_rate_higher(self):
        result = calculate_funding_edge(
            long_rate=Decimal("0.0001"),
            short_rate=Decimal("0.0005"),
            funding_interval_hours=8,
        )
        assert result["edge_bps"] > 0

    def test_negative_edge_when_long_rate_higher(self):
        result = calculate_funding_edge(
            long_rate=Decimal("0.0005"),
            short_rate=Decimal("0.0001"),
            funding_interval_hours=8,
        )
        assert result["edge_bps"] < 0

    def test_zero_edge_when_rates_equal(self):
        result = calculate_funding_edge(
            long_rate=Decimal("0.0003"),
            short_rate=Decimal("0.0003"),
            funding_interval_hours=8,
        )
        assert result["edge_bps"] == 0

    def test_1h_normalization_multiplies_by_8(self):
        result_1h = calculate_funding_edge(
            long_rate=Decimal("0.0001"),
            short_rate=Decimal("0.0005"),
            funding_interval_hours=1,
        )
        result_8h = calculate_funding_edge(
            long_rate=Decimal("0.0001"),
            short_rate=Decimal("0.0005"),
            funding_interval_hours=8,
        )
        assert result_1h["edge_bps"] == result_8h["edge_bps"] * 8

    def test_annualized_is_1095x_daily(self):
        result = calculate_funding_edge(
            long_rate=Decimal("0.0001"),
            short_rate=Decimal("0.0005"),
            funding_interval_hours=8,
        )
        # 3 settlements/day × 365 = 1095
        assert result["annualized_bps"] == result["edge_bps"] * 1095

    def test_negative_rates(self):
        result = calculate_funding_edge(
            long_rate=Decimal("-0.0002"),
            short_rate=Decimal("0.0003"),
            funding_interval_hours=8,
        )
        # short pays us 0.0003, long negative means longs get paid → we receive both
        assert result["edge_bps"] > 0


class TestCalculateFees:
    def test_round_trip_fees(self):
        fees = calculate_fees(
            long_taker_fee=Decimal("0.0005"),
            short_taker_fee=Decimal("0.0005"),
        )
        # (0.0005 + 0.0005) * 2 * 10000 = 20 bps
        assert fees == Decimal("20.0")

    def test_asymmetric_fees(self):
        fees = calculate_fees(
            long_taker_fee=Decimal("0.0004"),
            short_taker_fee=Decimal("0.0006"),
        )
        # (0.0004 + 0.0006) * 2 * 10000 = 20 bps
        assert fees == Decimal("20.0")

    def test_zero_fees(self):
        fees = calculate_fees(Decimal("0"), Decimal("0"))
        assert fees == Decimal("0")
