"""
Tests for ``src.core.status_publisher.StatusPublisher``.
"""
import asyncio
import json
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

from src.core.status_publisher import StatusPublisher
from src.core.contracts import TradeRecord, TradeState, TradeMode


@pytest.fixture
def shutdown_event():
    return asyncio.Event()


@pytest.fixture
def status_pub(config, mock_exchange_mgr, mock_redis, shutdown_event):
    """Build a StatusPublisher wired with mocks."""
    publisher = AsyncMock()
    publisher.publish_status = AsyncMock()
    publisher.publish_balances = AsyncMock()
    publisher.publish_summary = AsyncMock()
    publisher.publish_positions = AsyncMock()

    controller = MagicMock()
    controller._active_trades = {}

    return StatusPublisher(
        cfg=config,
        exchange_mgr=mock_exchange_mgr,
        controller=controller,
        redis=mock_redis,
        publisher=publisher,
        shutdown_event=shutdown_event,
    )


@pytest.mark.asyncio
async def test_publish_cycle_calls_publisher(status_pub):
    """A single cycle should call publish_status, publish_balances, publish_summary, publish_positions."""
    await status_pub._publish_cycle()

    status_pub._publisher.publish_status.assert_awaited_once()
    status_pub._publisher.publish_balances.assert_awaited_once()
    status_pub._publisher.publish_summary.assert_awaited_once()
    status_pub._publisher.publish_positions.assert_awaited_once()


@pytest.mark.asyncio
async def test_publish_cycle_fetches_balances(status_pub, mock_exchange_mgr):
    """Balances should be collected from each enabled exchange."""
    await status_pub._publish_cycle()

    # publish_balances should receive a dict with exchange keys
    call_args = status_pub._publisher.publish_balances.call_args[0][0]
    assert isinstance(call_args, dict)
    assert "exchange_a" in call_args or "exchange_b" in call_args


@pytest.mark.asyncio
async def test_run_respects_shutdown(status_pub, shutdown_event):
    """run() should exit promptly after the shutdown event is set."""
    shutdown_event.set()
    # Should return almost immediately since the event is already set
    await asyncio.wait_for(status_pub.run(), timeout=2.0)


@pytest.mark.asyncio
async def test_publish_cycle_error_resilience(status_pub):
    """If publisher.publish_status raises, the cycle should not crash."""
    status_pub._publisher.publish_status.side_effect = RuntimeError("boom")
    # Should raise (it's caught in run(), not in _publish_cycle)
    with pytest.raises(RuntimeError):
        await status_pub._publish_cycle()


@pytest.mark.asyncio
async def test_read_realized_pnl_returns_zero_on_empty(status_pub, mock_redis):
    """When there is no closed PnL data, should return 0."""
    mock_redis.zrangebyscore = AsyncMock(return_value=[])
    result = await status_pub._read_realized_pnl()
    assert result == 0.0


@pytest.mark.asyncio
async def test_read_realized_pnl_sums_entries(status_pub, mock_redis):
    """Should sum PnL from closed trade entries."""
    entries = [
        (json.dumps({"pnl": 1.5, "trade_id": "t1"}), 1000.0),
        (json.dumps({"pnl": -0.5, "trade_id": "t2"}), 1001.0),
    ]
    mock_redis.zrangebyscore = AsyncMock(return_value=entries)
    result = await status_pub._read_realized_pnl()
    assert abs(result - 1.0) < 0.001


@pytest.mark.asyncio
async def test_read_realized_pnl_backward_compat(config, mock_exchange_mgr, shutdown_event):
    """Old entries stored as plain float strings should still be parsed."""
    publisher = AsyncMock()
    controller = MagicMock()
    controller._active_trades = {}
    redis = AsyncMock()
    redis.zrangebyscore = AsyncMock(return_value=[("2.5", 1000.0)])

    sp = StatusPublisher(
        cfg=config,
        exchange_mgr=mock_exchange_mgr,
        controller=controller,
        redis=redis,
        publisher=publisher,
        shutdown_event=shutdown_event,
    )
    result = await sp._read_realized_pnl()
    assert abs(result - 2.5) < 0.001


