"""
Unit tests for src.execution.sizer — PositionSizer.compute.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.contracts import InstrumentSpec, OpportunityCandidate
from src.execution.sizer import PositionSizer


# ── Helpers ──────────────────────────────────────────────────────

def _make_spec(lot_size: str = "0.001", contract_size: str = "1") -> InstrumentSpec:
    return InstrumentSpec(
        exchange="exchange_a",
        symbol="BTC/USDT",
        base="BTC",
        quote="USDT",
        contract_size=Decimal(contract_size),
        tick_size=Decimal("0.01"),
        lot_size=Decimal(lot_size),
        min_notional=Decimal("5"),
        maker_fee=Decimal("0.0002"),
        taker_fee=Decimal("0.0005"),
    )


def _make_opp(reference_price: str = "50000") -> OpportunityCandidate:
    return OpportunityCandidate(
        symbol="BTC/USDT",
        long_exchange="exchange_a",
        short_exchange="exchange_b",
        long_funding_rate=Decimal("0.0001"),
        short_funding_rate=Decimal("0.0005"),
        funding_spread_pct=Decimal("0.06"),
        immediate_spread_pct=Decimal("0.9"),
        immediate_net_pct=Decimal("0.7"),
        gross_edge_pct=Decimal("1.2"),
        fees_pct=Decimal("0.2"),
        net_edge_pct=Decimal("0.7"),
        suggested_qty=Decimal("0.01"),
        reference_price=Decimal(reference_price),
    )


def _mock_adapter(
    free: float = 1000.0,
    spec: InstrumentSpec | None = None,
    ticker_last: float = 50000.0,
) -> AsyncMock:
    adapter = AsyncMock()
    adapter.get_balance.return_value = {
        "total": Decimal(str(free * 1.2)),
        "free": Decimal(str(free)),
        "used": Decimal(str(free * 0.2)),
    }
    adapter.get_instrument_spec.return_value = spec or _make_spec()
    # P1-3: sizer now fetches a live ticker price in the same gather as balances.
    # Return a realistic ticker dict so Decimal conversion in sizer.compute succeeds.
    adapter.get_ticker.return_value = {"last": ticker_last}
    return adapter


def _make_config(
    position_size_pct: float = 0.7,
    max_position_size_usd: float = 50000.0,
    leverage: int = 5,
) -> MagicMock:
    cfg = MagicMock()
    cfg.risk_limits.position_size_pct = Decimal(str(position_size_pct))
    cfg.risk_limits.max_position_size_usd = Decimal(str(max_position_size_usd))
    cfg.risk_limits.max_margin_usage = Decimal("0.70")

    def _exc_cfg(eid: str) -> MagicMock:
        exc = MagicMock()
        exc.leverage = leverage
        return exc

    cfg.exchanges.get.side_effect = _exc_cfg
    return cfg


# ── Tests ────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestPositionSizer:
    async def test_returns_tuple_of_four(self):
        sizer = PositionSizer(_make_config())
        opp = _make_opp()
        long_a = _mock_adapter(1000.0)
        short_a = _mock_adapter(1000.0)
        result = await sizer.compute(opp, long_a, short_a)
        assert result is not None
        qty, notional, long_spec, short_spec = result
        assert isinstance(qty, Decimal)
        assert isinstance(notional, Decimal)
        assert isinstance(long_spec, InstrumentSpec)
        assert isinstance(short_spec, InstrumentSpec)

    async def test_notional_uses_min_balance(self):
        # min(500, 1000) × 0.7 × 5 = 1750
        sizer = PositionSizer(_make_config(position_size_pct=0.7, leverage=5))
        long_a = _mock_adapter(free=500.0)
        short_a = _mock_adapter(free=1000.0)
        result = await sizer.compute(_make_opp("50000"), long_a, short_a)
        assert result is not None
        _, notional, _, _ = result
        assert notional == Decimal("1750")

    async def test_qty_rounded_to_lot(self):
        # notional = 1000 × 0.7 × 1 = 700; price = 50000 → 0.014 BTC; lot = 0.01
        sizer = PositionSizer(_make_config(position_size_pct=0.7, leverage=1))
        long_a = _mock_adapter(free=1000.0, spec=_make_spec(lot_size="0.01"))
        short_a = _mock_adapter(free=1000.0, spec=_make_spec(lot_size="0.01"))
        result = await sizer.compute(_make_opp("50000"), long_a, short_a)
        assert result is not None
        qty, _, _, _ = result
        # 700 / 50000 = 0.014 → rounded down to 0.01 (1 lot step)
        assert qty == Decimal("0.01")

    async def test_uses_coarsest_lot_across_exchanges(self):
        # long lot = 0.001, short lot = 0.01 → coarser is 0.01
        sizer = PositionSizer(_make_config(position_size_pct=0.7, leverage=1))
        long_a = _mock_adapter(free=1000.0, spec=_make_spec(lot_size="0.001"))
        short_a = _mock_adapter(free=1000.0, spec=_make_spec(lot_size="0.01"))
        result = await sizer.compute(_make_opp("50000"), long_a, short_a)
        assert result is not None
        qty, _, _, _ = result
        # Must be a multiple of 0.01
        remainder = qty % Decimal("0.01")
        assert remainder == Decimal("0")

    async def test_returns_none_when_balance_is_zero(self):
        sizer = PositionSizer(_make_config())
        long_a = _mock_adapter(free=0.0)
        short_a = _mock_adapter(free=0.0)
        result = await sizer.compute(_make_opp(), long_a, short_a)
        assert result is None

    async def test_uses_minimum_leverage_on_mismatch(self):
        # long=5x, short=10x  → should use 5x
        cfg = _make_config(position_size_pct=1.0, leverage=5)
        # Override short to return leverage=10
        def _exc_cfg(eid: str) -> MagicMock:
            exc = MagicMock()
            exc.leverage = 5 if eid == "exchange_a" else 10
            return exc
        cfg.exchanges.get.side_effect = _exc_cfg

        sizer = PositionSizer(cfg)
        long_a = _mock_adapter(free=100.0)
        short_a = _mock_adapter(free=100.0)
        result = await sizer.compute(_make_opp("50000"), long_a, short_a)
        assert result is not None
        _, notional, _, _ = result
        # 100 × 1.0 × 5 = 500 (using min leverage = 5)
        assert notional == Decimal("500")

    async def test_qty_minimum_is_one_lot(self):
        # Even if calculated qty is below one lot, result should be at least 1 lot
        # Balance must be above $8 minimum guard
        sizer = PositionSizer(_make_config(position_size_pct=0.0001, leverage=1))
        long_a = _mock_adapter(free=10.0, spec=_make_spec(lot_size="0.001"))
        short_a = _mock_adapter(free=10.0, spec=_make_spec(lot_size="0.001"))
        result = await sizer.compute(_make_opp("50000"), long_a, short_a)
        assert result is not None
        qty, _, _, _ = result
        assert qty >= Decimal("0.001")
