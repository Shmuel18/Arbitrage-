"""
Funding-rate arithmetic — calculate edges and fees.
"""

from decimal import Decimal
from typing import Dict, Any


def calculate_funding_edge(
    long_rate: Decimal,
    short_rate: Decimal,
    funding_interval_hours: int = 8,
) -> Dict[str, Any]:
    """
    Calculate the funding-rate edge in BPS.

    Strategy: short the exchange paying high funding, long the exchange
    paying low (or negative) funding. Normalize everything to 8 h.

    Parameters
    ----------
    long_rate   : funding rate on the exchange where we go long
    short_rate  : funding rate on the exchange where we go short
    funding_interval_hours : how often the exchange settles funding (1 or 8)

    Returns
    -------
    dict with edge_bps, annualized_bps, long / short rates in bps
    """
    # Normalize to 8-hour rate
    multiplier = Decimal(8) / Decimal(funding_interval_hours)
    norm_long = long_rate * multiplier
    norm_short = short_rate * multiplier

    # Edge = what the short pays us − what we pay on the long
    #   short > 0 means shorts pay longs → we *receive* this when short
    #   long  > 0 means shorts pay longs → we *pay* this when long
    edge = (norm_short - norm_long) * Decimal("10000")     # convert to bps

    # Annualised (3 settlements / day × 365)
    annual = edge * 3 * 365

    return {
        "edge_bps": edge,
        "annualized_bps": annual,
        "long_rate_bps": norm_long * Decimal("10000"),
        "short_rate_bps": norm_short * Decimal("10000"),
    }


def calculate_fees(
    long_taker_fee: Decimal,
    short_taker_fee: Decimal,
) -> Decimal:
    """Total round-trip taker fees in BPS (open + close both legs)."""
    return (long_taker_fee + short_taker_fee) * 2 * Decimal("10000")