@pytest.mark.asyncio
async def test_read_realized_pnl_error_returns_zero(status_pub, mock_redis):
    """On Redis error, realized PnL should return 0."""
    mock_redis.zrangebyscore = AsyncMock(side_effect=RuntimeError("Redis down"))
    result = await status_pub._read_realized_pnl()
    assert result == 0.0


@pytest.mark.asyncio
async def test_fetch_balances_returns_dict(status_pub):
    """_fetch_balances should return a dict of exchange_id → float."""
    balances = await status_pub._fetch_balances()
    assert isinstance(balances, dict)
    # Should have entries for enabled exchanges
    for eid in status_pub._cfg.enabled_exchanges:
        assert eid in balances
        assert isinstance(balances[eid], float)


@pytest.mark.asyncio
async def test_fetch_balances_handles_error(status_pub, mock_exchange_mgr):
    """If get_balance raises, that exchange should get 0.0."""
    adapter_a = mock_exchange_mgr.get("exchange_a")
    adapter_a.get_balance.side_effect = RuntimeError("network error")
    balances = await status_pub._fetch_balances()
    assert balances.get("exchange_a") == 0.0


@pytest.mark.asyncio
async def test_prefetch_market_data_empty(status_pub):
    """When there are no active trades, prefetch should return empty caches."""
    ticker_cache, position_cache = await status_pub._prefetch_market_data([])
    assert ticker_cache == {}
    assert position_cache == {}


@pytest.mark.asyncio
async def test_build_positions_empty(status_pub):
    """Building positions from an empty snapshot should return an empty list."""
    result = status_pub._build_positions([], {}, {})
    assert result == []


@pytest.mark.asyncio
async def test_publish_pnl_writes_to_redis(status_pub, mock_redis):
    """_publish_pnl should write PnL snapshot and latest payload to Redis."""
    mock_redis.zrangebyscore = AsyncMock(return_value=[])
    mock_redis.zadd = AsyncMock()
    mock_redis.zremrangebyscore = AsyncMock()
    mock_redis.set = AsyncMock()
    await status_pub._publish_pnl([], {}, 1000000.0)
    mock_redis.zadd.assert_awaited_once()
    mock_redis.set.assert_awaited_once()


@pytest.mark.asyncio
async def test_enrich_funding_spread_with_cached_rates(status_pub, mock_exchange_mgr):
    """_enrich_funding_spread should populate spread fields when cached rates exist."""
    # Set up cached rates
    adapter_a = mock_exchange_mgr.get("exchange_a")
    adapter_a._funding_rate_cache = {
        "BTC/USDT": {"rate": Decimal("0.0001"), "interval_hours": 8, "next_timestamp": 999999}
    }
    adapter_a.get_funding_rate_cached = lambda sym: adapter_a._funding_rate_cache.get(sym)

    adapter_b = mock_exchange_mgr.get("exchange_b")
    adapter_b._funding_rate_cache = {
        "BTC/USDT": {"rate": Decimal("0.0005"), "interval_hours": 8}
    }
    adapter_b.get_funding_rate_cached = lambda sym: adapter_b._funding_rate_cache.get(sym)

    # Build a mock trade
    pos_entry = {
        "immediate_spread_pct": None,
        "current_spread_pct": None,
        "current_long_rate": None,
        "current_short_rate": None,
        "next_funding_ms": None,
    }
    trade = MagicMock()
    trade.long_exchange = "exchange_a"
    trade.short_exchange = "exchange_b"
    trade.symbol = "BTC/USDT"
    trade.entry_price_long = Decimal("50000")
    trade.long_qty = Decimal("0.01")

    status_pub._enrich_funding_spread(pos_entry, trade)

    assert pos_entry["current_long_rate"] is not None
    assert pos_entry["current_short_rate"] is not None
    assert pos_entry["immediate_spread_pct"] is not None
    assert pos_entry["next_funding_ms"] == 999999


