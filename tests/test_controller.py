"""Tests for execution controller — the critical safety path."""

import json
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from src.core.contracts import OrderSide, TradeRecord, TradeState
from src.execution.controller import ExecutionController


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
        mode="hold",
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
        """If spread >= hold_min_spread after payment, should HOLD."""
        config.trading_params.quick_cycle = True
        config.trading_params.hold_min_spread = Decimal("0.5")
        config.trading_params.exit_offset_seconds = 0  # instant for testing

        # Set exchange funding rates to produce a high spread (0.6%)
        # Must populate _funding_rate_cache since _check_exit uses get_funding_rate_cached()
        for eid in ("exchange_a", "exchange_b"):
            adapter = mock_exchange_mgr.get(eid)
            past_ms = (time.time() - 1200) * 1000  # 20 min ago
            if eid == "exchange_a":
                data = {
                    "rate": Decimal("-0.0030"),  # negative = we receive
                    "next_timestamp": past_ms,
                    "interval_hours": 8,
                }
            else:
                data = {
                    "rate": Decimal("0.0030"),  # positive = we receive on short
                    "next_timestamp": past_ms,
                    "interval_hours": 8,
                }
            adapter.get_funding_rate.return_value = data
            adapter._funding_rate_cache["BTC/USDT"] = data

        trade = _make_trade(controller, spread_pct="1.0")

        await controller._check_exit(trade)

        # Trade should still be open (held)
        assert trade.trade_id in controller._active_trades
        assert trade.state == TradeState.OPEN

    @pytest.mark.asyncio
    async def test_exits_when_next_funding_too_far(
        self, controller, config, mock_exchange_mgr, mock_redis
    ):
        """Even if spread >= threshold, EXIT if next funding is beyond hold_max_wait."""
        config.trading_params.quick_cycle = True
        config.trading_params.hold_min_spread = Decimal("0.5")
        config.trading_params.hold_max_wait_seconds = 3600  # 1 hour
        config.trading_params.exit_offset_seconds = 0

        # Good spread (0.6%) BUT next_timestamp is 4 hours away
        future_4h_ms = (time.time() + 4 * 3600) * 1000
        for eid in ("exchange_a", "exchange_b"):
            adapter = mock_exchange_mgr.get(eid)
            if eid == "exchange_a":
                data = {
                    "rate": Decimal("-0.0030"),
                    "next_timestamp": future_4h_ms,  # 4h away
                    "interval_hours": 8,
                }
            else:
                data = {
                    "rate": Decimal("0.0030"),
                    "next_timestamp": future_4h_ms,
                    "interval_hours": 8,
                }
            adapter.get_funding_rate.return_value = data
            adapter._funding_rate_cache["BTC/USDT"] = data

        trade = _make_trade(controller, spread_pct="1.0")
        # Pre-set funding_paid flags so tracker doesn't get overwritten to future
        trade._funding_paid_long = True
        trade._funding_paid_short = True

        await controller._check_exit(trade)

        # Should have exited despite good spread — next funding too far
        assert trade.trade_id not in controller._active_trades

    @pytest.mark.asyncio
    async def test_holds_when_next_funding_within_max_wait(
        self, controller, config, mock_exchange_mgr
    ):
        """Spread >= threshold AND next funding within hold_max_wait → HOLD."""
        config.trading_params.quick_cycle = True
        config.trading_params.hold_min_spread = Decimal("0.5")
        config.trading_params.hold_max_wait_seconds = 3600  # 1 hour
        config.trading_params.exit_offset_seconds = 0

        # Good spread AND next funding in 30 min (within 1h limit)
        future_30m_ms = (time.time() + 1800) * 1000
        for eid in ("exchange_a", "exchange_b"):
            adapter = mock_exchange_mgr.get(eid)
            if eid == "exchange_a":
                data = {
                    "rate": Decimal("-0.0030"),
                    "next_timestamp": future_30m_ms,  # 30 min away
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
        trade._funding_paid_long = True
        trade._funding_paid_short = True

        await controller._check_exit(trade)

        # Should still be holding — next funding is close enough
        assert trade.trade_id in controller._active_trades
        assert trade.state == TradeState.OPEN

    @pytest.mark.asyncio
    async def test_exits_when_spread_below_threshold(
        self, controller, config, mock_exchange_mgr, mock_redis
    ):
        """If spread < hold_min_spread after payment, should EXIT."""
        config.trading_params.quick_cycle = True
        config.trading_params.hold_min_spread = Decimal("0.5")
        config.trading_params.exit_offset_seconds = 0

        # Set exchange funding rates to produce a LOW spread (< 0.5%)
        # Must populate _funding_rate_cache since _check_exit uses get_funding_rate_cached()
        for eid in ("exchange_a", "exchange_b"):
            adapter = mock_exchange_mgr.get(eid)
            past_ms = (time.time() - 1200) * 1000
            if eid == "exchange_a":
                data = {
                    "rate": Decimal("-0.0001"),  # tiny
                    "next_timestamp": past_ms,
                    "interval_hours": 8,
                }
            else:
                data = {
                    "rate": Decimal("0.0001"),  # tiny
                    "next_timestamp": past_ms,
                    "interval_hours": 8,
                }
            adapter.get_funding_rate.return_value = data
            adapter._funding_rate_cache["BTC/USDT"] = data

        trade = _make_trade(controller, spread_pct="1.0")

        await controller._check_exit(trade)

        # Trade should have been closed
        assert trade.trade_id not in controller._active_trades


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

        # Redis opp is only slightly better — not enough for upgrade
        weak_opp = {
            "symbol": "DOGE/USDT",
            "long_exchange": "exchange_a",
            "short_exchange": "exchange_b",
            "immediate_spread_pct": 0.4,  # ~0.16% + 0.4% threshold not met
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
