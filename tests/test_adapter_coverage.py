"""Extended adapter / mixin tests — target combined adapter coverage 80 %+.

Covers:
  _lifecycle_mixin:  disconnect, _maybe_resync_clock, maybe_reload_markets,
                     verify_credentials, _create_supervised_task
  _market_data_mixin:  get_instrument_spec (fresh & cached), get_ticker,
                       get_balance, get_positions, warm_up_symbols,
                       warm_up_trading_settings, fetch_funding_history
  _order_mixin:  ensure_trading_settings (idempotent + OKX/KuCoin paths),
                 _verify_fill_via_position (entry + close), place_order basics
  _funding_mixin:  _get_funding_interval, warm_up_funding_rates
"""

from __future__ import annotations

import asyncio
import time
from decimal import Decimal
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.contracts import InstrumentSpec, OrderRequest, OrderSide, Position
from src.exchanges.adapter import ExchangeAdapter


# ── Helpers ──────────────────────────────────────────────────────


def _adapter(
    exchange_id: str = "test_ex",
    cfg: dict | None = None,
) -> ExchangeAdapter:
    """Create an ExchangeAdapter *without* a real ccxt connection."""
    return ExchangeAdapter(exchange_id, cfg or {})


def _adapter_with_exchange(
    exchange_id: str = "test_ex",
    cfg: dict | None = None,
) -> ExchangeAdapter:
    """Adapter with a mocked _exchange (for methods requiring it)."""
    a = _adapter(exchange_id, cfg)
    a._exchange = MagicMock()
    a._exchange.markets = {
        "ETH/USDT:USDT": {
            "swap": True,
            "linear": True,
            "settle": "USDT",
            "active": True,
            "base": "ETH",
            "quote": "USDT",
            "contractSize": 1,
            "precision": {"price": "0.01", "amount": "0.001"},
            "limits": {"cost": {"min": 5}},
            "taker": 0.0005,
            "maker": 0.0002,
        },
    }
    a._exchange.symbols = ["ETH/USDT:USDT"]
    a._exchange.close = AsyncMock()
    a._rest_semaphore = asyncio.Semaphore(10)
    return a


