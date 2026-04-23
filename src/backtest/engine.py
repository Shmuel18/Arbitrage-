"""Event-driven backtest for one (symbol, exchange-pair) at a time.

MVP strategy — intentionally simpler than the live bot:

  Entry: at every funding event, pick the direction where
    ``rate_short_leg - rate_long_leg > min_net_pct + round_trip_cost_pct``.
    Long where funding is more negative, short where more positive.

  Hold: at each subsequent event, credit
    ``(rate_short_leg - rate_long_leg) × notional_usd``  as funding P&L.

  Exit triggers (first-to-fire wins):
    - ``max_collections``     — collected N funding payments
    - ``max_hold_timeout``    — held longer than ``max_hold_hours``
    - ``funding_flip``        — net funding rate turned negative against us
    - ``end_of_data``         — close at the final event if still open

Fees + slippage are modelled as constant percentages per leg (see
DEFAULT_TAKER_FEES + ``slippage_bps``). Order book depth is *not* modelled —
we assume the notional is small enough to fill at the OHLCV close.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from .data_loader import FundingEvent, build_events
from .portfolio import Trade

_ZERO = Decimal("0")

# Conservative round-trip taker rates per exchange (USDT perpetual futures,
# non-VIP tier). Override via ``BacktestConfig.taker_fees`` if you hold a
# VIP/BNB-discount account.
DEFAULT_TAKER_FEES: dict[str, Decimal] = {
    "binance": Decimal("0.0004"),
    "bybit":   Decimal("0.00055"),
    "kucoin":  Decimal("0.0006"),
    "gateio":  Decimal("0.0005"),
    "bitget":  Decimal("0.0006"),
}


@dataclass
class BacktestConfig:
    symbol: str
    exchange_a: str
    exchange_b: str
    funding_interval_hours: int = 8
    notional_usd: Decimal = Decimal("100")
    # Minimum net % AFTER round-trip fees + slippage to bother entering.
    # Mirrors config.yaml trading_params.min_funding_spread (0.3% → 0.003).
    min_net_pct: Decimal = Decimal("0.003")
    max_hold_hours: int = 72
    max_collections: int = 6
    # One-way slippage per leg in basis points; doubled for round-trip,
    # doubled again to cover both legs.
    slippage_bps: Decimal = Decimal("5")
    taker_fees: dict[str, Decimal] = field(default_factory=lambda: dict(DEFAULT_TAKER_FEES))

    def round_trip_cost_pct(self) -> Decimal:
        """Fees + slippage round-trip across both legs (as a decimal fraction)."""
        entry_fees = self.taker_fees[self.exchange_a] + self.taker_fees[self.exchange_b]
        slip = self.slippage_bps / Decimal("10000") * Decimal("2")  # two legs
        # Entry + exit = × 2
        return (entry_fees + slip) * Decimal("2")


@dataclass
class BacktestResult:
    cfg: BacktestConfig
    trades: list[Trade]

    @property
    def trade_count(self) -> int:
        return len(self.trades)

    @property
    def total_pnl_usd(self) -> Decimal:
        return sum((t.net_pnl_usd for t in self.trades), _ZERO)

    @property
    def win_rate(self) -> float:
        if not self.trades:
            return 0.0
        wins = sum(1 for t in self.trades if t.net_pnl_usd > 0)
        return wins / len(self.trades)

    @property
    def total_funding_usd(self) -> Decimal:
        return sum((t.funding_collected_usd for t in self.trades), _ZERO)

    @property
    def total_basis_usd(self) -> Decimal:
        return sum((t.basis_pnl_usd for t in self.trades), _ZERO)

    @property
    def total_fees_usd(self) -> Decimal:
        return sum((t.fees_usd for t in self.trades), _ZERO)


# ── strategy primitives ──────────────────────────────────────────────────


def _pick_direction(
    ev: FundingEvent, cfg: BacktestConfig
) -> tuple[str, str, Decimal] | None:
    """Return ``(long_exchange, short_exchange, net_pct_after_costs)`` or None.

    Captures funding by going **long** on the exchange with the more negative
    rate (shorts paying us) and **short** on the exchange with the more
    positive rate (longs paying us). Skips the event if we don't clear
    ``min_net_pct`` after round-trip fees + slippage.
    """
    r_a = ev.rates.get(cfg.exchange_a)
    r_b = ev.rates.get(cfg.exchange_b)
    if r_a is None or r_b is None:
        return None

    long_ex, short_ex = (cfg.exchange_a, cfg.exchange_b) if r_a < r_b else (cfg.exchange_b, cfg.exchange_a)
    gross = Decimal(str(ev.rates[short_ex] - ev.rates[long_ex]))
    net = gross - cfg.round_trip_cost_pct()
    if net < cfg.min_net_pct:
        return None
    return long_ex, short_ex, net


def _credit_funding(trade: Trade, ev: FundingEvent) -> bool:
    """Apply this event's funding P&L to ``trade`` in-place. Returns True iff credited."""
    long_r = ev.rates.get(trade.long_exchange)
    short_r = ev.rates.get(trade.short_exchange)
    if long_r is None or short_r is None:
        return False
    delta = (Decimal(str(short_r)) - Decimal(str(long_r))) * trade.notional_usd
    trade.funding_collected_usd += delta
    return True


