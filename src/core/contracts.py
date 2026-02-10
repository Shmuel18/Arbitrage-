"""
Data Contracts & Type Definitions
Immutable data structures for type safety
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Dict, List, Optional
from uuid import UUID, uuid4


# ==================== ENUMS ====================

class ExchangeStatus(str, Enum):
    """Exchange health status"""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    OFFLINE = "offline"


class TradeState(str, Enum):
    """Trade lifecycle states - FSM"""
    IDLE = "idle"
    VALIDATING = "validating"
    PRE_FLIGHT = "pre_flight"
    PENDING_OPEN = "pending_open"
    OPEN_PARTIAL = "open_partial"
    ACTIVE_HEDGED = "active_hedged"
    PENDING_CLOSE = "pending_close"
    RECONCILIATION = "reconciliation"
    CLOSED = "closed"
    ERROR_RECOVERY = "error_recovery"


class OrderSide(str, Enum):
    """Order direction"""
    LONG = "long"
    SHORT = "short"


class OrderStatus(str, Enum):
    """Order execution status"""
    PENDING = "pending"
    SUBMITTED = "submitted"
    PARTIAL = "partial"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    FAILED = "failed"


class SeverityLevel(str, Enum):
    """Panic severity levels"""
    S1_PARTIAL = "s1_partial"      # Partial fill - chase
    S2_ORPHAN = "s2_orphan"        # Orphaned position - immediate close
    S3_OUTAGE = "s3_outage"        # Exchange outage - halt symbol


# ==================== INSTRUMENT SPECS ====================

@dataclass(frozen=True)
class InstrumentSpec:
    """Exchange-specific instrument specification"""
    symbol: str
    exchange: str
    contract_multiplier: Decimal
    tick_size: Decimal
    step_size: Decimal
    min_notional: Decimal
    funding_interval_hours: int
    max_leverage: int
    taker_fee: Decimal
    maker_fee: Decimal
    
    def normalize_price(self, price: Decimal) -> Decimal:
        """Round price to tick size"""
        return (price // self.tick_size) * self.tick_size
    
    def normalize_quantity(self, qty: Decimal) -> Decimal:
        """Round quantity to step size"""
        return (qty // self.step_size) * self.step_size


# ==================== MARKET DATA ====================

@dataclass(frozen=True)
class OrderbookLevel:
    """Single orderbook level"""
    price: Decimal
    quantity: Decimal
    
    @property
    def notional(self) -> Decimal:
        return self.price * self.quantity


@dataclass(frozen=True)
class StandardMarketEvent:
    """Normalized market data from any exchange"""
    symbol_internal: str              # Internal symbol format
    exchange: str
    timestamp: datetime
    
    # Pricing
    bid: Decimal
    ask: Decimal
    mark_price: Decimal
    
    # Funding
    funding_rate: Decimal
    funding_timestamp: datetime
    next_funding: datetime
    
    # Orderbook
    bids: List[OrderbookLevel]
    asks: List[OrderbookLevel]
    
    # Health
    sequence: Optional[int] = None
    
    @property
    def spread(self) -> Decimal:
        return self.ask - self.bid
    
    @property
    def spread_bps(self) -> Decimal:
        mid = (self.bid + self.ask) / 2
        return (self.spread / mid) * 10000 if mid > 0 else Decimal('0')
    
    @property
    def is_healthy(self) -> bool:
        """Basic sanity check"""
        return (
            self.bid > 0 and
            self.ask > 0 and
            self.bid < self.ask and
            self.spread_bps < 100  # Max 1% spread
        )


# ==================== OPPORTUNITIES ====================

@dataclass(frozen=True)
class OpportunityCandidate:
    """
    Discovery output - opportunity contract
    Scanner NEVER executes, only produces this
    """
    # Instruments
    symbol: str
    exchange_long: str          # Where to go long
    exchange_short: str         # Where to go short
    
    # Sizing
    quantity: Decimal
    size_usd: Decimal
    
    # Economics (worst-case)
    expected_net_bps: Decimal
    funding_edge_bps: Decimal
    total_fees_bps: Decimal
    total_slippage_bps: Decimal
    total_buffer_bps: Decimal
    
    # Execution constraints
    max_slippage_bps: Decimal
    deadline_timestamp: datetime
    
    # Prices at discovery
    long_entry_price: Decimal
    short_entry_price: Decimal
    
    # Defaults
    opportunity_id: UUID = field(default_factory=uuid4)
    timestamp: datetime = field(default_factory=datetime.utcnow)
    
    # Risk
    panic_policy: SeverityLevel = SeverityLevel.S2_ORPHAN
    
    def is_expired(self) -> bool:
        """Check if opportunity window closed"""
        return datetime.utcnow() > self.deadline_timestamp
    
    def is_profitable(self) -> bool:
        """Validate profitability after worst-case"""
        return self.expected_net_bps > 0


# ==================== ORDERS ====================

@dataclass
class OrderRequest:
    """Order to be submitted"""
    exchange: str
    symbol: str
    side: OrderSide
    quantity: Decimal
    
    order_id: UUID = field(default_factory=uuid4)
    trade_id: Optional[UUID] = None
    price: Optional[Decimal] = None  # None = market
    
    timestamp_created: datetime = field(default_factory=datetime.utcnow)
    timestamp_submitted: Optional[datetime] = None
    timestamp_filled: Optional[datetime] = None
    
    status: OrderStatus = OrderStatus.PENDING
    filled_quantity: Decimal = Decimal('0')
    average_price: Optional[Decimal] = None
    fee: Decimal = Decimal('0')
    
    exchange_order_id: Optional[str] = None
    reject_reason: Optional[str] = None
    chase_count: int = 0


@dataclass
class TradeLeg:
    """Single leg of a paired trade"""
    exchange: str
    side: OrderSide
    
    order: Optional[OrderRequest] = None
    target_quantity: Decimal = Decimal('0')
    filled_quantity: Decimal = Decimal('0')
    
    @property
    def is_complete(self) -> bool:
        return self.filled_quantity >= self.target_quantity
    
    @property
    def fill_ratio(self) -> Decimal:
        if self.target_quantity == 0:
            return Decimal('0')
        return self.filled_quantity / self.target_quantity


# ==================== TRADE STATE ====================

@dataclass
class TradeRecord:
    """
    Complete trade record with full lifecycle
    This is the master state object
    """
    trade_id: UUID = field(default_factory=uuid4)
    
    # Opportunity reference
    opportunity: OpportunityCandidate = None
    
    # State machine
    state: TradeState = TradeState.IDLE
    state_history: List[tuple[TradeState, datetime]] = field(default_factory=list)
    
    # Legs
    long_leg: Optional[TradeLeg] = None
    short_leg: Optional[TradeLeg] = None
    
    # Timestamps
    timestamp_created: datetime = field(default_factory=datetime.utcnow)
    timestamp_validated: Optional[datetime] = None
    timestamp_opened: Optional[datetime] = None
    timestamp_closed: Optional[datetime] = None
    
    # P&L
    expected_net_bps: Decimal = Decimal('0')
    realized_pnl_usd: Decimal = Decimal('0')
    total_fees_usd: Decimal = Decimal('0')
    
    # Risk tracking
    max_orphan_time_ms: int = 0
    max_delta_breach_pct: Decimal = Decimal('0')
    
    # Metadata
    close_reason: Optional[str] = None
    errors: List[str] = field(default_factory=list)
    
    def transition_state(self, new_state: TradeState):
        """Update state with history tracking"""
        self.state_history.append((self.state, datetime.utcnow()))
        self.state = new_state
    
    @property
    def is_active(self) -> bool:
        return self.state in [
            TradeState.VALIDATING,
            TradeState.PRE_FLIGHT,
            TradeState.PENDING_OPEN,
            TradeState.OPEN_PARTIAL,
            TradeState.ACTIVE_HEDGED,
            TradeState.PENDING_CLOSE
        ]
    
    @property
    def is_hedged(self) -> bool:
        """Check if both legs filled"""
        if not self.long_leg or not self.short_leg:
            return False
        return self.long_leg.is_complete and self.short_leg.is_complete
    
    @property
    def current_delta(self) -> Decimal:
        """Calculate position delta"""
        long_qty = self.long_leg.filled_quantity if self.long_leg else Decimal('0')
        short_qty = self.short_leg.filled_quantity if self.short_leg else Decimal('0')
        return long_qty - short_qty


# ==================== POSITIONS ====================

@dataclass(frozen=True)
class Position:
    """Real position from exchange"""
    exchange: str
    symbol: str
    quantity: Decimal          # Positive = long, negative = short
    entry_price: Decimal
    mark_price: Decimal
    liquidation_price: Optional[Decimal]
    unrealized_pnl: Decimal
    margin_used: Decimal
    timestamp: datetime = field(default_factory=datetime.utcnow)
    
    @property
    def side(self) -> OrderSide:
        return OrderSide.LONG if self.quantity > 0 else OrderSide.SHORT
    
    @property
    def notional(self) -> Decimal:
        return abs(self.quantity * self.mark_price)


# ==================== RISK EVENTS ====================

@dataclass
class RiskEvent:
    """Risk violation event"""
    severity: SeverityLevel
    event_type: str
    message: str
    
    event_id: UUID = field(default_factory=uuid4)
    timestamp: datetime = field(default_factory=datetime.utcnow)
    
    trade_id: Optional[UUID] = None
    exchange: Optional[str] = None
    symbol: Optional[str] = None
    
    # Measured values
    measured_value: Optional[Decimal] = None
    threshold_value: Optional[Decimal] = None
    
    # Action taken
    action_taken: Optional[str] = None
    resolved: bool = False
    resolution_timestamp: Optional[datetime] = None


# ==================== RECONCILIATION ====================

@dataclass
class ReconciliationResult:
    """Result of position reconciliation"""
    exchange: str
    symbol: str
    
    expected_quantity: Decimal
    actual_quantity: Decimal
    mismatch: Decimal
    
    timestamp: datetime = field(default_factory=datetime.utcnow)
    expected_trades: List[UUID] = field(default_factory=list)
    
    action_taken: Optional[str] = None
    
    @property
    def has_mismatch(self) -> bool:
        return abs(self.mismatch) > Decimal('0.0001')  # Tolerance
