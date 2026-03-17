"""Tests for execution controller — the critical safety path."""

import json
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from src.core.contracts import OrderSide, TradeMode, TradeRecord, TradeState
from src.execution.controller import ExecutionController


# ── MRO structural guards ────────────────────────────────────────────────

class TestExecutionControllerMRO:
    """Guard against silent method shadowing between ExecutionController mixins."""

    def test_mro_order_is_stable(self):
        """MRO must list mixins in the expected composition order."""
        from src.execution._close_finalize_mixin import _CloseFinalizeMixin
        from src.execution._close_mixin import _CloseMixin
        from src.execution._entry_mixin import _EntryMixin
        from src.execution._entry_orders_mixin import _EntryOrdersMixin
        from src.execution._exit_logic_mixin import _ExitLogicMixin
        from src.execution._monitor_mixin import _MonitorMixin
        from src.execution._util_mixin import _UtilMixin

        mro_names = [c.__name__ for c in ExecutionController.__mro__]

        assert mro_names[0] == "ExecutionController"
        # Parent–child chains must be correctly ordered
        assert mro_names.index("_EntryMixin") < mro_names.index("_EntryOrdersMixin")
        assert mro_names.index("_MonitorMixin") < mro_names.index("_ExitLogicMixin")
        assert mro_names.index("_CloseMixin") < mro_names.index("_CloseFinalizeMixin")

    def test_no_public_method_shadowed_between_sibling_mixins(self):
        """No two sibling mixins define the same public method."""
        from src.execution._close_mixin import _CloseMixin
        from src.execution._entry_mixin import _EntryMixin
        from src.execution._monitor_mixin import _MonitorMixin
        from src.execution._util_mixin import _UtilMixin

        siblings = [_EntryMixin, _MonitorMixin, _CloseMixin, _UtilMixin]

        seen: dict[str, str] = {}
        conflicts: list[str] = []
        for mixin in siblings:
            own_methods = {
                name for name, val in vars(mixin).items()
                if callable(val) and not name.startswith("__")
            }
            for name in own_methods:
                if name in seen:
                    conflicts.append(
                        f"{name!r} defined in both {seen[name]} and {mixin.__name__}"
                    )
                else:
                    seen[name] = mixin.__name__

        assert not conflicts, (
            "Silent method shadowing detected between sibling mixins:\n"
            + "\n".join(f"  • {c}" for c in conflicts)
        )

    def test_handle_opportunity_owned_by_entry_mixin(self):
        """handle_opportunity() must resolve to _EntryMixin."""
        from src.execution._entry_mixin import _EntryMixin
        assert ExecutionController.handle_opportunity is _EntryMixin.handle_opportunity

    def test_close_trade_owned_by_close_mixin(self):
        """_close_trade() must resolve to _CloseMixin."""
        from src.execution._close_mixin import _CloseMixin
        assert ExecutionController._close_trade is _CloseMixin._close_trade


@pytest.fixture
def controller(config, mock_exchange_mgr, mock_redis):
    return ExecutionController(config, mock_exchange_mgr, mock_redis)


class TestHandleOpportunity:
    @pytest.mark.asyncio
    async def test_opens_trade(self, controller, sample_opportunity, mock_redis):
        await controller.handle_opportunity(sample_opportunity)

        assert len(controller._active_trades) == 1
        trade = list(controller._active_trades.values())[0]
        assert trade.symbol == "BTC/USDT"
        assert trade.state == TradeState.OPEN
        mock_redis.set_trade_state.assert_called()

    @pytest.mark.asyncio
    async def test_rejects_duplicate_symbol(self, controller, sample_opportunity):
        await controller.handle_opportunity(sample_opportunity)
        await controller.handle_opportunity(sample_opportunity)

        assert len(controller._active_trades) == 1

    @pytest.mark.asyncio
    async def test_respects_concurrency_cap(self, controller, config, sample_opportunity):
        from src.core.contracts import OpportunityCandidate

        config.execution.concurrent_opportunities = 1
        await controller.handle_opportunity(sample_opportunity)

        # Second opp for different symbol — should be rejected due to cap
        opp2 = OpportunityCandidate(
            symbol="ETH/USDT",
            long_exchange="exchange_a", short_exchange="exchange_b",
            long_funding_rate=Decimal("0.0001"), short_funding_rate=Decimal("0.0005"),
            funding_spread_pct=Decimal("0.06"),
            immediate_spread_pct=Decimal("0.9"),
            immediate_net_pct=Decimal("0.7"),
            gross_edge_pct=Decimal("1.2"), fees_pct=Decimal("0.2"),
            net_edge_pct=Decimal("0.7"), suggested_qty=Decimal("0.1"),
            reference_price=Decimal("3000"),
        )
        await controller.handle_opportunity(opp2)
        assert len(controller._active_trades) == 1

    @pytest.mark.asyncio
    async def test_uses_filled_qty_not_requested(self, controller, sample_opportunity, mock_exchange_mgr):
        """Critical safety: trade record should use actual filled qty."""
        long_adapter = mock_exchange_mgr.get("exchange_a")
        long_adapter.place_order.return_value = {
            "id": "o1", "filled": 0.008, "average": 50000, "status": "closed",
        }
        # Short must match long's partial fill to stay delta-neutral
        short_adapter = mock_exchange_mgr.get("exchange_b")
        short_adapter.place_order.return_value = {
            "id": "o2", "filled": 0.008, "average": 50000, "status": "closed",
        }

        await controller.handle_opportunity(sample_opportunity)

        trade = list(controller._active_trades.values())[0]
        assert trade.long_qty == Decimal("0.008")
        assert trade.short_qty == Decimal("0.008")  # delta neutral

    @pytest.mark.asyncio
    async def test_blocks_entry_when_price_spread_is_adverse(
        self,
        controller,
        sample_opportunity,
    ):
        """Adverse scanner spread must be hard-blocked before any order execution."""
        adverse_opp = replace(sample_opportunity, price_spread_pct=Decimal("0.15"))

        controller._execute_entry_orders = AsyncMock(return_value=None)

        await controller.handle_opportunity(adverse_opp)

        assert len(controller._active_trades) == 0
        controller._execute_entry_orders.assert_not_called()

    @pytest.mark.asyncio
    async def test_rejects_post_entry_adverse_basis_and_emergency_closes(
        self,
        controller,
        sample_opportunity,
        mock_redis,
    ):
        """Adverse realized entry basis must trigger immediate rejection and unwind."""
        long_spec = AsyncMock()
        long_spec.taker_fee = Decimal("0.0005")
        short_spec = AsyncMock()
        short_spec.taker_fee = Decimal("0.0005")

        controller._execute_entry_orders = AsyncMock(
            return_value={
                "order_qty": Decimal("0.01"),
                "long_filled_qty": Decimal("0.01"),
                "short_filled_qty": Decimal("0.01"),
                "entry_price_long": Decimal("50000"),
                "entry_price_short": Decimal("49900"),
                "entry_fees": Decimal("0.5"),
                "long_spec": long_spec,
                "short_spec": short_spec,
                "entry_basis_pct": Decimal("1.25"),
            }
        )

        close_calls = []

        async def _mock_place_with_timeout(adapter, req):
            close_calls.append(req)
            return {"id": "close", "filled": float(req.quantity), "average": 50000.0}

        controller._place_with_timeout = _mock_place_with_timeout

        await controller.handle_opportunity(sample_opportunity)

        assert len(controller._active_trades) == 0
        assert len(close_calls) == 2
        assert all(req.reduce_only for req in close_calls)
        assert {req.side for req in close_calls} == {OrderSide.SELL, OrderSide.BUY}
        mock_redis.set_trade_state.assert_not_called()


