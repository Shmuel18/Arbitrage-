"""Tests for risk guard — delta checks and panic close."""

from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from src.core.contracts import OrderSide, Position
from src.risk.guard import RiskGuard


@pytest.fixture
def guard(config, mock_exchange_mgr, mock_redis):
    return RiskGuard(config, mock_exchange_mgr, mock_redis)


class TestDeltaCheck:
    @pytest.mark.asyncio
    async def test_no_breach_when_balanced(self, guard, mock_exchange_mgr):
        """Balanced long/short = no panic."""
        adapter_a = mock_exchange_mgr.get("exchange_a")
        adapter_b = mock_exchange_mgr.get("exchange_b")

        adapter_a.get_positions.return_value = [
            Position(exchange="a", symbol="BTC/USDT", side=OrderSide.BUY,
                     quantity=Decimal("0.01"), entry_price=Decimal("50000")),
        ]
        adapter_b.get_positions.return_value = [
            Position(exchange="b", symbol="BTC/USDT", side=OrderSide.SELL,
                     quantity=Decimal("0.01"), entry_price=Decimal("50000")),
        ]

        # Should NOT panic-close
        await guard._check_delta()
        adapter_a.place_order.assert_not_called()
        adapter_b.place_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_breach_triggers_panic(self, guard, config, mock_exchange_mgr, mock_redis):
        """Big one-sided position should trigger panic close."""
        config.risk_limits.delta_threshold_pct = Decimal("1.0")

        adapter_a = mock_exchange_mgr.get("exchange_a")
        adapter_a.get_positions.return_value = [
            Position(exchange="a", symbol="BTC/USDT", side=OrderSide.BUY,
                     quantity=Decimal("10"), entry_price=Decimal("50000")),
        ]

        adapter_b = mock_exchange_mgr.get("exchange_b")
        adapter_b.get_positions.return_value = []

        await guard._check_delta()

        # Should have tried to close + set cooldown
        mock_redis.set_cooldown.assert_called()


class TestPanicClose:
    @pytest.mark.asyncio
    async def test_panic_close_uses_reduce_only(self, guard, mock_exchange_mgr, mock_redis):
        adapter = mock_exchange_mgr.get("exchange_a")
        adapter.get_positions.return_value = [
            Position(exchange="a", symbol="BTC/USDT", side=OrderSide.BUY,
                     quantity=Decimal("0.05"), entry_price=Decimal("50000")),
        ]

        await guard._panic_close("BTC/USDT")

        # Verify the order placed has reduce_only=True
        call_args = adapter.place_order.call_args
        req = call_args[0][0]
        assert req.reduce_only is True
        assert req.side == OrderSide.SELL  # opposite of BUY position
