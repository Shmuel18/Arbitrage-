"""
Unified exchange adapter — one concrete class wrapping ccxt.pro.

No abstract base, no empty subclasses. All exchanges go through here.

Implementation is split into four mixins for maintainability:
  _LifecycleMixin  — connect, disconnect, credential verification, clock sync
  _FundingMixin    — funding rate watchers, polling, cache, interval detection
  _MarketDataMixin — instruments, tickers, balances, positions, warm-up
  _OrderMixin      — order placement, fill verification, trading settings
"""

from __future__ import annotations

import asyncio
import time as _time
from decimal import Decimal
from typing import Any, Dict, List, Optional

import ccxt.pro as ccxtpro

from src.core.contracts import InstrumentSpec, OrderRequest, OrderSide, Position
from src.core.logging import get_logger
from src.exchanges._funding_mixin import _FundingMixin
from src.exchanges._lifecycle_mixin import _LifecycleMixin
from src.exchanges._market_data_mixin import _MarketDataMixin
from src.exchanges._order_mixin import _OrderMixin

logger = get_logger("exchanges")


class ExchangeAdapter(
    _LifecycleMixin,
    _FundingMixin,
    _MarketDataMixin,
    _OrderMixin,
):
    """Thin async wrapper around a single ccxt.pro exchange."""

    # Re-sync exchange clock offset every 5 minutes to prevent "timestamp ahead" errors
    _CLOCK_RESYNC_INTERVAL = 300

    # Reload markets (fees, contract specs) every 4 hours to pick up tier/fee changes
    _MARKETS_RELOAD_INTERVAL = 4 * 3600

    def __init__(self, exchange_id: str, cfg: dict):
        self.exchange_id = exchange_id
        self._cfg = cfg
        self._exchange: Optional[ccxtpro.Exchange] = None
        self._instrument_cache: Dict[str, InstrumentSpec] = {}
        self._settings_applied: set = set()
        self._funding_rate_cache: Dict[str, dict] = {}  # symbol → {rate, timestamp, ...}
        # (timestamp_sec, balance_dict) — populated by get_balance(), read by
        # get_balance_cached() in the entry hot-path to skip REST round-trips.
        self._balance_cache: Optional[tuple] = None
        self._price_cache: Dict[str, float] = {}  # symbol → last/mark price (fallback when funding data lacks markPrice)
        self._price_timestamp_cache: Dict[str, float] = {}  # symbol → last cached mark/last price timestamp (epoch-ms)
        self._ask_cache: Dict[str, float] = {}  # symbol → best ask price (from ticker poll, cached every 15s)
        self._ask_timestamp_cache: Dict[str, float] = {}  # symbol → best ask timestamp (epoch-ms)
        self._bid_cache: Dict[str, float] = {}  # symbol → best bid price (from ticker poll, cached every 15s)
        self._bid_timestamp_cache: Dict[str, float] = {}  # symbol → best bid timestamp (epoch-ms)
        # Symbol mapping: normalized (USDT) → original exchange symbol (e.g. USD for Kraken)
        self._symbol_map: Dict[str, str] = {}
        self._ws_tasks: List = []  # Track running WebSocket tasks
        self._rest_semaphore = asyncio.Semaphore(10)  # Limit concurrent REST calls per exchange
        self._ws_funding_supported = True
        self._ws_funding_disabled_logged = False
        self._ws_ticker_supported = True
        self._ws_ticker_disabled_logged = False
        self._ws_error_last_logged: Dict[str, float] = {}  # error_key → last log time (epoch-s)
        self._transient_log_last_logged: Dict[str, float] = {}  # log_key → last log time (monotonic-s)
        self._batch_funding_supported = True  # set to False if fetchFundingRates fails
        self._funding_intervals: Dict[str, int] = {}  # symbol → interval hours (from exchange API)
        # Candidate tracking for interval change confirmation (avoids false changes from
        # CCXT computing interval = (next_funding_ts - now) / 3600 near payment times)
        self._interval_change_candidates: Dict[str, tuple] = {}  # symbol → (candidate_hours, count)
        # Cached symbol list populated in connect(); avoids list() copy on every .symbols access
        self._symbols_list: Optional[List[str]] = None
        self._MAX_SANE_RATE = Decimal(str(cfg.get("max_sane_funding_rate", self._DEFAULT_MAX_SANE_RATE)))
        self._last_clock_sync: float = 0.0  # epoch timestamp of last clock sync
        self._last_markets_reload: float = 0.0  # epoch timestamp of last load_markets
        # Optional queue shared with Scanner — receives (exchange_id, symbol) on each price update.
        # Register via register_price_update_queue(); set before starting price watchers.
        self._price_update_queue: Optional[asyncio.Queue] = None

    def register_price_update_queue(self, queue: "asyncio.Queue[tuple[str, str]]") -> None:
        """Attach a hot-scan queue so fresh ticker updates trigger immediate re-evaluation."""
        self._price_update_queue = queue

    def _should_log_transient_error(self, key: str, interval_seconds: float) -> bool:
        """Rate-limit repetitive transient warnings per adapter instance."""
        now = _time.monotonic()
        last = self._transient_log_last_logged.get(key, 0.0)
        if now - last < interval_seconds:
            return False
        self._transient_log_last_logged[key] = now
        return True