class TestCloseOrphan:
    @pytest.mark.asyncio
    async def test_orphan_closes_long_when_short_fails(self, controller, sample_opportunity, mock_exchange_mgr, mock_redis):
        """If short leg fails, long leg must be closed with reduceOnly."""
        short_adapter = mock_exchange_mgr.get("exchange_b")
        short_adapter.place_order.return_value = None  # simulate timeout

        # Patch _place_with_timeout to return None for short
        call_count = 0
        original = controller._place_with_timeout

        async def mock_place(adapter, req):
            nonlocal call_count
            call_count += 1
            if call_count == 1:  # long leg
                return {"id": "o1", "filled": 0.01, "average": 50000, "status": "closed"}
            return None  # short leg fails

        controller._place_with_timeout = mock_place
        await controller.handle_opportunity(sample_opportunity)

        # No active trades (orphan was handled)
        assert len(controller._active_trades) == 0
        # Cooldown should be set
        mock_redis.set_cooldown.assert_called()


class TestDeltaCorrection:
    @pytest.mark.asyncio
    async def test_trims_long_when_short_partial_fill(
        self, controller, sample_opportunity, mock_exchange_mgr, mock_redis
    ):
        """If short leg partially fills, excess on long must be trimmed (reduceOnly)."""
        call_count = 0

        async def mock_place(adapter, req):
            nonlocal call_count
            call_count += 1
            if call_count == 1:  # long leg — full fill
                return {"id": "o1", "filled": 0.01, "average": 50000, "status": "closed"}
            if call_count == 2:  # short leg — partial fill (only 0.007)
                return {"id": "o2", "filled": 0.007, "average": 50000, "status": "closed"}
            # call_count == 3 → trim order on long side (reduceOnly)
            return {"id": "o3", "filled": 0.003, "average": 50000, "status": "closed"}

        controller._place_with_timeout = mock_place
        await controller.handle_opportunity(sample_opportunity)

        # Trade should be opened with balanced quantities
        assert len(controller._active_trades) == 1
        trade = list(controller._active_trades.values())[0]
        assert trade.long_qty == trade.short_qty  # delta neutral
        assert trade.long_qty == Decimal("0.007")
        # 3 orders placed: long, short, trim
        assert call_count == 3


class TestRecovery:
    @pytest.mark.asyncio
    async def test_recovers_open_trade_from_redis(self, config, mock_exchange_mgr, mock_redis):
        mock_redis.get_all_trades.return_value = {
            "abc123": {
                "symbol": "BTC/USDT",
                "state": "open",
                "long_exchange": "exchange_a",
                "short_exchange": "exchange_b",
                "long_qty": "0.01",
                "short_qty": "0.01",
                "entry_edge_pct": "1.0",
                "opened_at": "2026-01-01T00:00:00",
            }
        }

        ctrl = ExecutionController(config, mock_exchange_mgr, mock_redis)
        await ctrl._recover_trades()

        assert "abc123" in ctrl._active_trades
        assert ctrl._active_trades["abc123"].state == TradeState.OPEN


# ── Helper: build a TradeRecord already in the controller ────────