def _close(trade: Trade, ev: FundingEvent, reason: str, cfg: BacktestConfig) -> None:
    trade.exit_ts_ms = ev.timestamp_ms
    trade.exit_long_price = Decimal(str(ev.prices[trade.long_exchange]))
    trade.exit_short_price = Decimal(str(ev.prices[trade.short_exchange]))
    trade.exit_reason = reason
    exit_fees = cfg.taker_fees[trade.long_exchange] + cfg.taker_fees[trade.short_exchange]
    exit_slip = cfg.slippage_bps / Decimal("10000") * Decimal("2")
    trade.fees_usd += (exit_fees + exit_slip) * trade.notional_usd


# ── main loop ────────────────────────────────────────────────────────────


def run_backtest(cfg: BacktestConfig) -> BacktestResult:
    events = build_events(
        cfg.symbol, [cfg.exchange_a, cfg.exchange_b], cfg.funding_interval_hours,
    )
    trades: list[Trade] = []
    open_trade: Optional[Trade] = None
    collections = 0

    for ev in events:
        # (1) Maybe enter.
        if open_trade is None:
            pick = _pick_direction(ev, cfg)
            if pick is None:
                continue
            long_ex, short_ex, _net = pick
            long_price = ev.prices.get(long_ex)
            short_price = ev.prices.get(short_ex)
            if long_price is None or short_price is None:
                continue
            entry_fees = cfg.taker_fees[long_ex] + cfg.taker_fees[short_ex]
            entry_slip = cfg.slippage_bps / Decimal("10000") * Decimal("2")
            open_trade = Trade(
                symbol=cfg.symbol,
                long_exchange=long_ex,
                short_exchange=short_ex,
                entry_ts_ms=ev.timestamp_ms,
                entry_long_price=Decimal(str(long_price)),
                entry_short_price=Decimal(str(short_price)),
                notional_usd=cfg.notional_usd,
                fees_usd=(entry_fees + entry_slip) * cfg.notional_usd,
            )
            collections = 0

        # (2) Credit funding for this event (the live bot enters just BEFORE a
        # funding tick, so the tick at ``entry_ts_ms`` is collected too).
        if _credit_funding(open_trade, ev):
            collections += 1

        # (3) Exit checks, priority order.
        long_r = ev.rates.get(open_trade.long_exchange, 0.0) or 0.0
        short_r = ev.rates.get(open_trade.short_exchange, 0.0) or 0.0
        current_net_rate = Decimal(str(short_r)) - Decimal(str(long_r))
        held_h = (ev.timestamp_ms - open_trade.entry_ts_ms) / 3_600_000

        if collections >= cfg.max_collections:
            _close(open_trade, ev, "max_collections", cfg)
        elif held_h >= cfg.max_hold_hours:
            _close(open_trade, ev, "max_hold_timeout", cfg)
        elif collections > 1 and current_net_rate <= 0:
            # Allow the entry event's rate to count even if marginal; only
            # after at least one collection do we treat a flip as a reason
            # to abandon the trade.
            _close(open_trade, ev, "funding_flip", cfg)

        if open_trade is not None and not open_trade.is_open:
            trades.append(open_trade)
            open_trade = None
            collections = 0

    # Close leftover trade at end of data so P&L is recognized.
    if open_trade is not None and events:
        _close(open_trade, events[-1], "end_of_data", cfg)
        trades.append(open_trade)

    return BacktestResult(cfg=cfg, trades=trades)