# ── Manager ──────────────────────────────────────────────────────

class ExchangeManager:
    """Registry of exchange adapters, keyed by exchange id."""

    def __init__(self) -> None:
        self._adapters: Dict[str, ExchangeAdapter] = {}

    def register(self, exchange_id: str, cfg: dict) -> ExchangeAdapter:
        adapter = ExchangeAdapter(exchange_id, cfg)
        self._adapters[exchange_id] = adapter
        return adapter

    def get(self, exchange_id: str) -> ExchangeAdapter:
        return self._adapters[exchange_id]

    def all(self) -> Dict[str, ExchangeAdapter]:
        """Return a shallow copy of the adapters dict.

        Returning a copy (not a live proxy) makes it safe to iterate
        across ``await`` boundaries — concurrent calls to ``verify_all``
        or ``disconnect_all`` that mutate ``self._adapters`` cannot cause
        a ``RuntimeError: dictionary changed size during iteration``.
        """
        return dict(self._adapters)

    async def connect_all(self) -> None:
        adapters = list(self._adapters.values())
        results = await asyncio.gather(
            *[a.connect() for a in adapters],
            return_exceptions=True,
        )
        for adapter, result in zip(adapters, results):
            if isinstance(result, Exception):
                logger.error(
                    f"Failed to connect {adapter.exchange_id}: {result}",
                    extra={"exchange": adapter.exchange_id},
                )

    async def verify_all(self) -> list[str]:
        """Verify credentials on every adapter; remove & disconnect failures.

        Returns list of exchange ids that passed.
        """
        adapters = list(self._adapters.items())
        results = await asyncio.gather(
            *[adapter.verify_credentials() for _, adapter in adapters],
            return_exceptions=True,
        )
        failed: list[str] = []
        for (eid, adapter), ok in zip(adapters, results):
            if isinstance(ok, Exception) or not ok:
                failed.append(eid)
                await adapter.disconnect()
                del self._adapters[eid]
                if isinstance(ok, Exception):
                    logger.error(
                        f"Exchange {eid} removed — credential verification raised an exception",
                        exc_info=ok,
                        extra={"exchange": eid, "action": "exchange_removed"},
                    )
                else:
                    logger.error(
                        f"Exchange {eid} removed — invalid or rejected API credentials. "
                        "Check API key, secret, and IP whitelist.",
                        extra={"exchange": eid, "action": "exchange_removed"},
                    )
        return list(self._adapters.keys())

    async def disconnect_all(self) -> None:
        adapters = list(self._adapters.values())
        results = await asyncio.gather(
            *[a.disconnect() for a in adapters],
            return_exceptions=True,
        )
        for adapter, result in zip(adapters, results):
            if isinstance(result, Exception):
                logger.debug(f"Disconnect failed for {adapter.exchange_id}: {result}")