def _make_trade(controller, symbol="BTC/USDT", spread_pct="1.0",
                long_ex="exchange_a", short_ex="exchange_b",
                funding_paid=True, opened_minutes_ago=30):
    """Insert a trade directly into the controller and return it."""
    now = datetime.now(timezone.utc)
    trade = TradeRecord(
        trade_id="test-trade-1",
        symbol=symbol,
        state=TradeState.OPEN,
        long_exchange=long_ex,
        short_exchange=short_ex,
        long_qty=Decimal("0.01"),
        short_qty=Decimal("0.01"),
        entry_edge_pct=Decimal(spread_pct),
        opened_at=now - timedelta(minutes=opened_minutes_ago),
        mode=TradeMode.HOLD,
    )
    if funding_paid:
        # Set next_funding to the past so funding is considered paid
        trade.next_funding_long = now - timedelta(minutes=20)
        trade.next_funding_short = now - timedelta(minutes=20)
    else:
        trade.next_funding_long = now + timedelta(minutes=30)
        trade.next_funding_short = now + timedelta(minutes=30)
    controller._active_trades[trade.trade_id] = trade
    return trade


class TestHoldOrExit:
    """Tests for the Hold-or-Exit feature after funding collection."""

    @pytest.mark.asyncio
    async def test_holds_when_spread_above_threshold(
        self, controller, config, mock_exchange_mgr
    ):
        """After funding, basis is adverse (current < entry) so should HOLD while waiting for recovery."""
        config.trading_params.exit_offset_seconds = 0  # instant for testing
        config.trading_params.basis_recovery_timeout_minutes = Decimal("30")

        # Set exchange funding rates to produce a high spread (0.6%)
        for eid in ("exchange_a", "exchange_b"):
            adapter = mock_exchange_mgr.get(eid)
            past_ms = (time.time() - 1200) * 1000  # 20 min ago
            if eid == "exchange_a":
                data = {
                    "rate": Decimal("-0.0030"),
                    "next_timestamp": past_ms,
                    "interval_hours": 8,
                }
            else:
                data = {
                    "rate": Decimal("0.0030"),
                    "next_timestamp": past_ms,
                    "interval_hours": 8,
                }
            adapter.get_funding_rate.return_value = data
            adapter._funding_rate_cache["BTC/USDT"] = data

        trade = _make_trade(controller, spread_pct="1.0")
        trade.entry_price_long = Decimal("50000")
        trade.entry_price_short = Decimal("50000")
        # Entry basis = 0%, current long dropped → adverse basis (price loss)
        trade.entry_basis_pct = Decimal("0")
        # Long price dropped, short stayed → basis below entry (adverse)
        mock_exchange_mgr.get("exchange_a").get_ticker.return_value = {"last": 49800.0}
        mock_exchange_mgr.get("exchange_b").get_ticker.return_value = {"last": 50000.0}

        await controller._check_exit(trade)

        # Trade should still be open — basis adverse, within recovery timeout
        assert trade.trade_id in controller._active_trades
        assert trade.state == TradeState.OPEN

    @pytest.mark.asyncio
    async def test_exits_when_next_funding_too_far(
        self, controller, config, mock_exchange_mgr, mock_redis
    ):
        """After 1.5h timeout + next funding doesn't qualify → EXIT."""
        config.trading_params.exit_offset_seconds = 0

        # Low funding rates (won't qualify for next cycle)
        past_ms = (time.time() - 1200) * 1000
        for eid in ("exchange_a", "exchange_b"):
            adapter = mock_exchange_mgr.get(eid)
            rate = Decimal("-0.0001") if eid == "exchange_a" else Decimal("0.0001")
            data = {"rate": rate, "next_timestamp": past_ms, "interval_hours": 8}
            adapter.get_funding_rate.return_value = data
            adapter._funding_rate_cache["BTC/USDT"] = data

        trade = _make_trade(controller, spread_pct="1.0")
        trade.entry_price_long = Decimal("50000")
        trade.entry_price_short = Decimal("50000")
        # Pre-set: funding already collected, timeout elapsed (2h > 1.5h)
        trade._exit_check_active = True
        trade._funding_paid_long = True
        trade._funding_paid_short = True
        trade._funding_paid_at = datetime.now(timezone.utc) - timedelta(hours=2)
        trade.funding_collections = 1

        await controller._check_exit(trade)

        # Should have exited — timeout reached, next funding doesn't qualify
        assert trade.trade_id not in controller._active_trades

    @pytest.mark.asyncio
    async def test_holds_when_next_funding_within_max_wait(
        self, controller, config, mock_exchange_mgr
    ):
        """After funding, basis adverse but within recovery timeout → HOLD."""
        config.trading_params.exit_offset_seconds = 0
        config.trading_params.basis_recovery_timeout_minutes = Decimal("30")

        # Good spread AND next funding in 30 min (within 1h limit)
        future_30m_ms = (time.time() + 1800) * 1000
        for eid in ("exchange_a", "exchange_b"):
            adapter = mock_exchange_mgr.get(eid)
            if eid == "exchange_a":
                data = {
                    "rate": Decimal("-0.0030"),
                    "next_timestamp": future_30m_ms,
                    "interval_hours": 8,
                }
            else:
                data = {
                    "rate": Decimal("0.0030"),
                    "next_timestamp": future_30m_ms,
                    "interval_hours": 8,
                }
            adapter.get_funding_rate.return_value = data
            adapter._funding_rate_cache["BTC/USDT"] = data

        trade = _make_trade(controller, spread_pct="1.0")
        trade.entry_price_long = Decimal("50000")
        trade.entry_price_short = Decimal("50000")
        trade.entry_basis_pct = Decimal("0")
        trade._funding_paid_long = True
        trade._funding_paid_short = True
        # Adverse basis: long dropped → basis below entry (price loss)
        mock_exchange_mgr.get("exchange_a").get_ticker.return_value = {"last": 49700.0}
        mock_exchange_mgr.get("exchange_b").get_ticker.return_value = {"last": 50000.0}

        await controller._check_exit(trade)

        # Should still be holding — basis adverse, within recovery timeout
        assert trade.trade_id in controller._active_trades
        assert trade.state == TradeState.OPEN

    @pytest.mark.asyncio
    async def test_exits_when_spread_below_threshold(
        self, controller, config, mock_exchange_mgr, mock_redis
    ):
        """If PnL >= profit_target_pct (0.7%) on notional → EXIT."""
        config.trading_params.exit_offset_seconds = 0
        config.trading_params.profit_target_pct = Decimal("0.7")

        # Funding rates to trigger collection
        past_ms = (time.time() - 1200) * 1000
        for eid in ("exchange_a", "exchange_b"):
            adapter = mock_exchange_mgr.get(eid)
            rate = Decimal("-0.0001") if eid == "exchange_a" else Decimal("0.0001")
            data = {"rate": rate, "next_timestamp": past_ms, "interval_hours": 8}
            adapter.get_funding_rate.return_value = data
            adapter._funding_rate_cache["BTC/USDT"] = data

        # Set prices: long up 1.0% → after exit_slippage_buffer (0.3%) → adj PnL 0.7% = target
        # entry: 50000, current long: 50500 → PnL = 500/50000 * 100 = 1.0%
        mock_exchange_mgr.get("exchange_a").get_ticker.return_value = {"last": 50500.0}
        mock_exchange_mgr.get("exchange_b").get_ticker.return_value = {"last": 50000.0}

        trade = _make_trade(controller, spread_pct="1.0")
        trade.entry_price_long = Decimal("50000")
        trade.entry_price_short = Decimal("50000")

        await controller._check_exit(trade)

        # Trade should have been closed — profit target hit
        assert trade.trade_id not in controller._active_trades

    @pytest.mark.asyncio
    async def test_exits_when_no_funding_received_after_threshold(
        self, controller, config, mock_exchange_mgr, mock_redis
    ):
        """Trade where WS cache never populated next_timestamp → payment tracker
        never set → bot stuck indefinitely. Safety exit fires after
        max_entry_window_minutes + basis_recovery_timeout_minutes."""
        config.trading_params.max_entry_window_minutes = 60
        config.trading_params.basis_recovery_timeout_minutes = Decimal("30")

        # Cache has NO next_timestamp for KITE — simulates the KITE/GateIO bug
        for eid in ("exchange_a", "exchange_b"):
            adapter = mock_exchange_mgr.get(eid)
            adapter._funding_rate_cache["BTC/USDT"] = None  # cache empty
        mock_exchange_mgr.get("exchange_a").get_ticker.return_value = {"last": 50000.0}
        mock_exchange_mgr.get("exchange_b").get_ticker.return_value = {"last": 50000.0}

        # Trade opened 100 min ago — past the 90-min threshold (60+30)
        trade = _make_trade(controller, spread_pct="1.0",
                            opened_minutes_ago=100, funding_paid=False)
        # Next_funding trackers are never set (cache was always empty)
        trade.next_funding_long = None
        trade.next_funding_short = None
        trade._funding_paid_at = None
        trade.funding_collected_usd = Decimal("0")
        trade.entry_price_long = Decimal("50000")
        trade.entry_price_short = Decimal("50000")

        await controller._check_exit(trade)

        # Should have exited — no funding received after threshold
        assert trade.trade_id not in controller._active_trades

    @pytest.mark.asyncio
    async def test_does_not_exit_early_when_no_funding_received_below_threshold(
        self, controller, config, mock_exchange_mgr
    ):
        """No-funding safety exit must NOT fire before the threshold. 
        Trade open 50 min < 90 min (60+30) — should still hold."""
        config.trading_params.max_entry_window_minutes = 60
        config.trading_params.basis_recovery_timeout_minutes = Decimal("30")
        config.trading_params.exit_offset_seconds = 0

        for eid in ("exchange_a", "exchange_b"):
            adapter = mock_exchange_mgr.get(eid)
            adapter._funding_rate_cache["BTC/USDT"] = None
        mock_exchange_mgr.get("exchange_a").get_ticker.return_value = {"last": 50000.0}
        mock_exchange_mgr.get("exchange_b").get_ticker.return_value = {"last": 50000.0}

        # Trade opened only 50 min ago — below the 90-min threshold
        trade = _make_trade(controller, spread_pct="1.0",
                            opened_minutes_ago=50, funding_paid=False)
        trade.next_funding_long = None
        trade.next_funding_short = None
        trade._funding_paid_at = None
        trade.funding_collected_usd = Decimal("0")
        trade.entry_price_long = Decimal("50000")
        trade.entry_price_short = Decimal("50000")

        await controller._check_exit(trade)

        # Should still be open — threshold not yet reached
        assert trade.trade_id in controller._active_trades
        assert trade.state == TradeState.OPEN


