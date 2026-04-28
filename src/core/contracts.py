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


class TradeMode(str, Enum):
    """Trading sub-mode, determined by the funding-rate relationship."""
    HOLD = "hold"               # both sides income (or default fallback)
    POT = "pot"                 # both sides income, aliased label
    CHERRY_PICK = "cherry_pick" # one income, one cost — exit before cost fires
    NUTCRACKER = "nutcracker"   # both sides in same cycle (income & cost overlap)


class EntryTier(str, Enum):
    """Entry quality tier — determines timing and risk classification."""
    TOP = "top"          # 🏆 Funding + favorable price spread
    MEDIUM = "medium"    # 📊 Funding + neutral spread (adverse within noise)
    WEAK = "weak"        # ⚡ Funding + adverse spread, but funding exceeds it by 0.5%+


class ExitReason(str, Enum):
    """Static exit reason codes persisted to Redis and the trade journal.

    Dynamic reasons (e.g. ``max_wait_30min``) are plain strings and retain
    their diagnostic suffix; only the static, non-parameterised codes live here.
    """
    SPREAD_BELOW_THRESHOLD = "spread_below_threshold"
    MANUAL_CLOSE = "manual_close"
    UPGRADE_EXIT = "upgrade_exit"
    PROFIT_TARGET = "profit_target"
    EXIT_TIMEOUT = "exit_timeout"
    LIQUIDATION_RISK = "liquidation_risk"           # Bot detected risk + closed preventively
    LIQUIDATION_EXTERNAL = "liquidation_external"   # Exchange force-closed the position
    RESTART_SHUTDOWN = "restart_shutdown"


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
    # Closest funding payout timestamp (ms since epoch) — always the income side
    next_funding_ms: Optional[float] = None
    # Per-side next funding timestamps (ms since epoch) — from live exchange data
    long_next_funding_ms: Optional[float] = None
    short_next_funding_ms: Optional[float] = None
    # Per-side intervals (actual from exchange, not hardcoded)
    long_interval_hours: int = 8
    short_interval_hours: int = 8
    # Qualification flag (False = display-only, doesn't pass all trading gates)
    qualified: bool = True
    # Why qualified=False — set at the gate that rejected the opportunity so
    # the dashboard can show a specific reason instead of the misleading
    # "Below threshold (0.3%)" label that fires for every disqualification.
    # One of: "vol_unknown" / "low_vol" / "adverse_basis" / "funding_spread_low"
    # / "funding_no_imminent" / "funding_stale" / "cherry_unsuitable" / None.
    disqualify_reason: Optional[str] = None
    # Cherry-pick fields
    mode: TradeMode = TradeMode.HOLD       # see TradeMode enum
    exit_before: Optional[datetime] = None # when to exit (before costly payment)
    n_collections: int = 0                 # how many income payments we'll collect
    # Tier-based entry strategy
    entry_tier: Optional[str] = None       # TOP / MEDIUM / WEAK (see EntryTier)
    price_spread_pct: Decimal = Decimal("0")  # cross-exchange price diff % (positive = favorable)
    stale_price: bool = False              # True when prices are too old to trust for entry, display-only


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
    long_taker_fee: Optional[Decimal] = None
    short_taker_fee: Optional[Decimal] = None
    opened_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    mode: TradeMode = TradeMode.HOLD        # see TradeMode enum
    exit_before: Optional[datetime] = None # exit BEFORE this time
    next_funding_long: Optional[datetime] = None
    next_funding_short: Optional[datetime] = None
    # Funding collection tracking
    funding_collections: int = 0           # how many payments collected so far
    funding_collected_usd: Decimal = Decimal("0")  # cumulative USD received
    # Per-side funding tracked by the bot at collection time.
    # Used by close reconciliation to avoid the bot_net derivation bug:
    # if the bot missed one side at collection (e.g. rate was null), bot_net
    # is wrong, and deriving the missing side from it inflates the total.
    _funding_tracked_long: Decimal = Decimal("0")
    _funding_tracked_short: Decimal = Decimal("0")
    # Price basis at entry: (entry_long_price − entry_short_price) / entry_short_price × 100
    # Positive = long was more expensive at entry. Used as the exit break-even threshold:
    # we break even on price as long as (exit_long − exit_short) / exit_short × 100 ≥ entry_basis_pct
    entry_basis_pct: Optional[Decimal] = None
    # Tier-based entry classification
    entry_tier: Optional[str] = None       # TOP / MEDIUM / WEAK (see EntryTier)
    price_spread_pct: Optional[Decimal] = None  # cross-exchange price spread at entry
    # 24h quote volume (USD) on each leg at entry — for liquidity context in the UI
    long_24h_volume_usd: Optional[Decimal] = None
    short_24h_volume_usd: Optional[Decimal] = None

    # ── Serialization ────────────────────────────────────────────

    _DECIMAL_FIELDS = (
        "long_qty", "short_qty", "entry_edge_pct", "entry_basis_pct",
        "long_funding_rate", "short_funding_rate", "long_taker_fee",
        "short_taker_fee", "entry_price_long", "entry_price_short",
        "fees_paid_total", "funding_collected_usd", "price_spread_pct",
        "long_24h_volume_usd", "short_24h_volume_usd",
        # Per-side funding tracked by bot at collection time (persisted for reconciliation).
        "_funding_tracked_long", "_funding_tracked_short",
        # Running totals from exchange history — must be persisted so crash-recovery
        # computes the correct DELTA on the next payment (avoids double-counting).
        "_actual_long_funding_sum", "_actual_short_funding_sum",
    )
    _DATETIME_FIELDS = ("opened_at",)

    def to_persist_dict(self) -> dict:
        """Serialise persistent fields to a plain dict for Redis storage."""
        d: dict = {
            "symbol": self.symbol,
            "state": self.state.value if isinstance(self.state, TradeState) else self.state,
            "mode": self.mode.value if isinstance(self.mode, TradeMode) else self.mode,
            "long_exchange": self.long_exchange,
            "short_exchange": self.short_exchange,
            "funding_collections": self.funding_collections,
            "entry_tier": self.entry_tier,
        }
        for key in self._DECIMAL_FIELDS:
            val = getattr(self, key)
            d[key] = str(val) if val is not None else None
        for key in self._DATETIME_FIELDS:
            val = getattr(self, key)
            d[key] = val.isoformat() if val is not None else None
        return d

    @classmethod
    def from_persist_dict(cls, trade_id: str, data: dict) -> "TradeRecord":
        """Reconstruct a TradeRecord from a Redis-stored dict."""
        kwargs: dict = {
            "trade_id": trade_id,
            "symbol": data["symbol"],
            "state": TradeState(data.get("state", "open")),
            "mode": TradeMode(data.get("mode", "hold")),
            "long_exchange": data["long_exchange"],
            "short_exchange": data["short_exchange"],
            "funding_collections": int(data.get("funding_collections", 0)),
            "entry_tier": data.get("entry_tier"),
        }
        _zero_default_fields = {
            "funding_collected_usd",
            "_funding_tracked_long",
            "_funding_tracked_short",
            "_actual_long_funding_sum",
            "_actual_short_funding_sum",
        }
        for key in cls._DECIMAL_FIELDS:
            raw = data.get(key)
            kwargs[key] = Decimal(raw) if raw else (
                Decimal("0") if key in _zero_default_fields else None
            )
        for key in cls._DATETIME_FIELDS:
            raw = data.get(key)
            kwargs[key] = datetime.fromisoformat(raw) if raw else None
        # Legacy alias (entry_edge_bps → entry_edge_pct).
        if kwargs.get("entry_edge_pct") is None and data.get("entry_edge_bps"):
            kwargs["entry_edge_pct"] = Decimal(data["entry_edge_bps"])
        return cls(**kwargs)

    # ── Runtime state (not persisted to Redis) ───────────────────
    # These track in-memory monitoring state across monitor loop cycles.
    # Using field(compare=False, repr=False) keeps them out of equality
    # checks and debug output while still being properly typed.
    _funding_paid_long: bool = field(default=False, compare=False, repr=False)
    _funding_paid_short: bool = field(default=False, compare=False, repr=False)
    _exit_check_active: bool = field(default=False, compare=False, repr=False)
    _exit_wait_start: Optional[datetime] = field(default=None, compare=False, repr=False)
    _hold_logged_until: Optional[datetime] = field(default=None, compare=False, repr=False)
    _funding_paid_at: Optional[datetime] = field(default=None, compare=False, repr=False)
    _exit_reason: Optional[str] = field(default=None, compare=False, repr=False)
    # Cumulative exchange-history totals — persisted to Redis so that after a
    # crash+recovery the next payment delta is computed correctly (no double-count).
    _actual_long_funding_sum: Decimal = field(default=Decimal("0"), compare=False, repr=False)
    _actual_short_funding_sum: Decimal = field(default=Decimal("0"), compare=False, repr=False)
    # P2-1: Deferred funding history retry.
    # When T+0 history fetch returns estimate-only (exchange not yet settled),
    # record when to retry (90s later) and what estimate was used per side so
    # the correction can be computed exactly: real_amount − estimate_used.
    _funding_history_retry_at: Optional[datetime] = field(default=None, compare=False, repr=False)
    _pending_long_estimate: Optional[Decimal] = field(default=None, compare=False, repr=False)
    _pending_short_estimate: Optional[Decimal] = field(default=None, compare=False, repr=False)
    # Counts consecutive "stay for next cycle" decisions without basis recovery.
    # Prevents infinite hold loops on short-interval exchanges (e.g. Bybit 1h).
    _hold_cycles_stayed: int = field(default=0, compare=False, repr=False)