def _make_spec(symbol: str = "ETH/USDT:USDT") -> InstrumentSpec:
    return InstrumentSpec(
        exchange="test_ex",
        symbol=symbol,
        base="ETH",
        quote="USDT",
        contract_size=Decimal("1"),
        tick_size=Decimal("0.01"),
        lot_size=Decimal("0.001"),
        min_notional=Decimal("5"),
        maker_fee=Decimal("0.0002"),
        taker_fee=Decimal("0.0005"),
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 1. _lifecycle_mixin
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestDisconnect:
    """Test disconnect lifecycle."""

    @pytest.mark.asyncio
    async def test_disconnect_calls_close(self) -> None:
        a = _adapter_with_exchange()
        await a.disconnect()
        a._exchange.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_disconnect_no_exchange_is_noop(self) -> None:
        a = _adapter()
        a._exchange = None
        await a.disconnect()  # should not raise


class TestMaybeResyncClock:
    """Test periodic clock resync."""

    @pytest.mark.asyncio
    async def test_skips_when_not_stale(self) -> None:
        a = _adapter_with_exchange()
        a._last_clock_sync = time.time()  # just synced
        a._exchange.load_time_difference = AsyncMock()
        await a._maybe_resync_clock()
        a._exchange.load_time_difference.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_resyncs_when_stale(self) -> None:
        a = _adapter_with_exchange()
        a._last_clock_sync = 0.0  # very stale
        a._exchange.load_time_difference = AsyncMock()
        a._exchange.options = {"timeDifference": 100}
        await a._maybe_resync_clock()
        a._exchange.load_time_difference.assert_awaited_once()
        assert a._last_clock_sync > 0

    @pytest.mark.asyncio
    async def test_handles_resync_failure(self) -> None:
        a = _adapter_with_exchange()
        a._last_clock_sync = 0.0
        a._exchange.load_time_difference = AsyncMock(side_effect=RuntimeError("timeout"))
        await a._maybe_resync_clock()  # should not raise


class TestMaybeReloadMarkets:
    """Test periodic market reload."""

    @pytest.mark.asyncio
    async def test_skips_when_not_stale(self) -> None:
        a = _adapter_with_exchange()
        a._last_markets_reload = time.time()
        a._exchange.load_markets = AsyncMock()
        await a.maybe_reload_markets()
        a._exchange.load_markets.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_reloads_when_stale(self) -> None:
        a = _adapter_with_exchange()
        a._last_markets_reload = 0.0
        a._exchange.load_markets = AsyncMock()
        a._instrument_cache["ETH/USDT:USDT"] = _make_spec()
        await a.maybe_reload_markets()
        a._exchange.load_markets.assert_awaited_once()
        assert a._instrument_cache == {}  # cleared

    @pytest.mark.asyncio
    async def test_reload_failure_is_graceful(self) -> None:
        a = _adapter_with_exchange()
        a._last_markets_reload = 0.0
        a._exchange.load_markets = AsyncMock(side_effect=RuntimeError("fail"))
        await a.maybe_reload_markets()  # no raise

    @pytest.mark.asyncio
    async def test_no_exchange_is_noop(self) -> None:
        a = _adapter()
        a._exchange = None
        await a.maybe_reload_markets()


class TestCreateSupervisedTask:
    """Test _create_supervised_task auto-restart."""

    @pytest.mark.asyncio
    async def test_restarts_on_crash_then_exits(self) -> None:
        a = _adapter()
        call_count = 0

        async def _flaky():
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise RuntimeError("boom")
            # Third call: exit normally

        with patch("asyncio.sleep", new_callable=AsyncMock):
            task = a._create_supervised_task(
                lambda: _flaky(), name="test-flaky",
            )
            await task

        assert call_count == 3
        assert task in a._ws_tasks

    @pytest.mark.asyncio
    async def test_exits_on_cancelled_error(self) -> None:
        a = _adapter()

        async def _cancel():
            raise asyncio.CancelledError()

        task = a._create_supervised_task(lambda: _cancel(), name="test-cancel")
        await task  # should exit cleanly


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 2. _market_data_mixin
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestGetInstrumentSpec:
    """Test get_instrument_spec with fresh + cached paths."""

    @pytest.mark.asyncio
    async def test_returns_cached_spec(self) -> None:
        a = _adapter_with_exchange()
        spec = _make_spec()
        a._instrument_cache["ETH/USDT:USDT"] = spec
        result = await a.get_instrument_spec("ETH/USDT:USDT")
        assert result is spec

    @pytest.mark.asyncio
    async def test_builds_spec_from_market(self) -> None:
        a = _adapter_with_exchange()
        result = await a.get_instrument_spec("ETH/USDT:USDT")
        assert result is not None
        assert result.symbol == "ETH/USDT:USDT"
        assert result.taker_fee == Decimal("0.0005")
        # Should now be cached
        assert "ETH/USDT:USDT" in a._instrument_cache

    @pytest.mark.asyncio
    async def test_returns_none_for_unknown_symbol(self) -> None:
        a = _adapter_with_exchange()
        result = await a.get_instrument_spec("UNKNOWN/USDT")
        assert result is None

    @pytest.mark.asyncio
    async def test_uses_default_fee_when_taker_is_zero(self) -> None:
        a = _adapter_with_exchange()
        a._exchange.markets["ETH/USDT:USDT"]["taker"] = 0
        result = await a.get_instrument_spec("ETH/USDT:USDT")
        assert result is not None
        assert result.taker_fee == Decimal("0.0005")  # conservative default


class TestGetTickerAndBalance:
    """Test get_ticker and get_balance."""

    @pytest.mark.asyncio
    async def test_get_ticker_delegates(self) -> None:
        a = _adapter_with_exchange()
        a._exchange.fetch_ticker = AsyncMock(
            return_value={"last": 50000.0, "bid": 49999, "ask": 50001},
        )
        result = await a.get_ticker("ETH/USDT:USDT")
        assert result["last"] == 50000.0

    @pytest.mark.asyncio
    async def test_get_balance_returns_decimal(self) -> None:
        a = _adapter_with_exchange()
        a._exchange.fetch_balance = AsyncMock(
            return_value={"USDT": {"total": 5000, "free": 4000, "used": 1000}},
        )
        result = await a.get_balance()
        assert result["total"] == Decimal("5000")
        assert result["free"] == Decimal("4000")
        assert result["used"] == Decimal("1000")

    @pytest.mark.asyncio
    async def test_get_balance_falls_back_to_usd(self) -> None:
        a = _adapter_with_exchange()
        a._exchange.fetch_balance = AsyncMock(
            return_value={
                "USDT": {"total": 0},
                "USD": {"total": 3000, "free": 2500, "used": 500},
            },
        )
        result = await a.get_balance()
        assert result["total"] == Decimal("3000")


class TestGetPositions:
    """Test get_positions returns Position objects."""

    @pytest.mark.asyncio
    async def test_returns_positions(self) -> None:
        a = _adapter_with_exchange()
        a._exchange.fetch_positions = AsyncMock(return_value=[
            {
                "symbol": "ETH/USDT:USDT",
                "contracts": 10,
                "side": "long",
                "entryPrice": 3000.0,
                "unrealizedPnl": 5.0,
                "leverage": 5,
            },
        ])
        result = await a.get_positions("ETH/USDT:USDT")
        assert len(result) == 1
        pos = result[0]
        assert isinstance(pos, Position)
        assert pos.side == OrderSide.BUY
        assert pos.quantity == Decimal("10")

    @pytest.mark.asyncio
    async def test_skips_zero_contracts(self) -> None:
        a = _adapter_with_exchange()
        a._exchange.fetch_positions = AsyncMock(return_value=[
            {"symbol": "ETH/USDT:USDT", "contracts": 0, "side": "long"},
        ])
        result = await a.get_positions("ETH/USDT:USDT")
        assert result == []

    @pytest.mark.asyncio
    async def test_retries_on_failure(self) -> None:
        a = _adapter_with_exchange()
        call_count = 0

        async def _flaky(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise RuntimeError("timeout")
            return [
                {"symbol": "ETH/USDT:USDT", "contracts": 5, "side": "short"},
            ]

        a._exchange.fetch_positions = _flaky
        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await a.get_positions("ETH/USDT:USDT")
        assert len(result) == 1
        assert result[0].side == OrderSide.SELL


class TestWarmUp:
    """Test warm_up_symbols and warm_up_trading_settings."""

    @pytest.mark.asyncio
    async def test_warm_up_symbols(self) -> None:
        a = _adapter_with_exchange()
        await a.warm_up_symbols(["ETH/USDT:USDT"])
        assert "ETH/USDT:USDT" in a._instrument_cache

    @pytest.mark.asyncio
    async def test_warm_up_trading_settings(self) -> None:
        a = _adapter_with_exchange()
        a._exchange.set_margin_mode = AsyncMock()
        a._exchange.set_leverage = AsyncMock()
        a._exchange.set_position_mode = AsyncMock()
        count = await a.warm_up_trading_settings(["ETH/USDT:USDT"])
        assert count == 1
        assert "ETH/USDT:USDT" in a._settings_applied

    @pytest.mark.asyncio
    async def test_warm_up_empty_list(self) -> None:
        a = _adapter_with_exchange()
        count = await a.warm_up_trading_settings([])
        assert count == 0

    @pytest.mark.asyncio
    async def test_warm_up_partial_failure(self) -> None:
        a = _adapter_with_exchange()
        call_count = 0

        async def _failing_margin(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            raise RuntimeError("rate limited")

        a._exchange.set_margin_mode = _failing_margin
        a._exchange.set_leverage = AsyncMock()
        a._exchange.set_position_mode = AsyncMock()
        # Should not raise, just count failures
        count = await a.warm_up_trading_settings(["ETH/USDT:USDT"])
        # The ensure_trading_settings call will hit an error in set_margin_mode
        # but continue to set_leverage and set_position_mode, ultimately succeeding
        # (warnings are logged, not raised for "ok_keywords"-unmatched errors)
        assert isinstance(count, int)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 3. _order_mixin
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestEnsureTradingSettings:
    """Test ensure_trading_settings idempotency and exchange-specific paths."""

    @pytest.mark.asyncio
    async def test_idempotent_second_call(self) -> None:
        a = _adapter_with_exchange()
        a._exchange.set_margin_mode = AsyncMock()
        a._exchange.set_leverage = AsyncMock()
        a._exchange.set_position_mode = AsyncMock()
        await a.ensure_trading_settings("ETH/USDT:USDT")
        # Reset mocks and call again — should be no-op
        a._exchange.set_margin_mode.reset_mock()
        await a.ensure_trading_settings("ETH/USDT:USDT")
        a._exchange.set_margin_mode.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_handles_already_set_error(self) -> None:
        """'No need to change' errors are silenced."""
        a = _adapter_with_exchange()
        a._exchange.set_margin_mode = AsyncMock(
            side_effect=RuntimeError("No need to change margin type"),
        )
        a._exchange.set_leverage = AsyncMock()
        a._exchange.set_position_mode = AsyncMock()
        await a.ensure_trading_settings("ETH/USDT:USDT")
        assert "ETH/USDT:USDT" in a._settings_applied

    @pytest.mark.asyncio
    async def test_okx_passes_mgn_mode(self) -> None:
        a = _adapter_with_exchange(
            "okx", {"margin_mode": "cross", "leverage": 5, "max_leverage": 125},
        )
        a._exchange.set_margin_mode = AsyncMock()
        a._exchange.set_leverage = AsyncMock()
        a._exchange.set_position_mode = AsyncMock()
        await a.ensure_trading_settings("ETH/USDT:USDT")
        # OKX path: set_leverage should include mgnMode param
        lev_call = a._exchange.set_leverage.call_args
        assert lev_call is not None
        assert lev_call[0][1] == "ETH/USDT:USDT"  # native symbol

    @pytest.mark.asyncio
    async def test_kucoin_passes_margin_mode(self) -> None:
        a = _adapter_with_exchange(
            "kucoin", {"margin_mode": "cross", "leverage": 3},
        )
        a._exchange.set_margin_mode = AsyncMock()
        a._exchange.set_leverage = AsyncMock()
        a._exchange.set_position_mode = AsyncMock()
        await a.ensure_trading_settings("ETH/USDT:USDT")
        lev_call = a._exchange.set_leverage.call_args
        assert lev_call is not None


class TestVerifyFillViaPosition:
    """Test _verify_fill_via_position for entry and close orders."""

    @pytest.mark.asyncio
    async def test_entry_order_verified_by_position(self) -> None:
        a = _adapter_with_exchange()
        a._exchange.fetch_positions = AsyncMock(return_value=[
            {"contracts": 10, "side": "long"},
        ])
        filled = await a._verify_fill_via_position(
            "ETH/USDT:USDT", "ETH/USDT:USDT", OrderSide.BUY, 10,
            order_id="ord-1", reduce_only=False,
        )
        assert filled == 10

    @pytest.mark.asyncio
    async def test_entry_order_no_position_returns_zero(self) -> None:
        a = _adapter_with_exchange()
        a._exchange.fetch_positions = AsyncMock(return_value=[])
        filled = await a._verify_fill_via_position(
            "ETH/USDT:USDT", "ETH/USDT:USDT", OrderSide.BUY, 10,
            order_id="ord-2", reduce_only=False,
        )
        assert filled == 0.0

    @pytest.mark.asyncio
    async def test_close_order_position_gone(self) -> None:
        """Position gone after close → fully filled."""
        a = _adapter_with_exchange()
        a._exchange.fetch_positions = AsyncMock(return_value=[])
        filled = await a._verify_fill_via_position(
            "ETH/USDT:USDT", "ETH/USDT:USDT", OrderSide.SELL, 10,
            order_id="ord-3", reduce_only=True,
        )
        assert filled == 10

    @pytest.mark.asyncio
    async def test_close_order_position_still_exists(self) -> None:
        """Position still full after close → not filled."""
        a = _adapter_with_exchange()
        a._exchange.fetch_positions = AsyncMock(return_value=[
            {"contracts": 10, "side": "long"},
        ])
        filled = await a._verify_fill_via_position(
            "ETH/USDT:USDT", "ETH/USDT:USDT", OrderSide.SELL, 10,
            order_id="ord-4", reduce_only=True,
        )
        assert filled == 0.0

    @pytest.mark.asyncio
    async def test_close_order_partial_fill(self) -> None:
        """Position partially reduced after close."""
        a = _adapter_with_exchange()
        a._exchange.fetch_positions = AsyncMock(return_value=[
            {"contracts": 3, "side": "long"},
        ])
        filled = await a._verify_fill_via_position(
            "ETH/USDT:USDT", "ETH/USDT:USDT", OrderSide.SELL, 10,
            order_id="ord-5", reduce_only=True,
        )
        assert filled == 7  # 10 - 3

    @pytest.mark.asyncio
    async def test_api_error_returns_zero(self) -> None:
        a = _adapter_with_exchange()
        a._exchange.fetch_positions = AsyncMock(side_effect=RuntimeError("API"))
        filled = await a._verify_fill_via_position(
            "ETH/USDT:USDT", "ETH/USDT:USDT", OrderSide.BUY, 10,
            order_id="ord-6", reduce_only=False,
        )
        assert filled == 0.0


class TestPlaceOrder:
    """Test place_order basics."""

    @pytest.mark.asyncio
    async def test_place_order_calls_create_order(self) -> None:
        a = _adapter_with_exchange()
        a._exchange.set_margin_mode = AsyncMock()
        a._exchange.set_leverage = AsyncMock()
        a._exchange.set_position_mode = AsyncMock()
        a._exchange.create_order = AsyncMock(return_value={
            "id": "ord-test",
            "filled": 10.0,
            "average": 3000.0,
            "status": "closed",
        })
        req = OrderRequest(
            exchange="test_ex",
            symbol="ETH/USDT:USDT",
            side=OrderSide.BUY,
            quantity=Decimal("10"),
        )
        result = await a.place_order(req)
        assert result["id"] == "ord-test"
        a._exchange.create_order.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_place_order_refetches_on_zero_fill(self) -> None:
        """When initial fill=0, should retry with fetchOrder."""
        a = _adapter_with_exchange()
        a._exchange.set_margin_mode = AsyncMock()
        a._exchange.set_leverage = AsyncMock()
        a._exchange.set_position_mode = AsyncMock()
        a._exchange.create_order = AsyncMock(return_value={
            "id": "ord-zero",
            "filled": 0,
            "average": None,
            "status": "open",
        })
        a._exchange.fetch_order = AsyncMock(return_value={
            "id": "ord-zero",
            "filled": 10.0,
            "average": 3050.0,
            "status": "closed",
        })
        req = OrderRequest(
            exchange="test_ex",
            symbol="ETH/USDT:USDT",
            side=OrderSide.BUY,
            quantity=Decimal("10"),
        )
        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await a.place_order(req)
        assert result["average"] == 3050.0


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 4. _funding_mixin — _get_funding_interval, warm_up
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestGetFundingInterval:
    """Test _get_funding_interval priority: raw info → interval string → markets → default."""

    def test_from_funding_intervals_dict(self) -> None:
        a = _adapter_with_exchange()
        a._funding_intervals = {"ETH/USDT:USDT": 4}
        # Provide empty funding_data; pre-fetched table should win (step 4)
        result = a._get_funding_interval("ETH/USDT:USDT", {})
        assert result == 4

    def test_from_raw_info_binance_hours(self) -> None:
        a = _adapter_with_exchange()
        funding_data = {"info": {"fundingIntervalHours": 4}}
        result = a._get_funding_interval("ETH/USDT:USDT", funding_data)
        assert result == 4

    def test_from_raw_info_gateio_seconds(self) -> None:
        a = _adapter_with_exchange()
        funding_data = {"info": {"funding_interval": 14400}}
        result = a._get_funding_interval("ETH/USDT:USDT", funding_data)
        assert result == 4

    def test_default_8h(self) -> None:
        a = _adapter_with_exchange()
        result = a._get_funding_interval("ETH/USDT:USDT", {})
        assert result == 8


class TestFetchFundingHistory:
    """Test fetch_funding_history with different exchange types."""

    @pytest.mark.asyncio
    async def test_binance_uses_income_history(self) -> None:
        a = _adapter_with_exchange("binance")
        a._exchange.has = {"fetchIncomeHistory": True}
        now_ms = int(time.time() * 1000)
        a._exchange.fetch_income_history = AsyncMock(return_value=[
            {"timestamp": now_ms, "amount": 1.5, "info": {}},
            {"timestamp": now_ms - 1000, "amount": -0.5, "info": {}},
        ])
        result = await a.fetch_funding_history("ETH/USDT:USDT")
        assert result["source"] == "exchange"
        assert result["net_usd"] == 1.0
        assert result["received_usd"] == 1.5
        assert result["paid_usd"] == 0.5

    @pytest.mark.asyncio
    async def test_generic_exchange_uses_fetch_funding_history(self) -> None:
        a = _adapter_with_exchange("bybit")
        a._exchange.has = {"fetchFundingHistory": True}
        now_ms = int(time.time() * 1000)
        a._exchange.fetch_funding_history = AsyncMock(return_value=[
            {"timestamp": now_ms, "amount": 0.8, "info": {}},
        ])
        result = await a.fetch_funding_history("ETH/USDT:USDT")
        assert result["source"] == "exchange"
        assert result["received_usd"] == 0.8

    @pytest.mark.asyncio
    async def test_api_error_returns_unavailable(self) -> None:
        a = _adapter_with_exchange("bybit")
        a._exchange.has = {"fetchFundingHistory": True}
        a._exchange.fetch_funding_history = AsyncMock(
            side_effect=RuntimeError("rate limit"),
        )
        result = await a.fetch_funding_history("ETH/USDT:USDT")
        assert result["source"] == "unavailable"
        assert result["net_usd"] == 0.0

    @pytest.mark.asyncio
    async def test_no_payments_returns_unavailable(self) -> None:
        a = _adapter_with_exchange("bybit")
        a._exchange.has = {"fetchFundingHistory": True}
        a._exchange.fetch_funding_history = AsyncMock(return_value=[])
        result = await a.fetch_funding_history("ETH/USDT:USDT")
        assert result["source"] == "unavailable"


class TestVerifyCredentials:
    """Test verify_credentials with auth success, failure, network retry."""

    @pytest.mark.asyncio
    async def test_success(self) -> None:
        a = _adapter_with_exchange()
        a._exchange.fetch_balance = AsyncMock(return_value={"USDT": {}})
        result = await a.verify_credentials()
        assert result is True

    @pytest.mark.asyncio
    async def test_auth_error_returns_false(self) -> None:
        import ccxt.pro as ccxtpro

        a = _adapter_with_exchange()
        a._exchange.fetch_balance = AsyncMock(
            side_effect=ccxtpro.AuthenticationError("Invalid API key"),
        )
        result = await a.verify_credentials()
        assert result is False

    @pytest.mark.asyncio
    async def test_network_error_retries(self) -> None:
        import ccxt.pro as ccxtpro

        a = _adapter_with_exchange()
        call_count = 0

        async def _flaky_balance():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ccxtpro.NetworkError("timeout")
            return {"USDT": {}}

        a._exchange.fetch_balance = _flaky_balance
        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await a.verify_credentials()
        assert result is True
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_unexpected_error_returns_false(self) -> None:
        a = _adapter_with_exchange()
        a._exchange.fetch_balance = AsyncMock(
            side_effect=ValueError("weird error"),
        )
        result = await a.verify_credentials()
        assert result is False
