"""
Tests for ExchangeAdapter — watcher resilience.
"""

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.exchanges.adapter import ExchangeAdapter


# ── Helpers ──────────────────────────────────────────────────────


def _make_adapter() -> ExchangeAdapter:
    """Create a minimal ExchangeAdapter with mocked exchange."""
    cfg = {
        "ccxt_id": "binanceusdm",
        "api_key": "test",
        "api_secret": "test",
        "default_type": "swap",
    }
    adapter = ExchangeAdapter("test_exchange", cfg)
    adapter._exchange = MagicMock()
    adapter._exchange.markets = {"BTC/USDT:USDT": {}}
    return adapter


# ── Watcher resilience tests ────────────────────────────────────


class TestWatcherResilience:
    """Verify that _watch_funding_rate_loop never silently dies."""

    @pytest.mark.asyncio
    async def test_survives_beyond_five_failures(self):
        """The old code had max_retries=5.  Verify the loop retries beyond 5."""
        adapter = _make_adapter()
        adapter._ws_funding_supported = False  # force polling path

        call_count = 0

        async def _fake_polling(symbol):
            nonlocal call_count
            call_count += 1
            if call_count <= 8:
                raise RuntimeError(f"Connection refused (attempt {call_count})")
            # After 8 failures, "succeed" by running briefly then cancel
            raise asyncio.CancelledError()

        with patch.object(adapter, "_watch_funding_rate_polling", side_effect=_fake_polling):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                await adapter._watch_funding_rate_loop("BTC/USDT:USDT")

        # Should have been called 9 times: 8 failures + 1 cancel
        assert call_count == 9, (
            f"Expected loop to survive past 5 retries; got {call_count} calls"
        )

    @pytest.mark.asyncio
    async def test_backoff_resets_after_success(self):
        """After successful data, consecutive_failures should reset."""
        adapter = _make_adapter()
        adapter._ws_funding_supported = False

        call_count = 0

        async def _fake_polling(symbol):
            nonlocal call_count
            call_count += 1
            if call_count <= 3:
                raise RuntimeError("fail")
            if call_count == 4:
                # "Success" — return normally (which means the inner
                # while-True polling exited cleanly)
                return
            if call_count <= 6:
                raise RuntimeError("fail again after reset")
            raise asyncio.CancelledError()

        sleep_args = []
        original_sleep = asyncio.sleep

        async def _track_sleep(secs, *a, **kw):
            sleep_args.append(secs)

        with patch.object(adapter, "_watch_funding_rate_polling", side_effect=_fake_polling):
            with patch("asyncio.sleep", side_effect=_track_sleep):
                await adapter._watch_funding_rate_loop("BTC/USDT:USDT")

        assert call_count == 7
        # After the success at call 4, backoff should reset — so
        # failures at calls 5-6 should start from the base backoff again.
        # Calls 1-3 fail: sleeps at indices 0,1,2
        # Call 4 succeeds: no sleep
        # Calls 5-6 fail: sleeps at indices 3,4  — should be base backoff (5s)
        assert len(sleep_args) == 5
        # After reset, first failure should use base backoff (5s)
        assert sleep_args[3] == 5


class TestBatchPollResilience:
    """Verify batch polling logs warnings on consecutive failures."""

    @pytest.mark.asyncio
    async def test_logs_warning_on_consecutive_failures(self):
        """Batch poll loop should log warnings (not just debug) on failures."""
        adapter = _make_adapter()

        call_count = 0

        async def _fake_fetch_rates(symbols=None):
            nonlocal call_count
            call_count += 1
            if call_count <= 3:
                raise RuntimeError("API error")
            raise asyncio.CancelledError()

        adapter._exchange.fetch_funding_rates = AsyncMock(side_effect=_fake_fetch_rates)

        with patch("asyncio.sleep", new_callable=AsyncMock):
            with patch("src.exchanges.adapter.logger") as mock_logger:
                await adapter._batch_funding_poll_loop(["BTC/USDT:USDT"])

        # Should have logged 3 warnings (consecutive_failures 1,2,3)
        warning_calls = [c for c in mock_logger.warning.call_args_list
                         if "Batch funding poll error" in str(c)]
        assert len(warning_calls) == 3


class TestSequentialPollResilience:
    """Verify sequential polling tracks full-failure cycles."""

    @pytest.mark.asyncio
    async def test_logs_warning_when_all_symbols_fail(self):
        """When every symbol fetch fails, log a warning."""
        adapter = _make_adapter()

        cycle_count = 0

        async def _fake_fetch_rate(symbol):
            raise RuntimeError("down")

        async def _fake_sleep(secs):
            nonlocal cycle_count
            cycle_count += 1
            if cycle_count >= 3:
                raise asyncio.CancelledError()

        adapter._exchange.fetch_funding_rate = AsyncMock(side_effect=_fake_fetch_rate)

        with patch("asyncio.sleep", side_effect=_fake_sleep):
            with patch("src.exchanges.adapter.logger") as mock_logger:
                # CancelledError from sleep propagates (sleep is outside try)
                with pytest.raises(asyncio.CancelledError):
                    await adapter._sequential_funding_poll_loop(["BTC/USDT:USDT"])

        warning_calls = [c for c in mock_logger.warning.call_args_list
                         if "Sequential funding refresh: 0/" in str(c)]
        assert len(warning_calls) >= 1
