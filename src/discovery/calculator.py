"""Funding-rate arithmetic — calculate edges and fees."""

from decimal import Decimal
from typing import Dict, Any


def calculate_funding_edge(
    long_rate: Decimal,
    short_rate: Decimal,
    long_interval_hours: int = 8,
    short_interval_hours: int = 8,
) -> Dict[str, Any]:
    """
    Calculate the funding-rate edge in PERCENT (normalized to 8h).

    Funding mechanics:
      rate > 0 → shorts pay longs
      rate < 0 → longs pay shorts

    Per-payment PnL:
      Long side : −rate  (positive rate = we pay, negative = we receive)
      Short side: +rate  (positive rate = we receive, negative = we pay)

    Returns edge as percentage (e.g. 0.5 = 0.5%). Matches exchange display.
    """
    norm_long = long_rate * Decimal(8) / Decimal(long_interval_hours)
    norm_short = short_rate * Decimal(8) / Decimal(short_interval_hours)

    edge = (norm_short - norm_long) * Decimal("100")
    annual = edge * 3 * 365

    return {
        "edge_pct": edge,
        "annualized_pct": annual,
        "long_rate_pct": norm_long * Decimal("100"),
        "short_rate_pct": norm_short * Decimal("100"),
    }


def analyze_per_payment_pnl(
    long_rate: Decimal,
    short_rate: Decimal,
) -> Dict[str, Any]:
    """
    Analyze which funding payments are income vs cost.

    Returns per-payment PnL for each side (NOT normalized).

      Long PnL per payment = −long_rate
        rate > 0  → we pay   (PnL < 0)
        rate < 0  → we receive (PnL > 0)

      Short PnL per payment = +short_rate
        rate > 0  → we receive (PnL > 0)
        rate < 0  → we pay   (PnL < 0)
    """
    long_pnl = -long_rate
    short_pnl = short_rate

    return {
        "long_pnl_per_payment": long_pnl,
        "short_pnl_per_payment": short_pnl,
        "long_is_income": long_pnl > 0,
        "short_is_income": short_pnl > 0,
        "both_income": long_pnl > 0 and short_pnl > 0,
        "both_cost": long_pnl <= 0 and short_pnl <= 0,
    }


def calculate_cherry_pick_edge(
    income_rate_per_payment: Decimal,
    n_collections: int,
) -> Decimal:
    """Total collectible edge in PERCENT from cherry-pick strategy."""
    return abs(income_rate_per_payment) * n_collections * Decimal("100")


def calculate_fees(
    long_taker_fee: Decimal,
    short_taker_fee: Decimal,
) -> Decimal:
    """Total round-trip taker fees in PERCENT (open + close both legs)."""
    return (long_taker_fee + short_taker_fee) * 2 * Decimal("100")

