"""
One-shot script: split controller.py into 4 mixin files + thin orchestrator.

Run once from the project root:
    python scripts/split_controller.py

The resulting file layout:
    src/execution/
        _entry_mixin.py     handle_opportunity (lines 119-591)
        _monitor_mixin.py   _exit_monitor_loop / _check_upgrade / _check_exit / _reconcile_positions
        _close_mixin.py     _close_trade / _close_leg / _record_manual_close / close_all_positions
        _util_mixin.py      _place_with_timeout / _close_orphan / _persist_trade /
                            _recover_trades / _log_exchange_balances / _journal_balance_snapshot
        controller.py       __init__ / start / stop  +  inherits from all 4 mixins

Safe: ExecutionController's public API is unchanged.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CTRL = ROOT / "src" / "execution" / "controller.py"

lines = CTRL.read_text(encoding="utf-8").splitlines(keepends=True)

# ── Line ranges (1-based, inclusive on both ends) ────────────────
# These are zero-based indices into `lines` after the conversion below.
ENTRY_START   = 119 - 1   # async def handle_opportunity
ENTRY_END     = 591 - 1

MONITOR_START = 592 - 1   # async def _exit_monitor_loop
MONITOR_END   = 1465 - 1

CLOSE_START   = 1466 - 1  # async def _close_trade
CLOSE_END     = 2000 - 1

UTIL_START    = 2001 - 1  # async def _place_with_timeout
UTIL_END      = len(lines) - 1  # end of file

# ── Shared header (imports + module boilerplate) ─────────────────
HEADER = textwrap.dedent("""\
    \"\"\"
    Execution controller mixin — methods extracted from controller.py.
    Do NOT import this module directly; use ExecutionController from controller.py.
    \"\"\"
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

    logger = get_logger("execution")

    _ORDER_TIMEOUT_SEC = 10

""")


def _extract(start: int, end: int) -> str:
    return "".join(lines[start : end + 1])


def _write_mixin(path: Path, class_name: str, body_lines: str) -> None:
    content = HEADER + f"class {class_name}:\n" + body_lines
    path.write_text(content, encoding="utf-8")
    print(f"Wrote {path.relative_to(ROOT)}")


SRC_EXEC = ROOT / "src" / "execution"

_write_mixin(SRC_EXEC / "_entry_mixin.py",   "_EntryMixin",   _extract(ENTRY_START,   ENTRY_END))
_write_mixin(SRC_EXEC / "_monitor_mixin.py", "_MonitorMixin", _extract(MONITOR_START, MONITOR_END))
_write_mixin(SRC_EXEC / "_close_mixin.py",   "_CloseMixin",   _extract(CLOSE_START,   CLOSE_END))
_write_mixin(SRC_EXEC / "_util_mixin.py",    "_UtilMixin",    _extract(UTIL_START,    UTIL_END))

# ── Rewrite controller.py ─────────────────────────────────────────
ctrl_header = "".join(lines[: ENTRY_START])  # everything before handle_opportunity

# Find the class definition line inside the header and inject inheritance
new_class_def = "class ExecutionController(_EntryMixin, _MonitorMixin, _CloseMixin, _UtilMixin):\n"
ctrl_header = ctrl_header.replace("class ExecutionController:\n", new_class_def)

# Add mixin imports right after the TYPE_CHECKING block closing
mixin_imports = (
    "\nfrom src.execution._entry_mixin import _EntryMixin\n"
    "from src.execution._monitor_mixin import _MonitorMixin\n"
    "from src.execution._close_mixin import _CloseMixin\n"
    "from src.execution._util_mixin import _UtilMixin\n"
)
# Insert before "logger = get_logger"
ctrl_header = ctrl_header.replace(
    '\nlogger = get_logger("execution")',
    mixin_imports + '\nlogger = get_logger("execution")',
)

CTRL.write_text(ctrl_header, encoding="utf-8")
print(f"Rewrote {CTRL.relative_to(ROOT)}")
print("Done. Run pytest to verify.")
