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
from typing import TYPE_CHECKING, Dict, List, Optional

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

# Single source of truth — imported by all mixin modules via controller.py
_ORDER_TIMEOUT_SEC = 10


class ExecutionController(_EntryMixin, _MonitorMixin, _CloseMixin, _UtilMixin):
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

        # ── Sanity check: hold_min_spread should not exceed min_funding_spread ──
        # If it does, trades near the entry threshold exit after one payment rather
        # than holding, which is likely unintentional.
        _min_entry = self._cfg.trading_params.min_funding_spread
        _min_hold = self._cfg.trading_params.hold_min_spread
        if _min_hold > _min_entry:
            logger.warning(
                f"[CONFIG WARNING] hold_min_spread ({_min_hold}%) > min_funding_spread ({_min_entry}%). "
                f"Trades that enter near the threshold will always exit after one payment. "
                f"Set hold_min_spread <= min_funding_spread to allow multi-cycle holding."
            )

        logger.info("Execution controller started")

    async def stop(self) -> None:
        self._running = False
        if self._monitor_task:
            self._monitor_task.cancel()
            await asyncio.gather(self._monitor_task, return_exceptions=True)
        logger.info("Execution controller stopped")

    # ── Open trade ───────────────────────────────────────────────

