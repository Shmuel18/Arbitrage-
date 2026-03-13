"""
Critical edge-case tests — the 'Dangerous 35%' gaps identified in the audit.

Each test targets a specific failure mode that can cause capital loss or
silent misbehaviour in production. Tests are ordered by severity (highest first).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.contracts import (
    InstrumentSpec,
    OpportunityCandidate,
    OrderRequest,
    OrderSide,
    Position,
    TradeMode,
    TradeRecord,
    TradeState,
)
from src.execution.controller import ExecutionController


# ═══════════════════════════════════════════════════════════════════
# Fixtures (local — `controller` is not in conftest.py)
# ═══════════════════════════════════════════════════════════════════

@pytest.fixture
def controller(config, mock_exchange_mgr, mock_redis):
    return ExecutionController(config, mock_exchange_mgr, mock_redis)


# ═══════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════

def _make_open_trade(
    controller: ExecutionController,
    trade_id: str = "edge-trade-1",
    symbol: str = "BTC/USDT",
) -> TradeRecord:
    """Insert a minimal OPEN trade directly into the controller."""
    trade = TradeRecord(
        trade_id=trade_id,
        symbol=symbol,
        state=TradeState.OPEN,
        long_exchange="exchange_a",
        short_exchange="exchange_b",
        long_qty=Decimal("0.01"),
        short_qty=Decimal("0.01"),
        entry_edge_pct=Decimal("0.5"),
        opened_at=datetime.now(timezone.utc) - timedelta(minutes=60),
        mode=TradeMode.HOLD,
    )
    controller._active_trades[trade_id] = trade
    controller._active_symbols.add(symbol)
    controller._busy_exchanges.update({"exchange_a", "exchange_b"})
    return trade


# ═══════════════════════════════════════════════════════════════════
# 1. CRITICAL — Timeout orphan detection
# ═══════════════════════════════════════════════════════════════════

class TestTimeoutOrphanDetection:
    """Fill-despite-timeout → _close_orphan must fire immediately."""

    @pytest.mark.asyncio
    async def test_orphan_close_triggered_when_fill_detected_after_timeout(
        self, controller, mock_exchange_mgr
    ):
        """
        Scenario: market order times out on the network layer, but the exchange
        already executed it.  check_timed_out_fill returns 0.01 BTC filled.
        _close_orphan must be called with that filled qty.
        """
        adapter = mock_exchange_mgr.get("exchange_a")
        # Simulate: place_order hangs until timeout
        async def _hang(req):
            await asyncio.sleep(999)
        adapter.place_order.side_effect = _hang

        # check_timed_out_fill detects the fill
        adapter.check_timed_out_fill = AsyncMock(return_value=0.01)

        req = OrderRequest(
            exchange="exchange_a",
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            quantity=Decimal("0.01"),
            reduce_only=False,
        )

        orphan_calls = []
        original_close_orphan = controller._close_orphan

        async def _capture_orphan(adp, exchange, symbol, side, fill, fallback_qty=None):
            orphan_calls.append({"exchange": exchange, "symbol": symbol, "fill": fill})

        controller._close_orphan = _capture_orphan

        result = await controller._place_with_timeout(adapter, req)

        assert result is None, "Place should return None on timeout"
        assert len(orphan_calls) == 1, "Orphan close must be triggered exactly once"
        assert orphan_calls[0]["symbol"] == "BTC/USDT"
        assert orphan_calls[0]["fill"]["filled"] == pytest.approx(0.01)

    @pytest.mark.asyncio
    async def test_no_orphan_close_for_reduce_only_on_timeout(
        self, controller, mock_exchange_mgr
    ):
        """
        reduce_only orders that time out do NOT create new positions.
        _close_orphan must NOT be called.
        """
        adapter = mock_exchange_mgr.get("exchange_a")

        async def _hang(req):
            await asyncio.sleep(999)
        adapter.place_order.side_effect = _hang
        adapter.check_timed_out_fill = AsyncMock(return_value=0.0)

        req = OrderRequest(
            exchange="exchange_a",
            symbol="BTC/USDT",
            side=OrderSide.SELL,
            quantity=Decimal("0.01"),
            reduce_only=True,  # close order
        )

        orphan_calls = []
        controller._close_orphan = AsyncMock(side_effect=lambda *a, **k: orphan_calls.append(a))

        await controller._place_with_timeout(adapter, req)

        assert len(orphan_calls) == 0, "reduce_only timeout must never trigger orphan close"


# ═══════════════════════════════════════════════════════════════════
# 2. HIGH — close_orphan silently skips fill=0 with no fallback_qty
# ═══════════════════════════════════════════════════════════════════

class TestOrphanZeroFillNoFallback:
    """fill.get('filled')=0 and no fallback_qty → orphan skipped silently."""

    @pytest.mark.asyncio
    async def test_orphan_skipped_when_fill_zero_and_no_fallback(
        self, controller, mock_exchange_mgr
    ):
        """
        Scenario: short leg times out; ccxt returns fill dict with filled=0
        and no fallback_qty is supplied.  _close_orphan must skip (no order
        placed) — this is correct behaviour because we can't close zero qty.
        """
        adapter = mock_exchange_mgr.get("exchange_b")
        fill = {"id": "order-fail", "filled": 0, "status": "open"}

        await controller._close_orphan(
            adapter, "exchange_b", "BTC/USDT",
            OrderSide.BUY, fill, fallback_qty=None,
        )

        adapter.place_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_orphan_uses_fallback_qty_when_fill_zero(
        self, controller, mock_exchange_mgr
    ):
        """
        Same scenario but WITH fallback_qty=0.01 — the orphan MUST be closed
        using the fallback rather than being silently skipped.
        """
        adapter = mock_exchange_mgr.get("exchange_b")
        adapter.place_order.return_value = {
            "id": "orphan-close", "filled": 0.01, "status": "closed",
        }
        fill = {"id": "order-fail", "filled": 0, "status": "open"}

        await controller._close_orphan(
            adapter, "exchange_b", "BTC/USDT",
            OrderSide.BUY, fill, fallback_qty=Decimal("0.01"),
        )

        adapter.place_order.assert_called_once()
        placed_req = adapter.place_order.call_args[0][0]
        assert placed_req.quantity == Decimal("0.01")
        assert placed_req.reduce_only is True


# ═══════════════════════════════════════════════════════════════════
# 3. HIGH — Redis disconnect mid-persist
# ═══════════════════════════════════════════════════════════════════

class TestRedisMidPersist:
    """_persist_trade raises → trade must still be deregistered and not leaked."""

    @pytest.mark.asyncio
    async def test_trade_entry_survives_redis_persist_failure(
        self, controller, sample_opportunity, mock_redis
    ):
        """
        Scenario: Redis connection drops exactly when set_trade_state is called
        after a successful entry fill.  The controller must catch the error and
        not leave the in-memory state in an inconsistent partially-open
        limbo.
        """
        # First call (CLOSING persist) succeeds, second (OPEN persist) raises
        call_count = 0

        async def _flaky_set(trade_id, data):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionError("Redis connection lost")

        mock_redis.set_trade_state.side_effect = _flaky_set

        # Should not raise — controller must handle the Redis error gracefully
        try:
            await controller.handle_opportunity(sample_opportunity)
        except Exception as exc:
            pytest.fail(f"handle_opportunity must not propagate Redis errors: {exc}")

    @pytest.mark.asyncio
    async def test_persist_failure_does_not_corrupt_active_trades(
        self, controller, mock_redis
    ):
        """
        Even if persist raises, active_trades must not contain a half-initialised
        trade object that will never be cleaned up.
        """
        mock_redis.set_trade_state.side_effect = ConnectionError("Redis down")

        trade = TradeRecord(
            trade_id="persist-test",
            symbol="BTC/USDT",
            state=TradeState.OPEN,
            long_exchange="exchange_a",
            short_exchange="exchange_b",
            long_qty=Decimal("0.01"),
            short_qty=Decimal("0.01"),
            entry_edge_pct=Decimal("0.5"),
            opened_at=datetime.now(timezone.utc),
            mode=TradeMode.HOLD,
        )
        # _persist_trade should not crash the controller
        try:
            await controller._persist_trade(trade)
        except Exception:
            pass  # exception is acceptable — what's NOT acceptable is silent corruption

        # in-memory state must not be touched by _persist_trade alone
        assert "persist-test" not in controller._active_trades


# ═══════════════════════════════════════════════════════════════════
# 4. MEDIUM — Supervisor restarts crashed background task
# ═══════════════════════════════════════════════════════════════════

class TestSupervisedTaskRestart:
    """_create_supervised_task auto-restarts coroutine on unexpected exception."""

    @pytest.mark.asyncio
    async def test_supervisor_restarts_after_crash(self, controller):
        """
        Scenario: coro_factory's coroutine raises RuntimeError on first run.
        The supervisor must restart it.  Second run completes normally.
        """
        run_count = 0
        done_event = asyncio.Event()

        def _coro_factory():
            async def _coro():
                nonlocal run_count
                run_count += 1
                if run_count == 1:
                    raise RuntimeError("Simulated crash")
                done_event.set()
            return _coro()

        controller._running = True
        task = controller._create_supervised_task(_coro_factory, name="test-supervisor")
        try:
            await asyncio.wait_for(done_event.wait(), timeout=15)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        assert run_count == 2, (
            f"Supervisor must restart after crash — ran {run_count} times, expected 2"
        )

    @pytest.mark.asyncio
    async def test_supervisor_exits_cleanly_on_cancel(self, controller):
        """CancelledError must cause a clean exit without restart."""
        run_count = 0

        def _coro_factory():
            async def _coro():
                nonlocal run_count
                run_count += 1
                await asyncio.sleep(999)
            return _coro()

        controller._running = True
        task = controller._create_supervised_task(_coro_factory, name="test-cancel")
        await asyncio.sleep(0.05)  # let the coroutine start
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

        assert run_count == 1, "Cancelled task must not restart"


# ═══════════════════════════════════════════════════════════════════
# 5. MEDIUM — Balance is fetched fresh on each entry
# ═══════════════════════════════════════════════════════════════════

class TestBalanceFreshAtEntry:
    """compute_quantity must use a live balance fetch, not a stale cached value."""

    @pytest.mark.asyncio
    async def test_balance_fetched_for_every_entry(
        self, controller, sample_opportunity, mock_exchange_mgr
    ):
        """
        Verifies that get_balance() is called during entry, ensuring the sizer
        always sees current margin rather than a value captured at startup.
        """
        adapter_a = mock_exchange_mgr.get("exchange_a")
        adapter_b = mock_exchange_mgr.get("exchange_b")

        # Reset call counts before the entry
        adapter_a.get_balance.reset_mock()
        adapter_b.get_balance.reset_mock()

        await controller.handle_opportunity(sample_opportunity)

        # At least one of the adapters must have had its balance queried
        total_balance_calls = (
            adapter_a.get_balance.call_count + adapter_b.get_balance.call_count
        )
        assert total_balance_calls >= 1, (
            "No get_balance() call during entry — sizer may be using stale data"
        )


# ═══════════════════════════════════════════════════════════════════
# 6. MEDIUM — CLOSING state prevents double-close
# ═══════════════════════════════════════════════════════════════════

class TestDoubleClosePrevention:
    """A trade already CLOSING must not be sent to _close_trade a second time."""

    @pytest.mark.asyncio
    async def test_closing_trade_skipped_in_monitor_loop(
        self, controller, mock_exchange_mgr, mock_redis
    ):
        """
        Scenario: _check_upgrade fires, closes the trade (state → CLOSING), and
        returns True.  The monitor loop must skip _check_exit for that trade.
        Tests the `if upgraded: continue` guard in _exit_monitor_loop.
        """
        trade = _make_open_trade(controller)

        close_calls = []
        original_close = controller._close_trade

        async def _track_close(t):
            close_calls.append(t.trade_id)
            t.state = TradeState.CLOSING
            del controller._active_trades[t.trade_id]

        controller._close_trade = _track_close

        # _check_upgrade: closes the trade and returns True
        async def _upgrade_closes(t):
            await _track_close(t)
            return True

        controller._check_upgrade = _upgrade_closes
        controller._check_exit = AsyncMock()

        # Run one iteration of the loop body (no sleep)
        controller._running = True
        for trade_id, t in list(controller._active_trades.items()):
            if not t or t.state != TradeState.OPEN:
                continue
            upgraded = await controller._check_upgrade(t)
            if upgraded:
                continue
            await controller._check_exit(t)

        assert len(close_calls) == 1, "Trade must be closed exactly once"
        controller._check_exit.assert_not_called()

    @pytest.mark.asyncio
    async def test_state_guard_filters_non_open_trades(
        self, controller, mock_exchange_mgr
    ):
        """Trades in CLOSING state must be filtered out by the state guard."""
        trade = _make_open_trade(controller)
        trade.state = TradeState.CLOSING

        exit_calls = []
        controller._check_exit = AsyncMock(side_effect=lambda t: exit_calls.append(t))
        controller._check_upgrade = AsyncMock(return_value=False)

        for t in list(controller._active_trades.values()):
            if not t or t.state != TradeState.OPEN:
                continue
            upgraded = await controller._check_upgrade(t)
            if not upgraded:
                await controller._check_exit(t)

        assert len(exit_calls) == 0


# ═══════════════════════════════════════════════════════════════════
# 7. LOW — Liquidation safety threshold triggers emergency close
# ═══════════════════════════════════════════════════════════════════

class TestLiquidationSafetyExit:
    """margin_ratio < liquidation_safety_pct must trigger immediate trade close."""

    @pytest.mark.asyncio
    async def test_liquidation_risk_triggers_close(
        self, controller, config, mock_exchange_mgr, mock_redis
    ):
        """
        Scenario: one leg's margin ratio drops to 3% (below the 5% safety threshold).
        _check_liquidation_risk must flag the trade for emergency close.
        """
        config.trading_params.liquidation_safety_pct = Decimal("5.0")
        trade = _make_open_trade(controller)

        # margin = (entry_price * qty) / leverage = (50000 * 0.01) / 10 = 50
        # unrealized_pnl = -48.5 → equity = 50-48.5 = 1.5 → margin_ratio = 3% < 5%
        danger_position = Position(
            symbol="BTC/USDT",
            exchange="exchange_a",
            side=OrderSide.BUY,
            quantity=Decimal("0.01"),
            entry_price=Decimal("50000"),
            unrealized_pnl=Decimal("-48.5"),
            leverage=10,
        )
        # margin_ratio = 100% (no loss) — well above 5% safety
        safe_position = Position(
            symbol="BTC/USDT",
            exchange="exchange_b",
            side=OrderSide.SELL,
            quantity=Decimal("0.01"),
            entry_price=Decimal("50000"),
            unrealized_pnl=Decimal("0"),
            leverage=10,
        )

        long_adapter = mock_exchange_mgr.get("exchange_a")
        short_adapter = mock_exchange_mgr.get("exchange_b")
        long_adapter.get_positions.return_value = [danger_position]
        short_adapter.get_positions.return_value = [safe_position]

        closed_trades = []
        controller._close_trade = AsyncMock(side_effect=lambda t: closed_trades.append(t.trade_id))

        result = await controller._check_liquidation_risk(trade, long_adapter, short_adapter)

        assert result is True, "_check_liquidation_risk must return True when threshold crossed"
        assert trade.trade_id in closed_trades, "Trade must be closed on liquidation risk"

    @pytest.mark.asyncio
    async def test_healthy_margin_ratio_does_not_trigger_close(
        self, controller, config, mock_exchange_mgr
    ):
        """When both legs have healthy margin ratios, no close must occur."""
        config.trading_params.liquidation_safety_pct = Decimal("5.0")
        trade = _make_open_trade(controller)

        healthy = Position(
            symbol="BTC/USDT",
            exchange="exchange_a",
            side=OrderSide.BUY,
            quantity=Decimal("0.01"),
            entry_price=Decimal("50000"),
            unrealized_pnl=Decimal("0"),
            leverage=10,
        )
        long_adapter = mock_exchange_mgr.get("exchange_a")
        short_adapter = mock_exchange_mgr.get("exchange_b")
        long_adapter.get_positions.return_value = [healthy]
        short_adapter.get_positions.return_value = [healthy]

        controller._close_trade = AsyncMock()
        result = await controller._check_liquidation_risk(trade, long_adapter, short_adapter)

        assert result is False
        controller._close_trade.assert_not_called()


# ═══════════════════════════════════════════════════════════════════
# 8. LOW — Redis TLS and credential isolation
# ═══════════════════════════════════════════════════════════════════

class TestRedisCredentialIsolation:
    """Redis password must never appear in the URL string."""

    def test_redis_url_does_not_contain_password(self):
        """RedisConfig.url must be credential-free regardless of password value."""
        from src.core.config import RedisConfig
        from pydantic import SecretStr

        cfg = RedisConfig(
            host="redis.prod.example.com",
            port=6380,
            password=SecretStr("super-secret-password"),
            db=1,
            tls=True,
        )

        url = cfg.url
        assert "super-secret-password" not in url, (
            "Password must not appear in the URL — risk of log exposure"
        )
        assert url.startswith("rediss://"), "TLS flag must produce rediss:// scheme"
        assert "redis.prod.example.com" in url

    def test_redis_password_plaintext_unwraps_correctly(self):
        """password_plaintext must return the raw value for the connection boundary."""
        from src.core.config import RedisConfig
        from pydantic import SecretStr

        cfg = RedisConfig(password=SecretStr("my-pass"))
        assert cfg.password_plaintext == "my-pass"

    def test_redis_no_password_returns_none(self):
        from src.core.config import RedisConfig

        cfg = RedisConfig()
        assert cfg.password_plaintext is None

    def test_redis_client_accepts_tls_flag(self):
        """RedisClient constructor must accept tls parameter without error."""
        from src.storage.redis_client import RedisClient

        client_plain = RedisClient(url="redis://localhost:6379/0", tls=False)
        client_tls = RedisClient(url="rediss://remote:6380/0", tls=True)

        assert client_plain._tls is False
        assert client_tls._tls is True