class TestUpgrade:
    """Tests for the Upgrade feature — switch to a better opportunity."""

    @pytest.mark.asyncio
    async def test_upgrades_when_better_opp_found(
        self, controller, config, mock_exchange_mgr, mock_redis
    ):
        """Close trade when a much better qualified opp is available."""
        config.trading_params.upgrade_spread_delta = Decimal("0.5")
        config.trading_params.entry_offset_seconds = 900

        # Current trade has spread ~0.16% (small rates)
        # Must populate _funding_rate_cache since _check_upgrade uses get_funding_rate_cached()
        for eid in ("exchange_a", "exchange_b"):
            adapter = mock_exchange_mgr.get(eid)
            if eid == "exchange_a":
                data = {
                    "rate": Decimal("-0.0001"),
                    "next_timestamp": (time.time() + 600) * 1000,
                    "interval_hours": 8,
                }
            else:
                data = {
                    "rate": Decimal("0.0001"),
                    "next_timestamp": (time.time() + 600) * 1000,
                    "interval_hours": 8,
                }
            adapter.get_funding_rate.return_value = data
            adapter._funding_rate_cache["BTC/USDT"] = data

        trade = _make_trade(controller, spread_pct="0.5", funding_paid=False)

        # Redis has a qualified opp with much higher spread, in entry window
        better_opp = {
            "symbol": "DOGE/USDT",
            "long_exchange": "exchange_a",
            "short_exchange": "exchange_b",
            "immediate_spread_pct": 1.5,  # 1.5% >> current ~0.16% + 0.5%
            "qualified": True,
            "next_funding_ms": (time.time() + 600) * 1000,  # 10 min away
        }
        mock_redis.get.return_value = json.dumps({
            "opportunities": [better_opp],
            "count": 1,
        })

        upgraded = await controller._check_upgrade(trade)

        assert upgraded is True
        assert trade.trade_id not in controller._active_trades

    @pytest.mark.asyncio
    async def test_no_upgrade_when_delta_too_small(
        self, controller, config, mock_exchange_mgr, mock_redis
    ):
        """Don't upgrade if the better opp isn't +0.5% higher."""
        config.trading_params.upgrade_spread_delta = Decimal("0.5")
        config.trading_params.entry_offset_seconds = 900

        # Current trade spread = ~0.16%
        # Must populate _funding_rate_cache since _check_upgrade uses get_funding_rate_cached()
        for eid in ("exchange_a", "exchange_b"):
            adapter = mock_exchange_mgr.get(eid)
            if eid == "exchange_a":
                data = {
                    # Higher rates → current_immediate=0.6%, net=0.4%, threshold=0.9%
                    "rate": Decimal("-0.0030"),
                    "next_timestamp": (time.time() + 600) * 1000,
                    "interval_hours": 8,
                }
            else:
                data = {
                    "rate": Decimal("0.0030"),
                    "next_timestamp": (time.time() + 600) * 1000,
                    "interval_hours": 8,
                }
            adapter.get_funding_rate.return_value = data
            adapter._funding_rate_cache["BTC/USDT"] = data

        trade = _make_trade(controller, spread_pct="0.5", funding_paid=False)

        # Redis opp is only slightly better — not enough for upgrade
        # current_projected_net=0.4%, threshold=0.4+0.5=0.9%; candidate(0.4%) < 0.9% → no upgrade
        weak_opp = {
            "symbol": "DOGE/USDT",
            "long_exchange": "exchange_a",
            "short_exchange": "exchange_b",
            "immediate_spread_pct": 0.4,  # < 0.9% threshold → not enough delta
            "qualified": True,
            "next_funding_ms": (time.time() + 600) * 1000,
        }
        mock_redis.get.return_value = json.dumps({
            "opportunities": [weak_opp],
            "count": 1,
        })

        upgraded = await controller._check_upgrade(trade)

        assert upgraded is False
        assert trade.trade_id in controller._active_trades

    @pytest.mark.asyncio
    async def test_no_upgrade_outside_entry_window(
        self, controller, config, mock_exchange_mgr, mock_redis
    ):
        """Don't upgrade if the better opp isn't in the 15-min entry window."""
        config.trading_params.upgrade_spread_delta = Decimal("0.5")
        config.trading_params.entry_offset_seconds = 900

        for eid in ("exchange_a", "exchange_b"):
            adapter = mock_exchange_mgr.get(eid)
            if eid == "exchange_a":
                data = {
                    "rate": Decimal("-0.0001"),
                    "next_timestamp": (time.time() + 600) * 1000,
                    "interval_hours": 8,
                }
            else:
                data = {
                    "rate": Decimal("0.0001"),
                    "next_timestamp": (time.time() + 600) * 1000,
                    "interval_hours": 8,
                }
            adapter.get_funding_rate.return_value = data
            adapter._funding_rate_cache["BTC/USDT"] = data

        trade = _make_trade(controller, spread_pct="0.5", funding_paid=False)

        # Great spread but funding is 2 hours away (outside 15-min window)
        far_opp = {
            "symbol": "DOGE/USDT",
            "long_exchange": "exchange_a",
            "short_exchange": "exchange_b",
            "immediate_spread_pct": 2.0,
            "qualified": True,
            "next_funding_ms": (time.time() + 7200) * 1000,  # 2 hours away
        }
        mock_redis.get.return_value = json.dumps({
            "opportunities": [far_opp],
            "count": 1,
        })

        upgraded = await controller._check_upgrade(trade)

        assert upgraded is False
        assert trade.trade_id in controller._active_trades

    @pytest.mark.asyncio
    async def test_no_upgrade_for_same_symbol(
        self, controller, config, mock_exchange_mgr, mock_redis
    ):
        """Don't upgrade to the same symbol we're already trading."""
        config.trading_params.upgrade_spread_delta = Decimal("0.5")
        config.trading_params.entry_offset_seconds = 900

        for eid in ("exchange_a", "exchange_b"):
            adapter = mock_exchange_mgr.get(eid)
            if eid == "exchange_a":
                data = {
                    "rate": Decimal("-0.0001"),
                    "next_timestamp": (time.time() + 600) * 1000,
                    "interval_hours": 8,
                }
            else:
                data = {
                    "rate": Decimal("0.0001"),
                    "next_timestamp": (time.time() + 600) * 1000,
                    "interval_hours": 8,
                }
            adapter.get_funding_rate.return_value = data
            adapter._funding_rate_cache["BTC/USDT"] = data

        trade = _make_trade(controller, spread_pct="0.5", funding_paid=False)

        # Same symbol — should not trigger upgrade
        same_opp = {
            "symbol": "BTC/USDT",
            "long_exchange": "exchange_a",
            "short_exchange": "exchange_b",
            "immediate_spread_pct": 3.0,
            "qualified": True,
            "next_funding_ms": (time.time() + 600) * 1000,
        }
        mock_redis.get.return_value = json.dumps({
            "opportunities": [same_opp],
            "count": 1,
        })

        upgraded = await controller._check_upgrade(trade)

        assert upgraded is False

    @pytest.mark.asyncio
    async def test_upgrade_sets_cooldown_on_closed_symbol(
        self, controller, config, mock_exchange_mgr, mock_redis
    ):
        """After upgrade, the closed symbol should be in upgrade cooldown."""
        config.trading_params.upgrade_spread_delta = Decimal("0.5")
        config.trading_params.upgrade_cooldown_seconds = 300
        config.trading_params.entry_offset_seconds = 900

        for eid in ("exchange_a", "exchange_b"):
            adapter = mock_exchange_mgr.get(eid)
            if eid == "exchange_a":
                data = {
                    "rate": Decimal("-0.0001"),
                    "next_timestamp": (time.time() + 600) * 1000,
                    "interval_hours": 8,
                }
            else:
                data = {
                    "rate": Decimal("0.0001"),
                    "next_timestamp": (time.time() + 600) * 1000,
                    "interval_hours": 8,
                }
            adapter.get_funding_rate.return_value = data
            adapter._funding_rate_cache["BTC/USDT"] = data

        trade = _make_trade(controller, spread_pct="0.5", funding_paid=False)

        better_opp = {
            "symbol": "DOGE/USDT",
            "long_exchange": "exchange_a",
            "short_exchange": "exchange_b",
            "immediate_spread_pct": 1.5,
            "qualified": True,
            "next_funding_ms": (time.time() + 600) * 1000,
        }
        mock_redis.get.return_value = json.dumps({
            "opportunities": [better_opp],
            "count": 1,
        })

        upgraded = await controller._check_upgrade(trade)

        assert upgraded is True
        # The CLOSED symbol (BTC/USDT) should now be in upgrade cooldown
        assert "BTC/USDT" in controller._upgrade_cooldown
        assert controller._upgrade_cooldown["BTC/USDT"] > time.time()

    @pytest.mark.asyncio
    async def test_upgrade_cooldown_blocks_reentry(
        self, controller, sample_opportunity, mock_redis
    ):
        """Symbol in upgrade cooldown should be rejected at entry gate."""
        # Set upgrade cooldown for the symbol (5 min from now)
        controller._upgrade_cooldown["BTC/USDT"] = time.time() + 300

        await controller.handle_opportunity(sample_opportunity)

        # Should NOT have opened a trade
        assert len(controller._active_trades) == 0

    @pytest.mark.asyncio
    async def test_upgrade_cooldown_expires(
        self, controller, sample_opportunity, mock_redis
    ):
        """Expired upgrade cooldown should allow re-entry."""
        # Set upgrade cooldown in the PAST (already expired)
        controller._upgrade_cooldown["BTC/USDT"] = time.time() - 1

        await controller.handle_opportunity(sample_opportunity)

        # Should have opened normally
        assert len(controller._active_trades) == 1
        # Expired entry should be cleaned up
        assert "BTC/USDT" not in controller._upgrade_cooldown


