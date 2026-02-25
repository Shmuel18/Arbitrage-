"""
Blacklist manager — track symbols/exchanges that should be skipped.

Extracted from ExecutionController so the blacklist logic is isolated,
independently testable, and not buried in a 2000-line file.
"""

from __future__ import annotations

import time
from typing import Dict

from src.core.logging import get_logger

logger = get_logger("blacklist")

_DEFAULT_DURATION_SEC = 6 * 3600  # 6 hours


class BlacklistManager:
    """In-memory blacklist keyed by ``"symbol:exchange"``.

    An entry expires automatically after ``duration_sec`` (default 6 h).
    Thread-safety is *not* required because the bot is single-threaded async.
    """

    def __init__(self, duration_sec: int = _DEFAULT_DURATION_SEC) -> None:
        self._duration_sec = duration_sec
        self._entries: Dict[str, float] = {}  # key → expiry timestamp

    def add(self, symbol: str, exchange: str, duration_sec: int | None = None) -> None:
        """Blacklist ``symbol`` on ``exchange`` for ``duration_sec`` seconds."""
        duration = duration_sec if duration_sec is not None else self._duration_sec
        key = f"{symbol}:{exchange}"
        self._entries[key] = time.time() + duration
        logger.warning(
            f"⛔ Blacklisted {symbol} on {exchange} for {duration // 3600}h",
            extra={"symbol": symbol, "exchange": exchange, "action": "blacklisted"},
        )

    def is_blacklisted(self, symbol: str, long_ex: str, short_ex: str) -> bool:
        """Return True if *either* exchange is blacklisted for this symbol."""
        self._evict_expired()
        for exchange in (long_ex, short_ex):
            key = f"{symbol}:{exchange}"
            if key in self._entries:
                remaining = int((self._entries[key] - time.time()) / 60)
                logger.debug(
                    f"Skipping {symbol}: {exchange} is blacklisted ({remaining}min left)"
                )
                return True
        return False

    def _evict_expired(self) -> None:
        """Remove entries whose TTL has elapsed (called on every lookup)."""
        now = time.time()
        expired = [k for k, v in self._entries.items() if v < now]
        for key in expired:
            del self._entries[key]
            sym, ex = key.rsplit(":", 1)
            logger.info(f"✅ Blacklist expired for {sym} on {ex}")
