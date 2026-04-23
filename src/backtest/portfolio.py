"""Trade + P&L bookkeeping for the backtest engine.

Delta-neutral P&L breakdown for a single round-trip trade:

  net_pnl = funding_collected + basis_pnl - fees

where ``basis_pnl`` captures the change in price *basis* between the two
exchanges during the hold (price moves on a single underlying cancel between
long and short legs; only the *divergence* matters).
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

_ZERO = Decimal("0")


@dataclass
class Trade:
    symbol: str
    long_exchange: str
    short_exchange: str
    entry_ts_ms: int
    entry_long_price: Decimal
    entry_short_price: Decimal
    notional_usd: Decimal

    funding_collected_usd: Decimal = _ZERO
    fees_usd: Decimal = _ZERO

    exit_ts_ms: Optional[int] = None
    exit_long_price: Optional[Decimal] = None
    exit_short_price: Optional[Decimal] = None
    exit_reason: Optional[str] = None

    @property
    def is_open(self) -> bool:
        return self.exit_ts_ms is None

    @property
    def hold_hours(self) -> float:
        if self.exit_ts_ms is None:
            return 0.0
        return (self.exit_ts_ms - self.entry_ts_ms) / 3_600_000

    @property
    def basis_pnl_usd(self) -> Decimal:
        if self.exit_long_price is None or self.exit_short_price is None:
            return _ZERO
        long_ret = (self.exit_long_price - self.entry_long_price) / self.entry_long_price
        short_ret = (self.entry_short_price - self.exit_short_price) / self.entry_short_price
        return (long_ret + short_ret) * self.notional_usd

    @property
    def net_pnl_usd(self) -> Decimal:
        return self.funding_collected_usd + self.basis_pnl_usd - self.fees_usd