# ── Tests for _check_exit advanced branches ──────────────────────

class TestCherryPickHardExit:
    """Cherry-pick trades must close when exit_before is reached."""

    @pytest.mark.asyncio
    async def test_closes_when_exit_before_reached(
        self, controller, config, mock_exchange_mgr, mock_redis
    ):
        now = datetime.now(timezone.utc)
        trade = _make_trade(controller, spread_pct="1.0")
        trade.mode = TradeMode.CHERRY_PICK
        trade.exit_before = now - timedelta(minutes=1)  # already past

        await controller._check_exit(trade)

        assert trade.trade_id not in controller._active_trades

    @pytest.mark.asyncio
    async def test_does_not_close_before_exit_time(
        self, controller, config, mock_exchange_mgr
    ):
        """Cherry-pick should NOT hard-exit if exit_before is still in the future."""
        now = datetime.now(timezone.utc)
        config.trading_params.exit_offset_seconds = 0
        config.trading_params.basis_recovery_timeout_minutes = Decimal("30")

        # High spread so it would HOLD
        for eid in ("exchange_a", "exchange_b"):
            adapter = mock_exchange_mgr.get(eid)
            past_ms = (time.time() - 1200) * 1000
            rate = Decimal("-0.0030") if eid == "exchange_a" else Decimal("0.0030")
            data = {"rate": rate, "next_timestamp": past_ms, "interval_hours": 8}
            adapter._funding_rate_cache["BTC/USDT"] = data

        trade = _make_trade(controller, spread_pct="1.0")
        trade.mode = TradeMode.CHERRY_PICK
        trade.exit_before = now + timedelta(hours=2)  # far in future
        trade.entry_price_long = Decimal("50000")
        trade.entry_price_short = Decimal("50000")
        trade.entry_basis_pct = Decimal("0")
        # Adverse basis (long dropped) so it doesn't trigger basis_recovery exit
        mock_exchange_mgr.get("exchange_a").get_ticker.return_value = {"last": 49800.0}
        mock_exchange_mgr.get("exchange_b").get_ticker.return_value = {"last": 50000.0}

        await controller._check_exit(trade)

        # Still open — not yet time to exit, basis adverse (within timeout)
        assert trade.trade_id in controller._active_trades


