"""
Shared fixtures for all tests.
"""

import asyncio
import time
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.config import Config, ExchangeConfig, RiskLimits, TradingParams, ExecutionConfig, RiskGuardConfig, RedisConfig, LoggingConfig
from src.core.contracts import InstrumentSpec, OpportunityCandidate, OrderSide, Position


# ── Event loop ───────────────────────────────────────────────────

@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# ── Config ───────────────────────────────────────────────────────

@pytest.fixture
def config():
    """Minimal config for unit tests — no YAML, no .env."""
    return Config(
        environment="test",
        version="3.0.0-test",
        paper_trading=True,
        dry_run=True,
        enabled_exchanges=["exchange_a", "exchange_b"],
        exchanges={
            "exchange_a": ExchangeConfig(
                name="Exchange A", ccxt_id="binanceusdm",
                default_type="future", rate_limit_ms=50, max_leverage=20,
            ),
            "exchange_b": ExchangeConfig(
                name="Exchange B", ccxt_id="bybit",
                default_type="swap", rate_limit_ms=50, max_leverage=25,
            ),
        },
        watchlist=["BTC/USDT", "ETH/USDT"],
        risk_limits=RiskLimits(
            max_margin_usage=Decimal("0.30"),
            max_position_size_usd=Decimal("1000"),
            delta_threshold_pct=Decimal("5.0"),
        ),
        trading_params=TradingParams(
            min_funding_spread=Decimal("0.05"),
            min_net_pct=Decimal("0.01"),
            max_slippage_pct=Decimal("0.10"),
            slippage_buffer_pct=Decimal("0.015"),
            safety_buffer_pct=Decimal("0.02"),
            basis_buffer_pct=Decimal("0.01"),
        ),
        execution=ExecutionConfig(concurrent_opportunities=3, order_timeout_ms=5000),
        risk_guard=RiskGuardConfig(fast_loop_interval_sec=5, deep_loop_interval_sec=60),
        redis=RedisConfig(),
        logging=LoggingConfig(level="DEBUG"),
    )


# ── Instrument spec ──────────────────────────────────────────────

@pytest.fixture
def btc_spec():
    return InstrumentSpec(
        exchange="exchange_a", symbol="BTC/USDT",
        base="BTC", quote="USDT",
        contract_size=Decimal("1"), tick_size=Decimal("0.01"),
        lot_size=Decimal("0.001"), min_notional=Decimal("5"),
        maker_fee=Decimal("0.0002"), taker_fee=Decimal("0.0005"),
    )


# ── Mock exchange adapter ───────────────────────────────────────

# Helper: timestamp N seconds from now (in ms)
def _future_ms(seconds: float = 30) -> float:
    return time.time() * 1000 + seconds * 1000


@pytest.fixture
def mock_adapter(btc_spec):
    adapter = AsyncMock()
    adapter.exchange_id = "exchange_a"
    adapter.get_instrument_spec.return_value = btc_spec
    adapter.get_balance.return_value = {
        "total": Decimal("1000"), "free": Decimal("800"), "used": Decimal("200"),
    }
    adapter.get_ticker.return_value = {"last": 50000.0, "bid": 49999, "ask": 50001}
    adapter.get_funding_rate.return_value = {
        "rate": Decimal("0.0001"), "timestamp": None, "datetime": None,
        "next_timestamp": _future_ms(300), "interval_hours": 8,  # 5 min in future (within 15 min window)
    }
    adapter.get_positions.return_value = []
    adapter.place_order.return_value = {
        "id": "order-123", "filled": 0.01, "average": 50000.0, "status": "closed",
    }
    # Mock exchange markets for scanner symbol intersection
    adapter._exchange = MagicMock()
    adapter._exchange.markets = {"BTC/USDT": {}, "ETH/USDT": {}}
    # Add funding rate cache for WebSocket-based scanner
    adapter._funding_rate_cache = {}
    adapter.get_funding_rate_cached = lambda sym: adapter._funding_rate_cache.get(sym)
    return adapter


# ── Mock exchange manager ────────────────────────────────────────

@pytest.fixture
def mock_exchange_mgr(mock_adapter):
    mgr = MagicMock()
    adapter_b = AsyncMock()
    adapter_b.exchange_id = "exchange_b"
    adapter_b.get_instrument_spec = mock_adapter.get_instrument_spec
    adapter_b.get_balance.return_value = {
        "total": Decimal("1000"), "free": Decimal("800"), "used": Decimal("200"),
    }
    adapter_b.get_ticker.return_value = {"last": 50000.0}
    adapter_b.get_funding_rate.return_value = {
        "rate": Decimal("0.0003"), "timestamp": None, "datetime": None,
        "next_timestamp": _future_ms(300), "interval_hours": 8,
    }
    adapter_b.get_positions.return_value = []
    adapter_b.place_order.return_value = {
        "id": "order-456", "filled": 0.01, "average": 50000.0, "status": "closed",
    }
    # Mock exchange markets for scanner symbol intersection
    adapter_b._exchange = MagicMock()
    adapter_b._exchange.markets = {"BTC/USDT": {}, "ETH/USDT": {}}
    # Add funding rate cache for WebSocket-based scanner
    adapter_b._funding_rate_cache = {}
    adapter_b.get_funding_rate_cached = lambda sym: adapter_b._funding_rate_cache.get(sym)

    mgr.get.side_effect = lambda eid: mock_adapter if eid == "exchange_a" else adapter_b
    mgr.all.return_value = {"exchange_a": mock_adapter, "exchange_b": adapter_b}
    return mgr


# ── Mock Redis ───────────────────────────────────────────────────

@pytest.fixture
def mock_redis():
    r = AsyncMock()
    r.is_cooled_down.return_value = False
    r.acquire_lock.return_value = True
    r.get_all_trades.return_value = {}
    r.health_check.return_value = True
    r.set_trade_state = AsyncMock(return_value=True)
    r.set_cooldown = AsyncMock(return_value=True)
    return r


# ── Sample opportunity ──────────────────────────────────────────

@pytest.fixture
def sample_opportunity():
    return OpportunityCandidate(
        symbol="BTC/USDT",
        long_exchange="exchange_a",
        short_exchange="exchange_b",
        long_funding_rate=Decimal("0.0001"),
        short_funding_rate=Decimal("0.0005"),
        funding_spread_pct=Decimal("0.06"),
        gross_edge_pct=Decimal("1.2"),
        fees_pct=Decimal("0.2"),
        net_edge_pct=Decimal("0.7"),
        suggested_qty=Decimal("0.01"),
        reference_price=Decimal("50000"),
    )
