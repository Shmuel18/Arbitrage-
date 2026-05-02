"""
Execution controller — open, monitor, and close funding-arb trades.

Safety features retained from review:
  • partial-fill detection (use actual filled qty, not requested)
  • order timeout with auto-cancel
  • both-exchange exit monitoring (checks funding on BOTH legs)
  • reduceOnly on every close
  • Redis persistence of active trades (crash recovery)
  • orphan detection and alerting
  • cooldown after orphan
  • supervised exit-monitor loop (auto-restarts with exponential backoff on crash)
"""

from __future__ import annotations

import asyncio
import time as _time
import json
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Dict, List, Optional, Protocol, runtime_checkable

from src.core.contracts import (
    ExitReason,
    OpportunityCandidate,
    OrderRequest,
    OrderSide,
    Position,
    TradeMode,
    TradeRecord,
    TradeState,
)
from src.core.reconciliation import BalanceSnapshot
from src.core.logging import get_logger
from src.core.journal import get_journal
from src.discovery.calculator import calculate_fees
from src.execution.blacklist import BlacklistManager
from src.execution.sizer import PositionSizer
from src.execution import helpers as _h

if TYPE_CHECKING:
    from src.core.config import Config
    from src.exchanges.adapter import ExchangeManager
    from src.storage.redis_client import RedisClient
    from src.risk.guard import RiskGuard
    from src.api.publisher import APIPublisher

from src.execution._entry_mixin import _EntryMixin
from src.execution._monitor_mixin import _MonitorMixin
from src.execution._close_mixin import _CloseMixin
from src.execution._reconcile_mixin import _ReconcileMixin
from src.execution._util_mixin import _UtilMixin

logger = get_logger("execution")


def _task_done_handler(t: asyncio.Task) -> None:
    """Log exceptions from background tasks — never let them vanish silently."""
    if t.cancelled():
        return
    exc = t.exception()
    if exc:
        logger.error(
            f"Task {t.get_name()} failed: {exc}",
            exc_info=exc,
            extra={"action": "task_failed", "task_name": t.get_name()},
        )


# ── Structural type that every mixin expects ``self`` to satisfy ──
# This gives type-checkers a way to validate cross-mixin attribute access
# without circular imports.  The concrete class (*ExecutionController*)
# satisfies this protocol via composition (duck-typing).

@runtime_checkable
class ControllerProtocol(Protocol):
    """Contract that all execution mixins rely on."""

    # ── Instance data ──
    _cfg: "Config"
    _exchanges: "ExchangeManager"
    _redis: "RedisClient"
    _risk_guard: Optional["RiskGuard"]
    _publisher: Optional["APIPublisher"]
    _active_trades: Dict[str, TradeRecord]
    _active_symbols: set[str]
    _busy_exchanges: set[str]
    _exchanges_entering: set[str]
    _symbol_refcount: Dict[str, int]
    _exchange_refcount: Dict[str, int]
    _symbols_entering: set[str]
    _upgrade_cooldown: Dict[str, float]
    _blacklist: BlacklistManager
    _sizer: PositionSizer
    _timeout_streak: Dict[str, int]
    _running: bool

    # ── Cross-mixin methods ──
    async def _place_with_timeout(self, adapter: object, req: OrderRequest) -> Optional[dict]: ...
    async def _close_orphan(
        self, adapter: object, exchange: str, symbol: str,
        side: OrderSide, fill: dict, fallback_qty: Optional[Decimal] = None,
    ) -> None: ...
    def _register_trade(self, trade: TradeRecord) -> None: ...
    def _deregister_trade(self, trade: TradeRecord) -> None: ...
    async def _persist_trade(self, trade: TradeRecord) -> None: ...
    async def _close_trade(self, trade: TradeRecord) -> None: ...
    async def _record_manual_close(self, trade: TradeRecord) -> None: ...
    async def _log_exchange_balances(self) -> None: ...
    async def _capture_pre_snapshot(self, trade_id: str) -> None: ...
    async def _record_reconciliation(
        self, trade: TradeRecord, expected_pnl: Decimal,
    ) -> None: ...