class TestBasisGuard:
    """Tests for tier-aware profit target and timeout exit."""

    @pytest.mark.asyncio
    async def test_waits_when_spread_low_and_basis_adverse(
        self, controller, config, mock_exchange_mgr
    ):
        """PnL below target, basis adverse, within recovery timeout → holds."""
        config.trading_params.exit_offset_seconds = 0
        config.trading_params.basis_recovery_timeout_minutes = Decimal("30")

        past_ms = (time.time() - 1200) * 1000
        for eid in ("exchange_a", "exchange_b"):
            adapter = mock_exchange_mgr.get(eid)
            rate = Decimal("-0.0001") if eid == "exchange_a" else Decimal("0.0001")
            data = {"rate": rate, "next_timestamp": past_ms, "interval_hours": 8}
            adapter._funding_rate_cache["BTC/USDT"] = data

        # Adverse basis: long dropped → current basis < entry basis (price loss)
        mock_exchange_mgr.get("exchange_a").get_ticker.return_value = {"last": 49800.0}
        mock_exchange_mgr.get("exchange_b").get_ticker.return_value = {"last": 50000.0}

        trade = _make_trade(controller, spread_pct="1.0")
        trade.entry_price_long = Decimal("50000")
        trade.entry_price_short = Decimal("50000")
        trade.entry_basis_pct = Decimal("0")  # entry: equal prices

        await controller._check_exit(trade)

        # Should still be open — basis adverse, within recovery timeout
        assert trade.trade_id in controller._active_trades
        assert trade._funding_paid_at is not None

    @pytest.mark.asyncio
    async def test_exits_on_basis_timeout(
        self, controller, config, mock_exchange_mgr, mock_redis
    ):
        """After 1.5h timeout with no qualifying next funding → EXIT."""
        config.trading_params.exit_offset_seconds = 0

        past_ms = (time.time() - 1200) * 1000
        for eid in ("exchange_a", "exchange_b"):
            adapter = mock_exchange_mgr.get(eid)
            rate = Decimal("-0.0001") if eid == "exchange_a" else Decimal("0.0001")
            data = {"rate": rate, "next_timestamp": past_ms, "interval_hours": 8}
            adapter._funding_rate_cache["BTC/USDT"] = data

        mock_exchange_mgr.get("exchange_a").get_ticker.return_value = {"last": 50000.0}
        mock_exchange_mgr.get("exchange_b").get_ticker.return_value = {"last": 50000.0}

        trade = _make_trade(controller, spread_pct="1.0")
        trade.entry_price_long = Decimal("50000")
        trade.entry_price_short = Decimal("50000")
        # Pre-set: funding collected, 2h have passed (> 1.5h timeout)
        trade._exit_check_active = True
        trade._funding_paid_long = True
        trade._funding_paid_short = True
        trade._funding_paid_at = datetime.now(timezone.utc) - timedelta(hours=2)
        trade.funding_collections = 1

        await controller._check_exit(trade)

        # Should have been closed (timeout + no qualifying next funding)
        assert trade.trade_id not in controller._active_trades

    @pytest.mark.asyncio
    async def test_exits_immediately_when_basis_favorable(
        self, controller, config, mock_exchange_mgr, mock_redis
    ):
        """PnL >= profit_target → exits immediately."""
        config.trading_params.exit_offset_seconds = 0
        config.trading_params.profit_target_pct = Decimal("0.7")

        past_ms = (time.time() - 1200) * 1000
        for eid in ("exchange_a", "exchange_b"):
            adapter = mock_exchange_mgr.get(eid)
            rate = Decimal("-0.0001") if eid == "exchange_a" else Decimal("0.0001")
            data = {"rate": rate, "next_timestamp": past_ms, "interval_hours": 8}
            adapter._funding_rate_cache["BTC/USDT"] = data

        # Long price rose enough for 1.1% PnL → adj 0.8% (above 0.7% target)
        mock_exchange_mgr.get("exchange_a").get_ticker.return_value = {"last": 50550.0}
        mock_exchange_mgr.get("exchange_b").get_ticker.return_value = {"last": 50000.0}

        trade = _make_trade(controller, spread_pct="1.0")
        trade.entry_price_long = Decimal("50000")
        trade.entry_price_short = Decimal("50000")

        await controller._check_exit(trade)

        # Should have been closed immediately (profit target hit)
        assert trade.trade_id not in controller._active_trades