@pytest.mark.asyncio
async def test_enrich_price_pnl_computes_unrealized(status_pub):
    """_enrich_price_pnl should compute unrealized PnL from ticker cache."""
    trade = MagicMock()
    trade.long_exchange = "exchange_a"
    trade.short_exchange = "exchange_b"
    trade.symbol = "BTC/USDT"
    trade.entry_price_long = Decimal("50000")
    trade.entry_price_short = Decimal("50000")
    trade.long_qty = Decimal("0.01")
    trade.short_qty = Decimal("0.01")
    trade.funding_collected_usd = Decimal("0.5")
    trade.fees_paid_total = Decimal("0.1")

    ticker_cache = {
        ("exchange_a", "BTC/USDT"): {"last": 50100},  # long gained 100
        ("exchange_b", "BTC/USDT"): {"last": 49900},  # short also gained 100
    }
    pos_entry: dict = {}
    status_pub._enrich_price_pnl(pos_entry, trade, ticker_cache)

    assert "unrealized_pnl_pct" in pos_entry
    assert "live_price_long" in pos_entry
    assert "current_basis_pct" in pos_entry


@pytest.mark.asyncio
async def test_build_one_position_basic(status_pub):
    """_build_one_position should produce a valid position dict."""
    trade = MagicMock()
    trade.trade_id = "test-1"
    trade.symbol = "BTC/USDT"
    trade.long_exchange = "exchange_a"
    trade.short_exchange = "exchange_b"
    trade.long_qty = Decimal("0.01")
    trade.short_qty = Decimal("0.01")
    trade.entry_edge_pct = Decimal("0.06")
    trade.long_funding_rate = Decimal("0.0001")
    trade.short_funding_rate = Decimal("0.0005")
    trade.mode = "hold"
    trade.opened_at = datetime(2024, 1, 1, tzinfo=timezone.utc)
    trade.state = TradeState.OPEN
    trade.entry_price_long = Decimal("50000")
    trade.entry_price_short = Decimal("50000")
    trade.entry_tier = "standard"
    trade.entry_basis_pct = Decimal("0.01")
    trade.price_spread_pct = Decimal("0.02")
    trade.funding_collected_usd = Decimal("0.5")
    trade.fees_paid_total = Decimal("0.1")
    trade.funding_collections = 3

    entry = status_pub._build_one_position(trade, {}, {})

    assert entry["id"] == "test-1"
    assert entry["symbol"] == "BTC/USDT"
    assert entry["long_exchange"] == "exchange_a"
    assert entry["state"] == "open"


@pytest.mark.asyncio
async def test_publish_pnl_with_positions(status_pub, mock_redis):
    """_publish_pnl should sum unrealized PnL from position cache."""
    mock_redis.zrangebyscore = AsyncMock(return_value=[])
    mock_redis.zadd = AsyncMock()
    mock_redis.zremrangebyscore = AsyncMock()
    mock_redis.set = AsyncMock()

    trade = MagicMock()
    trade.long_exchange = "exchange_a"
    trade.short_exchange = "exchange_b"
    trade.symbol = "BTC/USDT"

    pos = MagicMock()
    pos.unrealized_pnl = 1.5
    position_cache = {
        ("exchange_a", "BTC/USDT"): [pos],
        ("exchange_b", "BTC/USDT"): [pos],
    }

    await status_pub._publish_pnl([("t1", trade)], position_cache, 1000000.0)

    # The zadd call should contain the unrealized PnL sum
    call_args = mock_redis.zadd.call_args[0]
    snapshot = json.loads(list(call_args[1].keys())[0])
    assert snapshot["unrealized"] == 3.0  # 1.5 + 1.5


@pytest.mark.asyncio
async def test_enrich_funding_spread_no_cache(status_pub, mock_exchange_mgr):
    """_enrich_funding_spread should gracefully handle missing cached rates."""
    adapter_a = mock_exchange_mgr.get("exchange_a")
    adapter_a.get_funding_rate_cached = lambda sym: None

    pos_entry = {"immediate_spread_pct": None, "current_spread_pct": None,
                 "current_long_rate": None, "current_short_rate": None,
                 "next_funding_ms": None}
    trade = MagicMock()
    trade.long_exchange = "exchange_a"
    trade.short_exchange = "exchange_b"
    trade.symbol = "BTC/USDT"

    status_pub._enrich_funding_spread(pos_entry, trade)
    # Should remain None since no cached rates
    assert pos_entry["current_long_rate"] is None