class ExecutionController(
    _EntryMixin, _MonitorMixin, _CloseMixin, _ReconcileMixin, _UtilMixin,
):
    # ── Cross-mixin constants (shared by _UtilMixin / _CloseMixin) ──
    _TIMEOUT_COOLDOWN_SEC = 600          # 10 min cooldown after first order timeout
    _TIMEOUT_BLACKLIST_THRESHOLD = 2     # blacklist after N consecutive timeouts

    def __init__(
        self,
        config: "Config",
        exchange_mgr: "ExchangeManager",
        redis: "RedisClient",
        risk_guard: Optional["RiskGuard"] = None,
        publisher: Optional["APIPublisher"] = None,
    ):
        self._cfg = config
        self._exchanges = exchange_mgr
        self._redis = redis
        self._risk_guard = risk_guard
        self._publisher = publisher
        self._active_trades: Dict[str, TradeRecord] = {}
        # O(1) derived sets — kept in sync by _register_trade / _deregister_trade
        self._active_symbols: set[str] = set()
        self._busy_exchanges: set[str] = set()
        self._exchanges_entering: set[str] = set()
        # Refcount maps: how many open trades hold a symbol / exchange slot.
        # Allows O(1) deregistration without scanning _active_trades.
        self._symbol_refcount: Dict[str, int] = {}
        self._exchange_refcount: Dict[str, int] = {}
        self._running = False
        self._monitor_task: Optional[asyncio.Task] = None
        # Blacklist: skips symbols/exchanges that are delisting or repeatedly failing
        self._blacklist = BlacklistManager()
        # Position sizer: computes quantity from balances and config
        self._sizer = PositionSizer(config)
        # Track consecutive order-timeout failures: "symbol:exchange" -> count
        self._timeout_streak: Dict[str, int] = {}
        # In-memory guard: symbols currently mid-entry (prevents TOCTOU before Redis lock)
        self._symbols_entering: set[str] = set()
        # Upgrade cooldown: symbol -> expiry timestamp (prevents re-entry after upgrade exit)
        self._upgrade_cooldown: Dict[str, float] = {}
        # Pre-entry balance snapshots — keyed by trade_id, popped on close.
        # Best-effort: a missing entry just flags the reconciliation as partial.
        self._pending_pre_snapshots: Dict[str, BalanceSnapshot] = {}
        # Trade journal for persistent audit trail
        self._journal = get_journal()

        # ── Runtime MRO / protocol guard ────────────────────────────────────
        # Catches the case where a new method is added to ControllerProtocol
        # but not implemented by the concrete class or one of its mixins.
        # `runtime_checkable` validates method presence only (not attributes).
        assert isinstance(self, ControllerProtocol), (  # noqa: S101
            f"{type(self).__name__} does not fully satisfy ControllerProtocol — "
            "a method declared in the protocol is missing from the MRO."
        )

    # ── Lifecycle ────────────────────────────────────────────────

    def _create_supervised_task(
        self, coro_factory, *, name: str = "supervised"
    ) -> asyncio.Task:
        """Create a background task that auto-restarts on unexpected failure.

        *coro_factory* is a zero-arg callable that returns a fresh coroutine
        each restart (e.g. ``lambda: self._exit_monitor_loop()``).
        CancelledError exits cleanly. All other exceptions trigger a delayed
        restart with exponential back-off (capped at 60 s).
        """
        async def _supervisor() -> None:
            backoff = 5
            while True:
                try:
                    await coro_factory()
                    return  # coroutine exited normally (self._running=False path)
                except asyncio.CancelledError:
                    return
                except Exception as exc:
                    logger.error(
                        f"Supervised task '{name}' crashed — restarting in {backoff}s",
                        exc_info=exc,
                        extra={"action": "task_restart", "task_name": name},
                    )
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 60)

        task = asyncio.create_task(_supervisor(), name=name)
        task.add_done_callback(_task_done_handler)
        return task

    async def start(self) -> None:
        self._running = True
        await self._recover_trades()
        # Supervised: if the loop crashes unexpectedly it restarts automatically
        # with exponential back-off (5s → 10s → … → 60s cap) so open trades
        # are never left unmonitored.
        self._monitor_task = self._create_supervised_task(
            lambda: self._exit_monitor_loop(), name="exit-monitor",
        )

        # Log balances on startup (if enabled in config)
        if self._cfg.logging.log_balances_on_startup:
            await self._log_exchange_balances()

        logger.info("Execution controller started")

    async def stop(self) -> None:
        self._running = False
        if self._monitor_task:
            self._monitor_task.cancel()
            await asyncio.gather(self._monitor_task, return_exceptions=True)
        logger.info("Execution controller stopped")

    # ── Open trade ───────────────────────────────────────────────

