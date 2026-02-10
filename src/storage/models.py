"""
Database Models (SQLAlchemy)
PostgreSQL/TimescaleDB schemas
"""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    Column,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    Boolean
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship

from src.core.contracts import OrderSide, OrderStatus, SeverityLevel, TradeState

Base = declarative_base()


class Trade(Base):
    """Trade records table"""
    __tablename__ = 'trades'
    
    trade_id = Column(PG_UUID(as_uuid=True), primary_key=True)
    opportunity_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)
    
    # Basic info
    symbol = Column(String(20), nullable=False, index=True)
    status = Column(Enum(TradeState), nullable=False, index=True)
    
    # Exchanges
    exchange_long = Column(String(20), nullable=False)
    exchange_short = Column(String(20), nullable=False)
    
    # Sizing
    quantity = Column(Numeric(20, 8), nullable=False)
    size_usd = Column(Numeric(20, 2), nullable=False)
    
    # Economics
    expected_net_bps = Column(Numeric(10, 4), nullable=False)
    realized_pnl_usd = Column(Numeric(20, 2), default=0)
    total_fees_usd = Column(Numeric(20, 2), default=0)
    
    # Timestamps
    timestamp_created = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    timestamp_validated = Column(DateTime)
    timestamp_opened = Column(DateTime)
    timestamp_closed = Column(DateTime)
    
    # Risk metrics
    max_orphan_time_ms = Column(Integer, default=0)
    max_delta_breach_pct = Column(Numeric(10, 4), default=0)
    
    # Metadata
    close_reason = Column(String(200))
    
    # Relationships
    orders = relationship("Order", back_populates="trade", cascade="all, delete-orphan")
    
    # Indexes
    __table_args__ = (
        Index('idx_trades_symbol_status', 'symbol', 'status'),
        Index('idx_trades_created', 'timestamp_created'),
    )


class Order(Base):
    """Order records table"""
    __tablename__ = 'orders'
    
    order_id = Column(PG_UUID(as_uuid=True), primary_key=True)
    trade_id = Column(PG_UUID(as_uuid=True), ForeignKey('trades.trade_id'), index=True)
    
    # Exchange info
    exchange = Column(String(20), nullable=False, index=True)
    exchange_order_id = Column(String(100), index=True)
    symbol = Column(String(20), nullable=False)
    
    # Order details
    side = Column(Enum(OrderSide), nullable=False)
    order_type = Column(String(20), nullable=False)  # market, limit
    
    # Pricing
    price = Column(Numeric(20, 8))  # NULL for market orders
    quantity = Column(Numeric(20, 8), nullable=False)
    filled_quantity = Column(Numeric(20, 8), default=0)
    average_price = Column(Numeric(20, 8))
    
    # Status
    status = Column(Enum(OrderStatus), nullable=False, index=True)
    reject_reason = Column(Text)
    
    # Fees
    fee = Column(Numeric(20, 8), default=0)
    fee_currency = Column(String(10), default='USDT')
    
    # Timestamps
    timestamp_created = Column(DateTime, nullable=False, default=datetime.utcnow)
    timestamp_submitted = Column(DateTime)
    timestamp_filled = Column(DateTime)
    
    # Execution
    chase_count = Column(Integer, default=0)
    
    # Relationships
    trade = relationship("Trade", back_populates="orders")
    
    # Indexes
    __table_args__ = (
        Index('idx_orders_trade_exchange', 'trade_id', 'exchange'),
        Index('idx_orders_status', 'status'),
    )


class DiscoveryLog(Base):
    """Discovery scanner logs"""
    __tablename__ = 'discovery_logs'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    opportunity_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)
    
    timestamp = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    
    # Opportunity details
    symbol = Column(String(20), nullable=False, index=True)
    exchange_long = Column(String(20), nullable=False)
    exchange_short = Column(String(20), nullable=False)
    
    # Scores
    expected_net_bps = Column(Numeric(10, 4), nullable=False)
    funding_edge_bps = Column(Numeric(10, 4), nullable=False)
    total_fees_bps = Column(Numeric(10, 4), nullable=False)
    total_slippage_bps = Column(Numeric(10, 4), nullable=False)
    
    # Depth metrics
    bid_depth_usd = Column(Numeric(20, 2))
    ask_depth_usd = Column(Numeric(20, 2))
    
    # Execution decision
    executed = Column(Boolean, nullable=False, default=False, index=True)
    reject_reason = Column(String(200))
    
    # Indexes
    __table_args__ = (
        Index('idx_discovery_symbol_timestamp', 'symbol', 'timestamp'),
        Index('idx_discovery_executed', 'executed'),
    )


class PositionSnapshot(Base):
    """Position snapshots for reconciliation"""
    __tablename__ = 'position_snapshots'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    
    timestamp = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    
    # Position details
    exchange = Column(String(20), nullable=False, index=True)
    symbol = Column(String(20), nullable=False, index=True)
    
    quantity = Column(Numeric(20, 8), nullable=False)  # Signed: + long, - short
    entry_price = Column(Numeric(20, 8), nullable=False)
    mark_price = Column(Numeric(20, 8), nullable=False)
    liquidation_price = Column(Numeric(20, 8))
    
    unrealized_pnl = Column(Numeric(20, 2), nullable=False)
    margin_used = Column(Numeric(20, 2), nullable=False)
    
    # Indexes
    __table_args__ = (
        Index('idx_positions_exchange_symbol', 'exchange', 'symbol'),
        Index('idx_positions_timestamp', 'timestamp'),
    )


class Incident(Base):
    """Risk events and incidents"""
    __tablename__ = 'incidents'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    event_id = Column(PG_UUID(as_uuid=True), nullable=False, unique=True, index=True)
    
    timestamp = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    
    # Severity
    severity = Column(Enum(SeverityLevel), nullable=False, index=True)
    event_type = Column(String(50), nullable=False, index=True)
    
    # Details
    message = Column(Text, nullable=False)
    
    # Context
    trade_id = Column(PG_UUID(as_uuid=True), index=True)
    exchange = Column(String(20))
    symbol = Column(String(20))
    
    # Metrics
    measured_value = Column(Numeric(20, 8))
    threshold_value = Column(Numeric(20, 8))
    
    # Resolution
    action_taken = Column(String(200))
    resolved = Column(Boolean, nullable=False, default=False, index=True)
    resolution_timestamp = Column(DateTime)
    
    # Indexes
    __table_args__ = (
        Index('idx_incidents_severity_timestamp', 'severity', 'timestamp'),
        Index('idx_incidents_resolved', 'resolved'),
    )


class SystemMetric(Base):
    """System performance metrics (TimescaleDB hypertable)"""
    __tablename__ = 'system_metrics'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    
    timestamp = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    
    # Metric identification
    metric_name = Column(String(50), nullable=False, index=True)
    component = Column(String(50), nullable=False)
    
    # Values
    value = Column(Numeric(20, 8), nullable=False)
    unit = Column(String(20))
    
    # Tags
    exchange = Column(String(20))
    symbol = Column(String(20))
    
    # Indexes
    __table_args__ = (
        Index('idx_metrics_name_timestamp', 'metric_name', 'timestamp'),
        Index('idx_metrics_component', 'component'),
    )


# For TimescaleDB, you'd convert system_metrics to hypertable:
# SELECT create_hypertable('system_metrics', 'timestamp');
