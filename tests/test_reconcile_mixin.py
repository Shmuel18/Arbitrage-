"""Tests for per-trade portfolio reconciliation (PR1).

Covers:
- src/core/reconciliation.py — BalanceSnapshot, ReconciliationRecord,
  compute_deltas, split_untouched_drift
- src/execution/_reconcile_mixin.py — _snapshot_balances,
  _extract_total_equity, _build_reconciliation_record,
  _persist_reconciliation, _record_reconciliation, _capture_pre_snapshot
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.contracts import TradeMode, TradeRecord, TradeState
from src.core.reconciliation import (
    BalanceSnapshot,
    ReconciliationRecord,
    compute_deltas,
    split_untouched_drift,
)
from src.execution.controller import ExecutionController


# ── Helpers ────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _make_trade(trade_id: str = "tid-1", symbol: str = "BTC/USDT") -> TradeRecord:
    return TradeRecord(
        trade_id=trade_id,
        symbol=symbol,
        state=TradeState.CLOSED,
        long_exchange="exchange_a",
        short_exchange="exchange_b",
        long_qty=Decimal("0.01"),
        short_qty=Decimal("0.01"),
        entry_edge_pct=Decimal("0.5"),
    )


# ── Pure data-model tests ──────────────────────────────────────────

class TestBalanceSnapshot:
    def test_round_trip(self):
        snap = BalanceSnapshot(
            captured_at=_now(),
            balances={"binance": Decimal("1000.50"), "bybit": Decimal("500")},
            failures=("kucoin",),
        )
        restored = BalanceSnapshot.from_dict(snap.to_dict())
        assert restored.balances == snap.balances
        assert restored.failures == snap.failures
        assert restored.captured_at == snap.captured_at

    def test_decimal_precision_preserved_through_dict(self):
        snap = BalanceSnapshot(
            captured_at=_now(),
            balances={"binance": Decimal("1000.123456789012345")},
        )
        restored = BalanceSnapshot.from_dict(snap.to_dict())
        # Round-trip via str(Decimal) -> Decimal preserves all digits.
        assert restored.balances["binance"] == Decimal("1000.123456789012345")


class TestReconciliationRecord:
    def test_round_trip_with_pre(self):
        pre = BalanceSnapshot(
            captured_at=_now(),
            balances={"a": Decimal("1000"), "b": Decimal("500")},
        )
        post = BalanceSnapshot(
            captured_at=_now(),
            balances={"a": Decimal("999.40"), "b": Decimal("501.00")},
        )
        rec = ReconciliationRecord(
            trade_id="t1", symbol="BTC/USDT",
            long_exchange="a", short_exchange="b",
            pre=pre, post=post,
            deltas={"a": Decimal("-0.60"), "b": Decimal("1.00")},
            net_delta=Decimal("0.40"),
            expected_pnl=Decimal("0.50"),
            drift=Decimal("-0.10"),
            untouched_drift={},
            pair_flat=True, global_flat=True,
        )
        restored = ReconciliationRecord.from_dict(rec.to_dict())
        assert restored.trade_id == "t1"
        assert restored.deltas == rec.deltas
        assert restored.net_delta == rec.net_delta
        assert restored.drift == rec.drift
        assert restored.partial is False

    def test_round_trip_partial(self):
        post = BalanceSnapshot(captured_at=_now(), balances={"a": Decimal("999")})
        rec = ReconciliationRecord(
            trade_id="t2", symbol="ETH/USDT",
            long_exchange="a", short_exchange="b",
            pre=None, post=post,
            deltas={}, net_delta=Decimal("0"),
            expected_pnl=Decimal("1.5"),
            drift=Decimal("0"),
            untouched_drift={},
            pair_flat=True, global_flat=True,
            partial=True,
        )
        restored = ReconciliationRecord.from_dict(rec.to_dict())
        assert restored.pre is None
        assert restored.partial is True


class TestComputeDeltas:
    def test_basic_diff(self):
        pre = BalanceSnapshot(
            captured_at=_now(),
            balances={"a": Decimal("1000"), "b": Decimal("500")},
        )
        post = BalanceSnapshot(
            captured_at=_now(),
            balances={"a": Decimal("995"), "b": Decimal("506")},
        )
        deltas = compute_deltas(pre, post)
        assert deltas == {"a": Decimal("-5"), "b": Decimal("6")}

    def test_no_pre_returns_empty(self):
        post = BalanceSnapshot(captured_at=_now(), balances={"a": Decimal("1000")})
        assert compute_deltas(None, post) == {}

    def test_skips_failed_exchanges(self):
        pre = BalanceSnapshot(
            captured_at=_now(),
            balances={"a": Decimal("1000")},
            failures=("b",),  # b never had a pre value
        )
        post = BalanceSnapshot(
            captured_at=_now(),
            balances={"a": Decimal("1005"), "b": Decimal("500")},
        )
        deltas = compute_deltas(pre, post)
        assert "a" in deltas
        assert "b" not in deltas

    def test_skips_when_post_failed(self):
        pre = BalanceSnapshot(
            captured_at=_now(),
            balances={"a": Decimal("1000"), "b": Decimal("500")},
        )
        post = BalanceSnapshot(
            captured_at=_now(),
            balances={"a": Decimal("995")},
            failures=("b",),
        )
        deltas = compute_deltas(pre, post)
        assert "b" not in deltas

    def test_exchange_only_in_post_skipped(self):
        pre = BalanceSnapshot(captured_at=_now(), balances={"a": Decimal("1000")})
        post = BalanceSnapshot(
            captured_at=_now(),
            balances={"a": Decimal("995"), "c": Decimal("100")},
        )
        deltas = compute_deltas(pre, post)
        assert "c" not in deltas


class TestSplitUntouchedDrift:
    def test_pair_exchanges_excluded(self):
        deltas = {
            "a": Decimal("-5"),    # long leg — expected to move
            "b": Decimal("6"),     # short leg — expected to move
            "c": Decimal("0.50"),  # untouched — drifted
        }
        untouched = split_untouched_drift(deltas, ("a", "b"))
        assert "a" not in untouched
        assert "b" not in untouched
        assert untouched["c"] == Decimal("0.50")

    def test_below_tolerance_excluded(self):
        deltas = {
            "a": Decimal("-5"),
            "b": Decimal("6"),
            "c": Decimal("0.005"),  # below default $0.01 threshold
        }
        untouched = split_untouched_drift(deltas, ("a", "b"))
        assert untouched == {}

    def test_negative_drift_above_tolerance_included(self):
        deltas = {"a": Decimal("0"), "b": Decimal("0"), "c": Decimal("-0.50")}
        untouched = split_untouched_drift(deltas, ("a", "b"))
        assert untouched["c"] == Decimal("-0.50")


# ── Mixin behavior tests ────────────────────────────────────────────

@pytest.fixture
def controller(config, mock_exchange_mgr, mock_redis):
    """Instantiate the real ExecutionController with mock infra."""
    ctrl = ExecutionController(config, mock_exchange_mgr, mock_redis)
    # Replace the real journal with a mock so tests don't write to disk.
    ctrl._journal = MagicMock()
    return ctrl


class TestExtractTotalEquity:
    def test_prefers_total_when_positive(self):
        bal = {
            "total": Decimal("1500"),
            "free": Decimal("100"),
            "used": Decimal("200"),
        }
        assert ExecutionController._extract_total_equity(bal) == Decimal("1500")

    def test_falls_back_to_free_plus_used(self):
        bal = {"total": Decimal("0"), "free": Decimal("400"), "used": Decimal("100")}
        assert ExecutionController._extract_total_equity(bal) == Decimal("500")

    def test_handles_missing_total(self):
        bal = {"free": Decimal("400"), "used": Decimal("100")}
        assert ExecutionController._extract_total_equity(bal) == Decimal("500")

    def test_coerces_non_decimal(self):
        bal = {"free": 250.0, "used": "50"}
        assert ExecutionController._extract_total_equity(bal) == Decimal("300")


class TestSnapshotBalances:
    @pytest.mark.asyncio
    async def test_happy_path_two_exchanges(self, controller):
        snap = await controller._snapshot_balances()
        assert set(snap.balances.keys()) == {"exchange_a", "exchange_b"}
        # Each fixture adapter returns total=1000.
        assert snap.balances["exchange_a"] == Decimal("1000")
        assert snap.balances["exchange_b"] == Decimal("1000")
        assert snap.failures == ()

    @pytest.mark.asyncio
    async def test_one_failure_recorded(self, controller, mock_exchange_mgr):
        # Make exchange_b raise on get_balance.
        adapter_b = mock_exchange_mgr.all()["exchange_b"]
        adapter_b.get_balance.side_effect = RuntimeError("boom")
        adapter_b.get_balance_cached.side_effect = RuntimeError("boom")

        snap = await controller._snapshot_balances()
        assert "exchange_a" in snap.balances
        assert "exchange_b" not in snap.balances
        assert "exchange_b" in snap.failures

    @pytest.mark.asyncio
    async def test_uses_cached_when_requested(self, controller, mock_exchange_mgr):
        # Make non-cached get_balance fail; cached returns the fixture value.
        # If cached path is taken correctly, no failures should appear.
        for adapter in mock_exchange_mgr.all().values():
            adapter.get_balance.side_effect = RuntimeError("should not be called")
        snap = await controller._snapshot_balances(use_cached=True)
        assert "exchange_a" in snap.balances
        assert "exchange_b" in snap.balances
        assert snap.failures == ()


class TestBuildReconciliationRecord:
    def test_with_pre_full_math(self, controller):
        trade = _make_trade()
        pre = BalanceSnapshot(
            captured_at=_now(),
            balances={
                "exchange_a": Decimal("1000"),
                "exchange_b": Decimal("500"),
                "exchange_c": Decimal("250"),  # third exchange — should be untouched
            },
        )
        post = BalanceSnapshot(
            captured_at=_now(),
            balances={
                "exchange_a": Decimal("994.40"),  # long leg lost some
                "exchange_b": Decimal("506.00"),  # short leg gained some
                "exchange_c": Decimal("250.50"),  # surprise drift > tolerance
            },
        )
        rec = controller._build_reconciliation_record(
            trade, pre, post, expected_pnl=Decimal("0.50"),
        )
        assert rec.deltas["exchange_a"] == Decimal("-5.60")
        assert rec.deltas["exchange_b"] == Decimal("6.00")
        assert rec.deltas["exchange_c"] == Decimal("0.50")
        assert rec.net_delta == Decimal("0.90")
        assert rec.drift == Decimal("0.40")  # 0.90 - 0.50
        assert "exchange_c" in rec.untouched_drift
        assert "exchange_a" not in rec.untouched_drift
        assert rec.partial is False

    def test_without_pre_marks_partial(self, controller):
        trade = _make_trade()
        post = BalanceSnapshot(
            captured_at=_now(),
            balances={"exchange_a": Decimal("1000"), "exchange_b": Decimal("500")},
        )
        rec = controller._build_reconciliation_record(
            trade, None, post, expected_pnl=Decimal("1.50"),
        )
        assert rec.pre is None
        assert rec.partial is True
        assert rec.deltas == {}
        assert rec.net_delta == Decimal("0")
        # When partial, drift defaults to zero so callers can mask it.
        assert rec.drift == Decimal("0")


class TestPersistReconciliation:
    @pytest.mark.asyncio
    async def test_writes_to_redis_and_journal(self, controller, mock_redis):
        trade = _make_trade()
        pre = BalanceSnapshot(
            captured_at=_now(),
            balances={"exchange_a": Decimal("1000"), "exchange_b": Decimal("500")},
        )
        post = BalanceSnapshot(
            captured_at=_now(),
            balances={"exchange_a": Decimal("995"), "exchange_b": Decimal("506")},
        )
        rec = controller._build_reconciliation_record(
            trade, pre, post, expected_pnl=Decimal("1.0"),
        )
        await controller._persist_reconciliation(rec)
        mock_redis.set_reconciliation.assert_called_once()
        call = mock_redis.set_reconciliation.call_args
        assert call.args[0] == trade.trade_id
        assert call.args[1]["trade_id"] == trade.trade_id
        controller._journal.event.assert_called_once()
        evt = controller._journal.event.call_args
        assert evt.args[0] == "reconciliation"
        assert evt.kwargs["trade_id"] == trade.trade_id

    @pytest.mark.asyncio
    async def test_redis_failure_does_not_propagate(self, controller, mock_redis):
        mock_redis.set_reconciliation.side_effect = RuntimeError("redis down")
        trade = _make_trade()
        post = BalanceSnapshot(captured_at=_now(), balances={"exchange_a": Decimal("1000")})
        rec = controller._build_reconciliation_record(
            trade, None, post, expected_pnl=Decimal("0"),
        )
        # Must not raise — journal still attempted as a fallback.
        await controller._persist_reconciliation(rec)
        controller._journal.event.assert_called_once()


class TestRecordReconciliation:
    @pytest.mark.asyncio
    async def test_full_flow_pops_pre_snapshot(self, controller, mock_redis):
        trade = _make_trade()
        pre = BalanceSnapshot(
            captured_at=_now(),
            balances={"exchange_a": Decimal("1000"), "exchange_b": Decimal("500")},
        )
        controller._pending_pre_snapshots[trade.trade_id] = pre

        await controller._record_reconciliation(trade, expected_pnl=Decimal("0"))

        assert trade.trade_id not in controller._pending_pre_snapshots
        mock_redis.set_reconciliation.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_pre_snapshot_records_partial(self, controller, mock_redis):
        trade = _make_trade()
        # No entry in _pending_pre_snapshots
        await controller._record_reconciliation(trade, expected_pnl=Decimal("0"))

        mock_redis.set_reconciliation.assert_called_once()
        payload = mock_redis.set_reconciliation.call_args.args[1]
        assert payload["partial"] is True
        assert payload["pre"] is None

    @pytest.mark.asyncio
    async def test_swallows_snapshot_errors(self, controller, mock_exchange_mgr):
        # Make BOTH exchanges raise. _snapshot_balances handles this and
        # returns an empty snapshot, so _record_reconciliation should still
        # complete without raising.
        for adapter in mock_exchange_mgr.all().values():
            adapter.get_balance.side_effect = RuntimeError("boom")
            adapter.get_balance_cached.side_effect = RuntimeError("boom")
        trade = _make_trade()
        # Should NOT raise.
        await controller._record_reconciliation(trade, expected_pnl=Decimal("0"))


class TestCapturePreSnapshot:
    @pytest.mark.asyncio
    async def test_stores_into_pending_dict(self, controller):
        await controller._capture_pre_snapshot("tid-x")
        assert "tid-x" in controller._pending_pre_snapshots
        snap = controller._pending_pre_snapshots["tid-x"]
        assert isinstance(snap, BalanceSnapshot)
        assert "exchange_a" in snap.balances

    @pytest.mark.asyncio
    async def test_failure_does_not_raise(self, controller, mock_exchange_mgr):
        # Force ALL adapters to raise.
        for adapter in mock_exchange_mgr.all().values():
            adapter.get_balance.side_effect = RuntimeError("boom")
            adapter.get_balance_cached.side_effect = RuntimeError("boom")
        # Must not propagate.
        await controller._capture_pre_snapshot("tid-y")
        # Dict still gets a snapshot (with all failures listed) since
        # _snapshot_balances itself doesn't raise on per-adapter failures.
        assert "tid-y" in controller._pending_pre_snapshots
        snap = controller._pending_pre_snapshots["tid-y"]
        assert set(snap.failures) == {"exchange_a", "exchange_b"}
