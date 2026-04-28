"""
Shared fixtures for all tests.
"""

import asyncio
import time
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.config import (
    Config, ExchangeConfig, RiskLimits, TradingParams,
    ExecutionConfig, RiskGuardConfig, RedisConfig, LoggingConfig,
)
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
            slippage_buffer_pct=Decimal("0.015"),
            safety_buffer_pct=Decimal("0.02"),
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
    adapter.get_cached_instrument_spec = MagicMock(return_value=btc_spec)
    _bal_a = {
        "total": Decimal("1000"), "free": Decimal("800"), "used": Decimal("200"),
    }
    adapter.get_balance.return_value = _bal_a
    adapter.get_balance_cached.return_value = _bal_a
    adapter.get_ticker.return_value = {"last": 50000.0, "bid": 49999, "ask": 50001}
    adapter.get_funding_rate.return_value = {
        "rate": Decimal("0.0001"), "timestamp": None, "datetime": None,
        "next_timestamp": _future_ms(300), "interval_hours": 8,  # 5 min in future (within 15 min window)
    }
    adapter.get_positions.return_value = []
    adapter.place_order.return_value = {
        "id": "order-123", "filled": 0.01, "average": 50000.0, "status": "closed",
    }
    adapter.ensure_trading_settings = AsyncMock(return_value=None)
    adapter.get_vwap_and_depth = AsyncMock(return_value=(Decimal("50000"), True))
    adapter.fetch_fill_details_from_trades = AsyncMock(
        return_value={"total_fee": Decimal("0"), "avg_price": Decimal("50000"), "filled_qty": Decimal("0.01")}
    )
    adapter.get_executable_price = AsyncMock(return_value=Decimal("50000"))
    adapter.update_taker_fee_from_fill = MagicMock()  # called sync in controller
    adapter.get_mark_price = MagicMock(return_value=None)  # sync — must not be AsyncMock
    adapter.get_best_ask = MagicMock(return_value=50001.0)
    adapter.get_best_bid = MagicMock(return_value=49999.0)
    adapter.get_best_ask_age_ms = MagicMock(return_value=0.0)
    adapter.get_best_bid_age_ms = MagicMock(return_value=0.0)
    # Mock public adapter properties used by scanner and main
    adapter.symbols = ["BTC/USDT", "ETH/USDT"]
    adapter.markets = {"BTC/USDT": {}, "ETH/USDT": {}}
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
    adapter_b.get_cached_instrument_spec = mock_adapter.get_cached_instrument_spec
    _bal_b = {
        "total": Decimal("1000"), "free": Decimal("800"), "used": Decimal("200"),
    }
    adapter_b.get_balance.return_value = _bal_b
    adapter_b.get_balance_cached.return_value = _bal_b
    adapter_b.get_ticker.return_value = {"last": 50000.0}
    adapter_b.get_funding_rate.return_value = {
        "rate": Decimal("0.0003"), "timestamp": None, "datetime": None,
        "next_timestamp": _future_ms(300), "interval_hours": 8,
    }
    adapter_b.get_positions.return_value = []
    adapter_b.place_order.return_value = {
        "id": "order-456", "filled": 0.01, "average": 50000.0, "status": "closed",
    }
    adapter_b.ensure_trading_settings = AsyncMock(return_value=None)
    adapter_b.get_vwap_and_depth = AsyncMock(return_value=(Decimal("50000"), True))
    adapter_b.fetch_fill_details_from_trades = AsyncMock(
        return_value={"total_fee": Decimal("0"), "avg_price": Decimal("50000"), "filled_qty": Decimal("0.01")}
    )
    adapter_b.get_executable_price = AsyncMock(return_value=Decimal("50000"))
    adapter_b.update_taker_fee_from_fill = MagicMock()  # called sync in controller
    adapter_b.get_mark_price = MagicMock(return_value=None)  # sync — must not be AsyncMock
    adapter_b.get_best_ask = MagicMock(return_value=50001.0)
    adapter_b.get_best_bid = MagicMock(return_value=49999.0)
    adapter_b.get_best_ask_age_ms = MagicMock(return_value=0.0)
    adapter_b.get_best_bid_age_ms = MagicMock(return_value=0.0)
    # Mock public adapter properties used by scanner and main
    adapter_b.symbols = ["BTC/USDT", "ETH/USDT"]
    adapter_b.markets = {"BTC/USDT": {}, "ETH/USDT": {}}
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
    r.is_route_cooled_down.return_value = False      # P1-2: per-route cooldown check
    r.acquire_lock.return_value = True
    r.acquire_lock_with_token.return_value = True    # P0-1: token-owned lock
    r.release_lock_if_owner.return_value = True      # P0-1: safe release
    r.extend_lock.return_value = True                # P0-1: heartbeat renewal
    r.get_all_trades.return_value = {}
    r.health_check.return_value = True
    r.set_trade_state = AsyncMock(return_value=True)
    r.set_cooldown = AsyncMock(return_value=True)
    r.set_route_cooldown = AsyncMock(return_value=True)  # P1-2
    r.get_cooled_down_symbols.return_value = set()  # sync set — prevents AsyncMock default
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
        immediate_spread_pct=Decimal("0.9"),
        immediate_net_pct=Decimal("0.7"),
        gross_edge_pct=Decimal("1.2"),
        fees_pct=Decimal("0.2"),
        net_edge_pct=Decimal("0.7"),
        suggested_qty=Decimal("0.01"),
        reference_price=Decimal("50000"),
        next_funding_ms=_future_ms(300),  # 5 min from now — within entry window
    )
