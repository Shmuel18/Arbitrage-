"""Funding-rate arithmetic — pure funding-spread calculations and fees.

Entry logic is driven ENTIRELY by the funding-rate spread, never by price edge.
Price differences are only used as a slippage filter.
"""

from decimal import Decimal
from typing import Dict, Any


# ── Primary entry signal ─────────────────────────────────────────

def calculate_funding_spread(
    long_rate: Decimal,
    short_rate: Decimal,
    long_interval_hours: int = 8,
    short_interval_hours: int = 8,
) -> Dict[str, Any]:
    """
    Pure funding-rate spread — the PRIMARY entry signal.

    Formula (per the funding-arb strategy):
        spread = (Long_Funding × −1) + (Short_Funding × +1)

    Mechanics:
      • Long side PnL  = −funding_rate  (positive rate → we pay, negative → we receive)
      • Short side PnL  = +funding_rate  (positive rate → we receive, negative → we pay)

    The spread is normalized to an 8-hour settlement period and returned in
    PERCENT (e.g. 0.05 means 0.05 %).

    You profit when the spread > 0, i.e.:
      • Long funding is negative  (you receive as long holder), AND/OR
      • Short funding is positive (you receive as short holder).
    """
    # Immediate spread (no normalization) — the ACTUAL rate difference right now
    immediate_long_pnl = -long_rate    # negative rate → income
    immediate_short_pnl = short_rate   # positive rate → income
    immediate_spread_pct = (immediate_long_pnl + immediate_short_pnl) * Decimal("100")

    # Normalise to 8 h
    norm_long = long_rate * Decimal(8) / Decimal(long_interval_hours)
    norm_short = short_rate * Decimal(8) / Decimal(short_interval_hours)

    # Per-payment PnL (as raw rate, not percent)
    long_pnl = -norm_long   # negative rate → income
    short_pnl = norm_short  # positive rate → income

    # Spread in percent
    spread_pct = (long_pnl + short_pnl) * Decimal("100")
    annual_pct = spread_pct * 3 * 365   # 3 settlements/day × 365

    return {
        "immediate_spread_pct": immediate_spread_pct,
        "funding_spread_pct": spread_pct,
        "annualized_pct": annual_pct,
        "long_pnl_pct": long_pnl * Decimal("100"),
        "short_pnl_pct": short_pnl * Decimal("100"),
        "long_rate_norm": norm_long,
        "short_rate_norm": norm_short,
    }


# ── Backward-compatible alias ────────────────────────────────────

def calculate_funding_edge(
    long_rate: Decimal,
    short_rate: Decimal,
    long_interval_hours: int = 8,
    short_interval_hours: int = 8,
) -> Dict[str, Any]:
    """Alias kept for backward compatibility — delegates to calculate_funding_spread."""
    result = calculate_funding_spread(
        long_rate, short_rate, long_interval_hours, short_interval_hours,
    )
    # Map new keys → old keys expected by callers
    return {
        "edge_pct": result["funding_spread_pct"],
        "annualized_pct": result["annualized_pct"],
        "long_rate_pct": result["long_pnl_pct"] * Decimal("-1"),  # restore original sign
        "short_rate_pct": result["short_pnl_pct"],
    }


# ── Per-payment analysis ─────────────────────────────────────────

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


# ── Cherry-pick edge ─────────────────────────────────────────────

def calculate_cherry_pick_edge(
    income_rate_per_payment: Decimal,
    n_collections: int,
) -> Decimal:
    """Total collectible edge in PERCENT from cherry-pick strategy."""
    return abs(income_rate_per_payment) * n_collections * Decimal("100")


# ── Fee calculation ──────────────────────────────────────────────

def calculate_fees(
    long_taker_fee: Decimal,
    short_taker_fee: Decimal,
) -> Decimal:
    """Total round-trip taker fees in PERCENT (open + close both legs)."""
    return (long_taker_fee + short_taker_fee) * 2 * Decimal("100")

