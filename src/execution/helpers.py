"""
Execution helpers — pure, stateless utility functions.

Extracted from ExecutionController to keep each concern small and testable.
All functions here have no side effects and no dependencies on shared state.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Optional, Tuple

if TYPE_CHECKING:
    from src.core.contracts import TradeRecord


def extract_avg_price(order: dict) -> Optional[Decimal]:
    """Extract average fill price from a CCXT order dict."""
    for key in ("average", "avg_price", "price", "avgPrice"):
        val = order.get(key)
        if val is not None:
            try:
                return Decimal(str(val))
            except Exception:
                continue
    return None


def extract_fee(order: dict, fallback_rate: Optional[Decimal] = None) -> Decimal:
    """Extract fee cost in USDT from a CCXT order dict.

    Some exchanges return fees in the base currency (e.g. CYBER) rather than USDT.
    When that happens we convert using the order's average fill price so the
    total_fees figure is always denominated in USDT.

    If the exchange doesn't provide fee data in the order response (common),
    we use the fallback_rate (if provided) multiplied by the fill cost.
    """
    avg_price = extract_avg_price(order) or Decimal("0")

    def _cost_to_usdt(f: dict) -> Decimal:
        try:
            cost = Decimal(str(f.get("cost", 0) or 0))
            currency = (f.get("currency") or "").upper()
            if not currency or currency in ("USDT", "BUSD", "USDC", "USD"):
                return cost
            # Fee is in base asset — convert to USDT using fill price
            if avg_price > 0:
                return cost * avg_price
            return cost
        except Exception:
            return Decimal("0")

    total = Decimal("0")
    fee = order.get("fee")
    if isinstance(fee, dict) and fee.get("cost") is not None:
        total += _cost_to_usdt(fee)
    fees = order.get("fees")
    if isinstance(fees, list):
        for f in fees:
            if isinstance(f, dict) and f.get("cost") is not None:
                total += _cost_to_usdt(f)

    if total == 0 and fallback_rate is not None and fallback_rate > 0:
        filled_val = order.get("filled")
        if filled_val is None or Decimal(str(filled_val)) == 0:
            filled_val = order.get("amount") or 0
        filled = Decimal(str(filled_val))
        if filled > 0 and avg_price > 0:
            total = filled * avg_price * fallback_rate

    return total


def estimate_funding_totals(trade: "TradeRecord") -> Tuple[Decimal, Decimal]:
    """Estimate funding paid / received from entry rates and notional.

    Returns ``(paid, received)`` in USD.  This is an *estimate*; the actual
    exchange settlement is reconciled in ``_close_trade`` when exchange APIs
    are available.
    """
    if not trade.entry_price_long or not trade.entry_price_short:
        return Decimal("0"), Decimal("0")

    long_rate = trade.long_funding_rate or Decimal("0")
    short_rate = trade.short_funding_rate or Decimal("0")
    notional_long = trade.entry_price_long * trade.long_qty
    notional_short = trade.entry_price_short * trade.short_qty

    paid = Decimal("0")
    received = Decimal("0")

    if long_rate >= 0:
        paid += notional_long * long_rate
    else:
        received += notional_long * abs(long_rate)

    if short_rate >= 0:
        received += notional_short * short_rate
    else:
        paid += notional_short * abs(short_rate)

    return paid, received
