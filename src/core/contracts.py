"""
Data contracts — the shared language of the system.

Every data structure that crosses a module boundary lives here.
Frozen dataclasses for immutability; plain dataclass only for TradeRecord.
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, List, Optional


# ── Enums ────────────────────────────────────────────────────────

class TradeState(str, Enum):
    OPEN = "open"
    CLOSING = "closing"
    CLOSED = "closed"
    ERROR = "error"
    PRE_FLIGHT = "pre_flight"
    PENDING_OPEN = "pending_open"
    ACTIVE_HEDGED = "active_hedged"
    ERROR_RECOVERY = "error_recovery"


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


# ── Market data ──────────────────────────────────────────────────

@dataclass(frozen=True)
class StandardMarketEvent:
    exchange: str
    symbol: str
    bid: Decimal
    ask: Decimal
    spread_bps: Decimal
    timestamp: datetime


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
    exchange_long: str
    exchange_short: str
    quantity: Decimal
    size_usd: Decimal
    expected_net_bps: Decimal
    funding_edge_bps: Decimal
    total_fees_bps: Decimal = Decimal("0")
    total_slippage_bps: Decimal = Decimal("0")
    total_buffer_bps: Decimal = Decimal("0")
    max_slippage_bps: Decimal = Decimal("10")
    deadline_timestamp: Optional[datetime] = None
    long_entry_price: Optional[Decimal] = None
    short_entry_price: Optional[Decimal] = None
    long_funding_rate: Decimal = Decimal("0")
    short_funding_rate: Decimal = Decimal("0")
    mode: str = "hold"
    exit_before: Optional[datetime] = None
    n_collections: int = 0
    opportunity_id: Optional[str] = None


# ── Trade record ─────────────────────────────────────────────────

@dataclass
class TradeRecord:
    opportunity: Optional[Any] = None     # OpportunityCandidate ref
    state: TradeState = TradeState.OPEN
    trade_id: str = ""
    expected_net_bps: Decimal = Decimal("0")
    errors: List[str] = field(default_factory=list)
    opened_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