class TestCherryPickCostExit:
    """Cherry-pick should exit when now >= exit_before."""

    @pytest.mark.asyncio
    async def test_exits_when_cost_within_max_wait(
        self, controller, config, mock_exchange_mgr, mock_redis
    ):
        config.trading_params.exit_offset_seconds = 0

        now = datetime.now(timezone.utc)

        # Fund rates
        past_ms = (time.time() - 1200) * 1000
        for eid in ("exchange_a", "exchange_b"):
            adapter = mock_exchange_mgr.get(eid)
            rate = Decimal("-0.0030") if eid == "exchange_a" else Decimal("0.0030")
            data = {"rate": rate, "next_timestamp": past_ms, "interval_hours": 8}
            adapter._funding_rate_cache["BTC/USDT"] = data

        trade = _make_trade(controller, spread_pct="1.0")
        trade.mode = TradeMode.CHERRY_PICK
        # exit_before is in the past → should trigger hard exit
        trade.exit_before = now - timedelta(minutes=1)

        await controller._check_exit(trade)

        assert trade.trade_id not in controller._active_trades


class TestNonQuickCyclePath:
    """Tests for timeout-based exit and next-cycle hold logic."""

    @pytest.mark.asyncio
    async def test_exits_when_net_below_threshold(
        self, controller, config, mock_exchange_mgr, mock_redis
    ):
        """Timeout elapsed + next funding doesn't qualify → EXIT."""
        config.trading_params.exit_offset_seconds = 0

        # Low funding rates (won't qualify for next cycle)
        past_ms = (time.time() - 1200) * 1000
        for eid in ("exchange_a", "exchange_b"):
            adapter = mock_exchange_mgr.get(eid)
            rate = Decimal("-0.0001") if eid == "exchange_a" else Decimal("0.0001")
            data = {"rate": rate, "next_timestamp": past_ms, "interval_hours": 8}
            adapter._funding_rate_cache["BTC/USDT"] = data

        mock_exchange_mgr.get("exchange_a").get_ticker.return_value = {"last": 50000.0}
        mock_exchange_mgr.get("exchange_b").get_ticker.return_value = {"last": 50000.0}

        trade = _make_trade(controller, spread_pct="1.0")
        trade.entry_price_long = Decimal("50000")
        trade.entry_price_short = Decimal("50000")
        # Pre-set: funding collected 2h ago (past 1.5h timeout)
        trade._exit_check_active = True
        trade._funding_paid_long = True
        trade._funding_paid_short = True
        trade._funding_paid_at = datetime.now(timezone.utc) - timedelta(hours=2)
        trade.funding_collections = 1

        await controller._check_exit(trade)

        assert trade.trade_id not in controller._active_trades

    @pytest.mark.asyncio
    async def test_holds_when_net_above_threshold(
        self, controller, config, mock_exchange_mgr
    ):
        """Non-quick-cycle: basis adverse, within recovery timeout → hold."""
        config.trading_params.exit_offset_seconds = 0
        config.trading_params.basis_recovery_timeout_minutes = Decimal("30")

        past_ms = (time.time() - 1200) * 1000
        future_4h = (time.time() + 4 * 3600) * 1000
        for eid in ("exchange_a", "exchange_b"):
            adapter = mock_exchange_mgr.get(eid)
            rate = Decimal("-0.0030") if eid == "exchange_a" else Decimal("0.0030")
            data = {"rate": rate, "next_timestamp": future_4h, "interval_hours": 8}
            adapter._funding_rate_cache["BTC/USDT"] = data

        trade = _make_trade(controller, spread_pct="1.0")
        trade.entry_price_long = Decimal("50000")
        trade.entry_price_short = Decimal("50000")
        trade.entry_basis_pct = Decimal("0")
        # Adverse: long dropped → basis below entry (price loss)
        mock_exchange_mgr.get("exchange_a").get_ticker.return_value = {"last": 49700.0}
        mock_exchange_mgr.get("exchange_b").get_ticker.return_value = {"last": 50000.0}

        await controller._check_exit(trade)

        assert trade.trade_id in controller._active_trades
        assert trade.state == TradeState.OPEN


