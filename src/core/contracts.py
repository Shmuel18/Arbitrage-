"""
Data contracts — the shared language of the system.

Every data structure that crosses a module boundary lives here.
Frozen dataclasses for immutability; plain dataclass only for TradeRecord.
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional


# ── Enums ────────────────────────────────────────────────────────

class TradeState(str, Enum):
    OPEN = "open"
    CLOSING = "closing"
    CLOSED = "closed"
    ERROR = "error"


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


# ── Instrument specification ─────────────────────────────────────

@dataclass(frozen=True)
class InstrumentSpec:
    exchange: str
    symbol: str
    base: str
    quote: str
    contract_size: Decimal
    tick_size: Decimal
    lot_size: Decimal
    min_notional: Decimal
    maker_fee: Decimal
    taker_fee: Decimal


# ── Position ─────────────────────────────────────────────────────

@dataclass(frozen=True)
class Position:
    exchange: str
    symbol: str
    side: OrderSide
    quantity: Decimal
    entry_price: Decimal
    unrealized_pnl: Decimal = Decimal(0)
    leverage: int = 1


# ── Order request ────────────────────────────────────────────────

@dataclass
class OrderRequest:
    exchange: str
    symbol: str
    side: OrderSide
    quantity: Decimal
    reduce_only: bool = False


# ── Opportunity candidate ────────────────────────────────────────

@dataclass(frozen=True)
class OpportunityCandidate:
    symbol: str
    long_exchange: str
    short_exchange: str
    long_funding_rate: Decimal
    short_funding_rate: Decimal
    funding_spread_pct: Decimal            # PRIMARY signal: (-long_rate + short_rate) in %
    gross_edge_pct: Decimal
    fees_pct: Decimal
    net_edge_pct: Decimal
    suggested_qty: Decimal
    reference_price: Decimal
    # Immediate spread (raw, before 8h normalization)
    immediate_spread_pct: Decimal = Decimal("0")
    # Immediate net = immediate_spread - fees (no 8h normalization) — primary ranking metric
    immediate_net_pct: Decimal = Decimal("0")
    # Ranking: return per hour (immediate_net / min_interval)
    min_interval_hours: int = 8            # fastest funding interval in this pair
    hourly_rate_pct: Decimal = Decimal("0") # immediate_net_pct / min_interval_hours
    # Closest funding payout timestamp (ms since epoch)
    next_funding_ms: Optional[float] = None
    # Qualification flag (False = display-only, doesn't pass all trading gates)
    qualified: bool = True
    # Cherry-pick fields
    mode: str = "hold"                    # "hold" or "cherry_pick"
    exit_before: Optional[datetime] = None # when to exit (before costly payment)
    n_collections: int = 0                 # how many income payments we'll collect


# ── Trade record ─────────────────────────────────────────────────

@dataclass
class TradeRecord:
    trade_id: str
    symbol: str
    state: TradeState
    long_exchange: str
    short_exchange: str
    long_qty: Decimal
    short_qty: Decimal
    entry_edge_pct: Decimal
    long_funding_rate: Optional[Decimal] = None
    short_funding_rate: Optional[Decimal] = None
    entry_price_long: Optional[Decimal] = None
    entry_price_short: Optional[Decimal] = None
    exit_price_long: Optional[Decimal] = None
    exit_price_short: Optional[Decimal] = None
    fees_paid_total: Optional[Decimal] = None
    funding_received_total: Optional[Decimal] = None
    funding_paid_total: Optional[Decimal] = None
    opened_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    mode: str = "hold"                     # "hold" or "cherry_pick"
    exit_before: Optional[datetime] = None # exit BEFORE this time
    next_funding_long: Optional[datetime] = None
    next_funding_short: Optional[datetime] = None
