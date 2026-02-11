"""Tests for contracts — data integrity."""

from decimal import Decimal

from src.core.contracts import (
    InstrumentSpec,
    OpportunityCandidate,
    OrderRequest,
    OrderSide,
    Position,
    TradeRecord,
    TradeState,
)


class TestOrderSide:
    def test_values(self):
        assert OrderSide.BUY.value == "buy"
        assert OrderSide.SELL.value == "sell"


class TestTradeState:
    def test_all_states(self):
        assert TradeState.OPEN.value == "open"
        assert TradeState.CLOSING.value == "closing"
        assert TradeState.CLOSED.value == "closed"
        assert TradeState.ERROR.value == "error"


class TestInstrumentSpec:
    def test_frozen(self, btc_spec):
        """InstrumentSpec should be immutable."""
        try:
            btc_spec.symbol = "ETH/USDT"
            assert False, "Should have raised"
        except AttributeError:
            pass


class TestOrderRequest:
    def test_defaults(self):
        req = OrderRequest(
            exchange="binance", symbol="BTC/USDT",
            side=OrderSide.BUY, quantity=Decimal("0.01"),
        )
        assert req.reduce_only is False

    def test_reduce_only(self):
        req = OrderRequest(
            exchange="binance", symbol="BTC/USDT",
            side=OrderSide.SELL, quantity=Decimal("0.01"),
            reduce_only=True,
        )
        assert req.reduce_only is True


class TestTradeRecord:
    def test_mutable(self):
        trade = TradeRecord(
            trade_id="t1", symbol="BTC/USDT",
            state=TradeState.OPEN,
            long_exchange="a", short_exchange="b",
            long_qty=Decimal("0.01"), short_qty=Decimal("0.01"),
            entry_edge_bps=Decimal("10"),
        )
        trade.state = TradeState.CLOSING
        assert trade.state == TradeState.CLOSING
