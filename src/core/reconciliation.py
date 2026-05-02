"""
Per-trade portfolio reconciliation contracts.

Captured before entry and after close so the operator can verify that
each exchange balance moved by what was expected. Drift between
`net_delta` (real balance change across all exchanges) and `expected_pnl`
(what the bot computed) flags scenarios like the LAB phantom-PnL where
the internal view diverged from exchange truth.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Optional, Tuple


_ZERO = Decimal("0")
_DRIFT_TOLERANCE = Decimal("0.01")  # untouched-exchange threshold (USD)


@dataclass(frozen=True)
class BalanceSnapshot:
    """Total USDT-equivalent equity per exchange at a point in time."""

    captured_at: datetime
    balances: Dict[str, Decimal]
    failures: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "captured_at": self.captured_at.isoformat(),
            "balances": {ex: str(v) for ex, v in self.balances.items()},
            "failures": list(self.failures),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BalanceSnapshot":
        return cls(
            captured_at=datetime.fromisoformat(data["captured_at"]),
            balances={ex: Decimal(v) for ex, v in data.get("balances", {}).items()},
            failures=tuple(data.get("failures", [])),
        )


@dataclass(frozen=True)
class ReconciliationRecord:
    """Audit record for a single trade's portfolio impact across all exchanges."""

    trade_id: str
    symbol: str
    long_exchange: str
    short_exchange: str
    pre: Optional[BalanceSnapshot]
    post: BalanceSnapshot
    deltas: Dict[str, Decimal]
    net_delta: Decimal
    expected_pnl: Decimal
    drift: Decimal
    untouched_drift: Dict[str, Decimal]
    pair_flat: bool
    global_flat: bool
    flatness_failures: Tuple[str, ...] = ()
    partial: bool = False
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc),
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trade_id": self.trade_id,
            "symbol": self.symbol,
            "long_exchange": self.long_exchange,
            "short_exchange": self.short_exchange,
            "pre": self.pre.to_dict() if self.pre else None,
            "post": self.post.to_dict(),
            "deltas": {ex: str(v) for ex, v in self.deltas.items()},
            "net_delta": str(self.net_delta),
            "expected_pnl": str(self.expected_pnl),
            "drift": str(self.drift),
            "untouched_drift": {ex: str(v) for ex, v in self.untouched_drift.items()},
            "pair_flat": self.pair_flat,
            "global_flat": self.global_flat,
            "flatness_failures": list(self.flatness_failures),
            "partial": self.partial,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ReconciliationRecord":
        pre_data = data.get("pre")
        return cls(
            trade_id=data["trade_id"],
            symbol=data["symbol"],
            long_exchange=data["long_exchange"],
            short_exchange=data["short_exchange"],
            pre=BalanceSnapshot.from_dict(pre_data) if pre_data else None,
            post=BalanceSnapshot.from_dict(data["post"]),
            deltas={ex: Decimal(v) for ex, v in data.get("deltas", {}).items()},
            net_delta=Decimal(data.get("net_delta", "0")),
            expected_pnl=Decimal(data.get("expected_pnl", "0")),
            drift=Decimal(data.get("drift", "0")),
            untouched_drift={
                ex: Decimal(v) for ex, v in data.get("untouched_drift", {}).items()
            },
            pair_flat=bool(data.get("pair_flat", False)),
            global_flat=bool(data.get("global_flat", False)),
            flatness_failures=tuple(data.get("flatness_failures", [])),
            partial=bool(data.get("partial", False)),
            created_at=datetime.fromisoformat(data["created_at"]),
        )


def compute_deltas(
    pre: Optional[BalanceSnapshot],
    post: BalanceSnapshot,
) -> Dict[str, Decimal]:
    """Compute per-exchange balance change. Empty dict when no pre-snapshot.

    Only includes exchanges present in both snapshots and not in failures.
    """
    if pre is None:
        return {}
    skipped = set(pre.failures) | set(post.failures)
    deltas: Dict[str, Decimal] = {}
    for ex, post_val in post.balances.items():
        if ex in skipped:
            continue
        pre_val = pre.balances.get(ex)
        if pre_val is None:
            continue
        deltas[ex] = post_val - pre_val
    return deltas


def split_untouched_drift(
    deltas: Dict[str, Decimal],
    pair: Tuple[str, str],
    tolerance: Decimal = _DRIFT_TOLERANCE,
) -> Dict[str, Decimal]:
    """Return non-pair exchanges whose absolute delta exceeds tolerance.

    These are the surprises: exchanges the bot wasn't trading on that
    nonetheless moved, e.g. funding paid on an unrelated leftover position.
    """
    long_ex, short_ex = pair
    return {
        ex: d
        for ex, d in deltas.items()
        if ex not in (long_ex, short_ex) and abs(d) > tolerance
    }