# ── Tests for TradeRecord serialization ──────────────────────────

class TestTradeRecordPersistence:
    """Round-trip test for to_persist_dict / from_persist_dict."""

    def test_round_trip(self):
        now = datetime.now(timezone.utc)
        original = TradeRecord(
            trade_id="rt-001",
            symbol="ETH/USDT",
            state=TradeState.OPEN,
            mode=TradeMode.CHERRY_PICK,
            long_exchange="binance",
            short_exchange="bybit",
            long_qty=Decimal("1.5"),
            short_qty=Decimal("1.5"),
            entry_edge_pct=Decimal("0.85"),
            entry_basis_pct=Decimal("0.03"),
            long_funding_rate=Decimal("-0.0002"),
            short_funding_rate=Decimal("0.0004"),
            long_taker_fee=Decimal("0.0005"),
            short_taker_fee=Decimal("0.0006"),
            entry_price_long=Decimal("3200.50"),
            entry_price_short=Decimal("3201.10"),
            fees_paid_total=Decimal("2.88"),
            opened_at=now,
            funding_collections=3,
            funding_collected_usd=Decimal("1.23"),
        )

        d = original.to_persist_dict()
        restored = TradeRecord.from_persist_dict("rt-001", d)

        assert restored.symbol == original.symbol
        assert restored.state == original.state
        assert restored.mode == original.mode
        assert restored.long_qty == original.long_qty
        assert restored.entry_basis_pct == original.entry_basis_pct
        assert restored.long_funding_rate == original.long_funding_rate
        assert restored.funding_collections == original.funding_collections
        assert restored.funding_collected_usd == original.funding_collected_usd
        assert restored.opened_at.isoformat() == original.opened_at.isoformat()

    def test_legacy_entry_edge_bps(self):
        """from_persist_dict handles old 'entry_edge_bps' key."""
        data = {
            "symbol": "BTC/USDT",
            "state": "open",
            "long_exchange": "a",
            "short_exchange": "b",
            "long_qty": "0.01",
            "short_qty": "0.01",
            "entry_edge_bps": "150",  # legacy key
        }
        trade = TradeRecord.from_persist_dict("legacy-1", data)
        assert trade.entry_edge_pct == Decimal("150")

    def test_none_optionals(self):
        """Fields that are None should round-trip correctly."""
        original = TradeRecord(
            trade_id="n-001",
            symbol="SOL/USDT",
            state=TradeState.OPEN,
            long_exchange="a",
            short_exchange="b",
            long_qty=Decimal("10"),
            short_qty=Decimal("10"),
            entry_edge_pct=Decimal("0.5"),
        )
        d = original.to_persist_dict()
        restored = TradeRecord.from_persist_dict("n-001", d)

        assert restored.entry_basis_pct is None
        assert restored.long_funding_rate is None
        assert restored.opened_at is None
        assert restored.funding_collected_usd == Decimal("0")
