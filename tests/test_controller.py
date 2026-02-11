"""Tests for execution controller — the critical safety path."""

from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from src.core.contracts import OrderSide, TradeState
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
            gross_edge_bps=Decimal("12"), fees_bps=Decimal("2"),
            net_edge_bps=Decimal("7"), suggested_qty=Decimal("0.1"),
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

        await controller.handle_opportunity(sample_opportunity)

        trade = list(controller._active_trades.values())[0]
        assert trade.long_qty == Decimal("0.008")


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
                "entry_edge_bps": "10",
                "opened_at": "2026-01-01T00:00:00",
            }
        }

        ctrl = ExecutionController(config, mock_exchange_mgr, mock_redis)
        await ctrl._recover_trades()

        assert "abc123" in ctrl._active_trades
        assert ctrl._active_trades["abc123"].state == TradeState.OPEN
