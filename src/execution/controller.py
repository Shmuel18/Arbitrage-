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
from src.execution._util_mixin import _UtilMixin

logger = get_logger("execution")


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

class ExecutionController(_EntryMixin, _MonitorMixin, _CloseMixin, _UtilMixin):
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
        # Trade journal for persistent audit trail
        self._journal = get_journal()

    # ── Lifecycle ────────────────────────────────────────────────

    async def start(self) -> None:
        self._running = True
        await self._recover_trades()
        self._monitor_task = asyncio.create_task(
            self._exit_monitor_loop(), name="exit-monitor",
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

