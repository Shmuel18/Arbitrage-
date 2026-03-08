"""
Unified exchange adapter — one concrete class wrapping ccxt.pro.

No abstract base, no empty subclasses. All exchanges go through here.
"""

from __future__ import annotations

import asyncio
import logging
import time as _time
from datetime import datetime, timezone
from decimal import Decimal
from types import MappingProxyType
from typing import Any, Dict, List, Mapping, Optional

import ccxt.pro as ccxtpro

from src.core.contracts import InstrumentSpec, OrderRequest, OrderSide, Position
from src.core.logging import get_logger

logger = get_logger("exchanges")


class ExchangeAdapter:
    """Thin async wrapper around a single ccxt.pro exchange."""

    def __init__(self, exchange_id: str, cfg: dict):
        self.exchange_id = exchange_id
        self._cfg = cfg
        self._exchange: Optional[ccxtpro.Exchange] = None
        self._instrument_cache: Dict[str, InstrumentSpec] = {}
        self._settings_applied: set = set()
        self._funding_rate_cache: Dict[str, dict] = {}  # symbol → {rate, timestamp, ...}
        self._price_cache: Dict[str, float] = {}  # symbol → last/mark price (fallback when funding data lacks markPrice)
        # Symbol mapping: normalized (USDT) → original exchange symbol (e.g. USD for Kraken)
        self._symbol_map: Dict[str, str] = {}
        self._ws_tasks: List = []  # Track running WebSocket tasks
        self._rest_semaphore = asyncio.Semaphore(10)  # Limit concurrent REST calls per exchange
        self._ws_funding_supported = True
        self._ws_funding_disabled_logged = False
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

    # ── Supervised task (auto-restart on crash) ──────────────────

    def _create_supervised_task(self, coro_factory, *, name: str = "supervised"):
        """Create a background task that auto-restarts on unexpected failure.
        
        *coro_factory* is a zero-arg callable that returns a new coroutine
        each time (e.g. ``lambda: self._batch_funding_poll_loop(syms)``).
        CancelledError exits cleanly. All other exceptions trigger a delayed
        restart with exponential back-off (capped at 60 s).
        """
        async def _supervisor():
            backoff = 5
            while True:
                try:
                    await coro_factory()
                    return  # coroutine exited normally
                except asyncio.CancelledError:
                    return
                except Exception as exc:
                    logger.error(
                        f"[{self.exchange_id}] Supervised task '{name}' crashed: {exc}. "
                        f"Restarting in {backoff}s",
                        extra={"exchange": self.exchange_id, "action": "task_restart"},
                    )
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 60)

        task = asyncio.create_task(_supervisor(), name=f"{self.exchange_id}-{name}")
        self._ws_tasks.append(task)
        return task

    # ── Lifecycle ────────────────────────────────────────────────

    async def connect(self) -> None:
        cls = getattr(ccxtpro, self._cfg.get("ccxt_id", self.exchange_id))
        opts: Dict[str, Any] = {
            "apiKey": self._cfg.get("api_key"),
            "secret": self._cfg.get("api_secret"),
            "enableRateLimit": True,
            "options": {
                "defaultType": self._cfg.get("default_type", "swap"),
                # Mitigate timestamp/recv_window errors on Bybit and others
                "adjustForTimeDifference": True,
                "recvWindow": 10000,
            },
        }
        if pw := self._cfg.get("api_passphrase"):
            opts["password"] = pw
        if self._cfg.get("testnet"):
            opts["sandbox"] = True

        self._exchange = cls(opts)
        try:
            await self._exchange.load_markets()
        except Exception as e:
            # Some exchanges (e.g. Gate.io) may partially fail load_markets
            # but still populate the markets dict — continue if we got data
            if not self._exchange.markets:
                raise
            logger.warning(
                f"{self.exchange_id}: load_markets partial error ({e}), "
                f"continuing with {len(self._exchange.markets)} raw markets",
                extra={"exchange": self.exchange_id, "action": "load_markets_partial"},
            )

        # Sync clock offset against exchange server time to avoid timestamp errors
        try:
            if hasattr(self._exchange, "load_time_difference"):
                await self._exchange.load_time_difference()
                self._last_clock_sync = _time.time()
                logger.info(
                    f"{self.exchange_id}: clock offset synced "
                    f"(timeDifference={self._exchange.options.get('timeDifference', 0)}ms)",
                    extra={"exchange": self.exchange_id},
                )
        except Exception as e:
            logger.warning(
                f"{self.exchange_id}: could not sync clock offset: {e}",
                extra={"exchange": self.exchange_id},
            )

        # Filter to ACTIVE linear perpetuals settled in USDT or USD
        filtered = {
            k: v for k, v in self._exchange.markets.items()
            if v.get("swap") and v.get("linear")
            and v.get("settle") in ("USDT", "USD")
            and v.get("active") is not False  # exclude delisted/settling markets
        }

        # Normalize USD-settled symbols to USDT format for cross-exchange matching
        # e.g. Kraken "BTC/USD:USD" → "BTC/USDT:USDT"
        remapped: Dict[str, Any] = {}
        self._symbol_map = {}
        for orig_sym, mkt in filtered.items():
            if mkt.get("settle") == "USD":
                norm_sym = orig_sym.replace("/USD:USD", "/USDT:USDT")
                self._symbol_map[norm_sym] = orig_sym
                remapped[norm_sym] = mkt
                # Keep original key too so ccxt internal lookups work
                remapped[orig_sym] = mkt
            else:
                remapped[orig_sym] = mkt

        self._exchange.markets = remapped
        # Only expose normalized symbols (not the USD originals) to scanner
        normalized_symbols = [
            s for s in remapped
            if s not in self._symbol_map.values()  # exclude raw USD keys
        ]
        self._exchange.symbols = normalized_symbols
        # Cache right here so the `symbols` property never copies the list again.
        self._symbols_list = normalized_symbols

        # krakenfutures has ccxt bugs in parse_funding_rate:
        # 1) String comparison instead of numeric for clamping (positive rates → -0.25)
        # 2) Precise.string_div returns None → crashes batch fetch
        # Fix: monkey-patch parse_funding_rate with corrected logic
        # Also: batch fetch_funding_rates still fails because ccxt tries to parse
        # non-swap instruments (e.g. FI_XRPUSD_250131) → keep batch off
        if self._cfg.get("ccxt_id", self.exchange_id) == "krakenfutures":
            self._patch_kraken_funding_parser()
            self._batch_funding_supported = False

        # Binance: fetch /fapi/v1/fundingInfo for correct funding intervals
        # (ccxt doesn't expose this — many newer coins have 4h instead of 8h)
        if self.exchange_id == "binance":
            await self._fetch_binance_funding_intervals()

        if self._symbol_map:
            logger.info(
                f"{self.exchange_id}: remapped {len(self._symbol_map)} USD→USDT symbols",
                extra={"exchange": self.exchange_id, "action": "symbol_remap"},
            )

        logger.info(
            f"Connected to {self.exchange_id}",
            extra={"exchange": self.exchange_id,
                   "action": "connect",
                   "data": {"markets": len(filtered)}},
        )

    async def _fetch_binance_funding_intervals(self) -> None:
        """Fetch Binance /fapi/v1/fundingInfo to get correct funding intervals.

        Many newer Binance coins have 4h (or other) funding intervals,
        but ccxt doesn't expose this — the 'interval' field is always None
        and market info lacks 'fundingInterval'. This endpoint is the only
        reliable source.
        """
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    "https://fapi.binance.com/fapi/v1/fundingInfo",
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status != 200:
                        logger.warning(
                            f"Binance fundingInfo returned status {resp.status}",
                            extra={"exchange": "binance"},
                        )
                        return
                    data = await resp.json()

            non_default = 0
            for item in data:
                raw_sym = item.get("symbol", "")
                hours = item.get("fundingIntervalHours")
                if not hours:
                    continue
                hours = int(hours)
                # Map raw symbol (e.g. "MMTUSDT") to ccxt format ("MMT/USDT:USDT")
                # Try direct lookup in markets
                for ccxt_sym, mkt in self._exchange.markets.items():
                    if mkt.get("id") == raw_sym or mkt.get("info", {}).get("symbol") == raw_sym:
                        self._funding_intervals[ccxt_sym] = hours
                        if hours != 8:
                            non_default += 1
                        break

            logger.info(
                f"Binance fundingInfo: loaded {len(self._funding_intervals)} intervals "
                f"({non_default} non-8h)",
                extra={"exchange": "binance", "action": "funding_intervals_loaded"},
            )
        except Exception as e:
            logger.warning(
                f"Failed to fetch Binance fundingInfo: {e}",
                extra={"exchange": "binance"},
            )

    def _patch_kraken_funding_parser(self) -> None:
        """Monkey-patch krakenfutures.parse_funding_rate to fix ccxt bugs.
        
        ccxt bug: uses string comparison ('0.00001' > '-0.25' → True) instead of
        numeric for clamping, causing ALL positive rates to become -0.25.
        Also: Precise.string_div can return None, crashing batch fetch.
        """
        ex = self._exchange

        def _patched_parse_funding_rate(ticker, market=None):
            market_id = ex.safe_string(ticker, 'symbol')
            symbol = ex.symbol(market_id)
            timestamp = ex.parse8601(ex.safe_string(ticker, 'lastTime'))
            mark_price_str = ex.safe_string(ticker, 'markPrice')
            funding_rate_str = ex.safe_string(ticker, 'fundingRate')
            next_rate_str = ex.safe_string(ticker, 'fundingRatePrediction')

            # Compute rate = fundingRate / markPrice (safe numeric division)
            funding_rate = None
            next_funding_rate = None
            try:
                if funding_rate_str and mark_price_str:
                    fr = float(funding_rate_str) / float(mark_price_str)
                    funding_rate = max(-0.25, min(0.25, fr))
            except (ValueError, ZeroDivisionError):
                pass
            try:
                if next_rate_str and mark_price_str:
                    nfr = float(next_rate_str) / float(mark_price_str)
                    next_funding_rate = max(-0.25, min(0.25, nfr))
            except (ValueError, ZeroDivisionError):
                pass

            return {
                'info': ticker,
                'symbol': symbol,
                'markPrice': ex.parse_number(mark_price_str),
                'indexPrice': ex.safe_number(ticker, 'indexPrice'),
                'interestRate': None,
                'estimatedSettlePrice': None,
                'timestamp': timestamp,
                'datetime': ex.iso8601(timestamp),
                'fundingRate': funding_rate,
                'fundingTimestamp': None,
                'fundingDatetime': None,
                'nextFundingRate': next_funding_rate,
                'nextFundingTimestamp': None,
                'nextFundingDatetime': None,
                'previousFundingRate': None,
                'previousFundingTimestamp': None,
                'previousFundingDatetime': None,
                'interval': '1h',
            }

        ex.parse_funding_rate = _patched_parse_funding_rate
        logger.debug(
            f"Patched parse_funding_rate on {self.exchange_id}",
            extra={"exchange": self.exchange_id},
        )

    async def verify_credentials(self) -> bool:
        """Test an authenticated call. Returns False if keys are invalid."""
        try:
            await self._exchange.fetch_balance()
            return True
        except Exception as e:
            logger.warning(
                f"Credentials invalid for {self.exchange_id}: {e}",
                extra={"exchange": self.exchange_id, "action": "auth_fail"},
            )
            return False

    def _resolve_symbol(self, symbol: str) -> str:
        """Return the original exchange symbol for ccxt API calls.
        
        For most exchanges this is identity. For Kraken, maps e.g.
        'BTC/USDT:USDT' back to 'BTC/USD:USD'.
        """
        return self._symbol_map.get(symbol, symbol)

    def _normalize_symbol(self, orig_symbol: str) -> str:
        """Return normalized (USDT) symbol from an original exchange symbol.
        
        Reverse of _resolve_symbol. For batch API results from Kraken,
        maps 'BTC/USD:USD' back to 'BTC/USDT:USDT'.
        """
        # Build reverse map on first call
        if not hasattr(self, '_reverse_symbol_map'):
            self._reverse_symbol_map = {v: k for k, v in self._symbol_map.items()}
        return self._reverse_symbol_map.get(orig_symbol, orig_symbol)

    async def start_funding_rate_watchers(self, symbols: List[str]) -> None:
        """Start funding rate polling — batch if supported, per-symbol otherwise."""
        eligible = [s for s in symbols if s in self._exchange.symbols]
        if not eligible:
            logger.info(
                f"Starting funding rate polling for 0 symbols",
                extra={"exchange": self.exchange_id, "action": "ws_start"},
            )
            return

        if self._batch_funding_supported:
            logger.info(
                f"Starting funding rate BATCH polling for {len(eligible)} symbols",
                extra={"exchange": self.exchange_id, "action": "ws_start"},
            )
            self._create_supervised_task(
                lambda syms=eligible: self._batch_funding_poll_loop(syms),
                name="batch-funding-poll",
            )
        else:
            # Batch not supported (e.g. KuCoin) — single task, sequential with semaphore
            logger.info(
                f"Starting funding rate SEQUENTIAL polling for {len(eligible)} symbols",
                extra={"exchange": self.exchange_id, "action": "ws_start"},
            )
            self._create_supervised_task(
                lambda syms=eligible: self._sequential_funding_poll_loop(syms),
                name="sequential-funding-poll",
            )

        # Always start price poll loop — provides markPrice fallback for exchanges
        # that don't include markPrice in their funding rate API response (e.g. KuCoin)
        self._create_supervised_task(
            lambda syms=eligible: self._price_poll_loop(syms),
            name="price-poll",
        )

    async def _watch_funding_rate_loop(self, symbol: str) -> None:
        """Continuously watch funding rate for a symbol via WebSocket.

        Uses infinite retry with exponential backoff (capped at 60 s).
        Backoff resets after every successful data receive so transient
        errors don't accumulate towards a permanent shutdown.
        """
        backoff = 5          # initial wait after failure (seconds)
        max_backoff = 60     # cap
        consecutive_failures = 0

        while True:
            try:
                # Try WebSocket if available (ccxt.pro)
                if self._ws_funding_supported and hasattr(self._exchange, 'watch_funding_rate'):
                    await self._watch_funding_rate_websocket(symbol)
                else:
                    # Fallback: fast polling every 5 seconds
                    await self._watch_funding_rate_polling(symbol)
                # If the inner loop returns normally, reset backoff
                consecutive_failures = 0
                backoff = 5
            except asyncio.CancelledError:
                logger.debug(f"Funding watcher cancelled for {symbol}")
                return
            except Exception as e:
                consecutive_failures += 1
                msg = str(e).lower()
                if "not supported" in msg or "does not support" in msg:
                    self._ws_funding_supported = False
                    if not self._ws_funding_disabled_logged:
                        self._ws_funding_disabled_logged = True
                        logger.warning(
                            f"{self.exchange_id} watch_funding_rate() not supported — falling back to polling",
                            extra={"exchange": self.exchange_id, "action": "ws_funding_disabled"},
                        )
                    # Switch to polling (infinite loop inside); if it
                    # raises we'll come back to the outer while True.
                    continue

                # Escalate log level after repeated failures
                if consecutive_failures <= 3:
                    logger.warning(
                        f"Funding watcher error for {symbol}: {e}",
                        extra={"exchange": self.exchange_id, "symbol": symbol,
                               "retry": consecutive_failures},
                    )
                elif consecutive_failures % 10 == 0:
                    # Log every 10th failure at ERROR to avoid spam
                    logger.error(
                        f"Funding watcher for {symbol} has failed {consecutive_failures} times "
                        f"in a row — cached data may be STALE: {e}",
                        extra={"exchange": self.exchange_id, "symbol": symbol,
                               "retry": consecutive_failures},
                    )
                wait = min(backoff * (2 ** min(consecutive_failures - 1, 5)), max_backoff)
                await asyncio.sleep(wait)

    async def _watch_funding_rate_websocket(self, symbol: str) -> None:
        """Watch funding rate via WebSocket (ccxt.pro)."""
        while True:
            try:
                data = await self._exchange.watch_funding_rate(self._resolve_symbol(symbol))
                self._update_funding_cache(symbol, data)
                logger.debug(
                    f"[WS] Funding update for {symbol}: {data.get('fundingRate')}",
                    extra={"exchange": self.exchange_id, "symbol": symbol, "ws_rate": str(data.get('fundingRate'))},
                )
            except Exception as e:
                logger.debug(f"WebSocket funding error for {symbol}: {e}")
                raise  # Re-raise to trigger fallback/retry

    async def _watch_funding_rate_polling(self, symbol: str) -> None:
        """Fast polling fallback every 5 seconds."""
        while True:
            try:
                data = await self._exchange.fetch_funding_rate(self._resolve_symbol(symbol))
                self._update_funding_cache(symbol, data)
                await asyncio.sleep(5)  # Poll every 5 seconds instead of 30s scan
            except Exception as e:
                logger.debug(f"Funding poll error for {symbol}: {e}")
                await asyncio.sleep(5)

    # Maximum plausible absolute funding rate per interval — configurable via config.yaml
    _DEFAULT_MAX_SANE_RATE = Decimal("0.10")

    # Re-sync exchange clock offset every 5 minutes to prevent "timestamp ahead" errors
    _CLOCK_RESYNC_INTERVAL = 300

    # Reload markets (fees, contract specs) every 4 hours to pick up tier/fee changes
    _MARKETS_RELOAD_INTERVAL = 4 * 3600

    def _update_funding_cache(self, symbol: str, data: dict) -> None:
        """Update in-memory cache with latest funding rate."""
        rate = Decimal(str(data.get("fundingRate", 0)))
        
        # Raw ccxt data — guard f-string: called on every WebSocket tick
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                f"[{self.exchange_id}] Raw ccxt funding data for {symbol}: "
                f"fundingRate={data.get('fundingRate')}, mark={data.get('markPrice')}, "
                f"index={data.get('indexPrice')}, timestamp={data.get('timestamp')}, "
                f"fundingTimestamp={data.get('fundingTimestamp')}",
                extra={
                    "exchange": self.exchange_id,
                    "symbol": symbol,
                    "action": "ccxt_raw_funding",
                    "raw_rate": str(data.get("fundingRate")),
                    "interval_ms": data.get("fundingTimestamp"),
                },
            )

        # Sanity check: skip obviously broken rates (e.g. Kraken returning -0.25)
        if abs(rate) > self._MAX_SANE_RATE:
            logger.warning(
                f"[WARNING] Skipping insane funding rate {rate} for {symbol} on {self.exchange_id} "
                f"(exceeds {self._MAX_SANE_RATE})",
                extra={"exchange": self.exchange_id, "symbol": symbol},
            )
            return

        interval_hours = self._get_funding_interval(symbol, data)
        # Pick the EARLIEST future timestamp from both CCXT fields.
        # OKX: fundingTimestamp = upcoming payment, nextFundingTimestamp = the one after.
        # Bitget/others: fundingTimestamp = past payment, nextFundingTimestamp = upcoming.
        # By choosing the earliest future value, we handle both conventions correctly.
        _now_ms_pick = _time.time() * 1000
        _ts_a = data.get("nextFundingTimestamp")
        _ts_b = data.get("fundingTimestamp")
        _future_candidates = [t for t in (_ts_a, _ts_b) if t and t > _now_ms_pick]
        next_ts = min(_future_candidates) if _future_candidates else (_ts_a or _ts_b)

        now_ms = _now_ms_pick
        interval_ms = interval_hours * 3_600_000

        # If exchange doesn't provide next funding time, compute it from interval
        # (e.g. Kraken 1h funding → next full hour boundary)
        if not next_ts and interval_ms > 0:
            next_ts = (int(now_ms // interval_ms) + 1) * interval_ms

        # If next_timestamp is in the past, advance by interval until future
        if next_ts and interval_ms > 0:
            while next_ts <= now_ms:
                next_ts += interval_ms

        # Final safety: if next_ts is STILL in the past (e.g. interval_ms was 0
        # or no timestamp data at all), compute from epoch boundary so callers
        # never see a stale "NOW" indicator.
        if next_ts and next_ts <= now_ms and interval_ms > 0:
            next_ts = (int(now_ms // interval_ms) + 1) * interval_ms
        elif not next_ts and interval_ms > 0:
            next_ts = (int(now_ms // interval_ms) + 1) * interval_ms

        self._funding_rate_cache[symbol] = {
            "rate": rate,
            "timestamp": data.get("timestamp"),
            "datetime": data.get("datetime"),
            "next_timestamp": next_ts,
            "interval_hours": interval_hours,
            "markPrice": data.get("markPrice"),  # stored for price basis checks
            "indexPrice": data.get("indexPrice"),
        }
        
        # Guard f-string: called on every WebSocket tick
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                f"[{self.exchange_id}] Cached funding for {symbol}: "
                f"rate={rate:.8f} ({rate*100:.6f}%), interval={interval_hours}h, next_ts={next_ts}",
                extra={
                    "exchange": self.exchange_id,
                    "symbol": symbol,
                    "action": "funding_cached",
                    "cached_rate": str(rate),
                    "interval_hours": interval_hours,
                },
            )

    def get_mark_price(self, symbol: str) -> Optional[float]:
        """Return best available mark price for symbol (no API call).
        
        Cascade: markPrice from funding cache → indexPrice → price cache (from ticker poll).
        Returns None if no price is available yet.
        """
        cached = self._funding_rate_cache.get(symbol) or {}
        mp = cached.get("markPrice") or cached.get("indexPrice")
        if mp is not None:
            return float(mp)
        return self._price_cache.get(symbol)

    def get_funding_rate_cached(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Get latest cached funding rate (low-latency, no network call).

        If the cached ``next_timestamp`` has drifted into the past (funding
        already fired since the last WS/REST refresh), advance it by the
        funding interval so callers always see a future timestamp.
        """
        cached = self._funding_rate_cache.get(symbol)
        if cached:
            next_ts = cached.get("next_timestamp")
            interval_hours = cached.get("interval_hours")
            if next_ts and interval_hours:
                now_ms = _time.time() * 1000
                interval_ms = interval_hours * 3_600_000
                if next_ts <= now_ms:
                    while next_ts <= now_ms:
                        next_ts += interval_ms
                    cached["next_timestamp"] = next_ts
        # Guard f-string formatting: called ~1000×/scan, skip when not in DEBUG mode.
        if cached and logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                f"[{self.exchange_id}] Retrieved cached rate for {symbol}: "
                f"rate={cached['rate']:.8f} ({cached['rate']*100:.6f}%), "
                f"interval={cached.get('interval_hours')}h, age_ms={(_time.time()*1000 - (cached.get('timestamp') or 0)):.0f}",
                extra={
                    "exchange": self.exchange_id,
                    "symbol": symbol,
                    "action": "cache_retrieved",
                    "cached_rate": str(cached["rate"]),
                },
            )
        return cached

    # ── Public read-only views of internal exchange state ────────
    # Use these instead of accessing _exchange directly from outside.

    @property
    def symbols(self) -> List[str]:
        """Normalized symbol list available on this exchange (cached after connect)."""
        return self._symbols_list if self._symbols_list is not None else []

    @property
    def markets(self) -> Dict[str, Any]:
        """Market dict keyed by normalized symbol."""
        if self._exchange is None:
            return {}
        return dict(self._exchange.markets)

    def get_cached_instrument_spec(self, symbol: str) -> Optional[InstrumentSpec]:
        """Return in-memory cached InstrumentSpec without a network call."""
        return self._instrument_cache.get(symbol)

    async def warm_up_funding_rates(self, symbols: Optional[List[str]] = None) -> None:
        """Batch-fetch ALL funding rates in one API call to pre-populate cache.
        Falls back to per-symbol fetch if batch not supported (e.g. KuCoin, Kraken)."""
        # Ensure Binance funding intervals are loaded (fallback if connect() failed)
        if self.exchange_id == "binance" and not self._funding_intervals:
            await self._fetch_binance_funding_intervals()

        if not symbols:
            symbols = [s for s in self._exchange.symbols
                       if s in self._exchange.markets]

        # Try batch first (if supported)
        if self._batch_funding_supported:
            try:
                all_rates = await self._exchange.fetch_funding_rates()
                count = 0
                for sym_raw, data in all_rates.items():
                    symbol = self._normalize_symbol(sym_raw)
                    if symbol in self._exchange.symbols:
                        self._update_funding_cache(symbol, data)
                        count += 1
                logger.info(
                    f"[OK] Warmed up {count} funding rates on {self.exchange_id}",
                    extra={"exchange": self.exchange_id, "action": "funding_warm_up", "count": count},
                )
                return count
            except Exception as e:
                self._batch_funding_supported = False
                logger.warning(
                    f"Batch fetch not supported on {self.exchange_id}, using per-symbol warmup",
                    extra={"exchange": self.exchange_id, "action": "funding_warm_up_fallback"},
                )

        # Fallback: per-symbol fetch with concurrency limit
        sem = asyncio.Semaphore(20)
        count = 0

        async def _fetch_one(sym: str):
            nonlocal count
            async with sem:
                try:
                    data = await self._exchange.fetch_funding_rate(self._resolve_symbol(sym))
                    self._update_funding_cache(sym, data)
                    count += 1
                except Exception as exc:
                    logger.debug(f"[{self.exchange_id}] Funding warm-up failed for {sym}: {exc}")

        await asyncio.gather(*[_fetch_one(s) for s in symbols], return_exceptions=True)
        logger.info(
            f"Warmed up {count}/{len(symbols)} funding rates on {self.exchange_id} (per-symbol)",
            extra={"exchange": self.exchange_id, "action": "funding_warm_up"},
        )
        return count

    async def _batch_funding_poll_loop(self, symbols: List[str]) -> None:
        """Periodically fetch ALL funding rates in one batch API call.
        Also refreshes Binance funding intervals every 30 minutes."""
        poll_interval = 15  # seconds between batch refreshes (single API call, safe for all batch exchanges)
        interval_refresh_every = 1800  # re-fetch funding intervals every 30 min
        consecutive_failures = 0
        last_interval_refresh = _time.time()
        while True:
            try:
                # ── Periodically refresh Binance fundingInfo (intervals can change) ──
                if (self.exchange_id == "binance"
                        and _time.time() - last_interval_refresh >= interval_refresh_every):
                    await self._fetch_binance_funding_intervals()
                    last_interval_refresh = _time.time()

                # Fetch without symbol filter — avoids OKX "must be same type" error
                all_rates = await self._exchange.fetch_funding_rates()
                count = 0
                for sym_raw, data in all_rates.items():
                    sym = self._normalize_symbol(sym_raw)
                    if sym in self._exchange.symbols:
                        self._update_funding_cache(sym, data)
                        count += 1
                consecutive_failures = 0
                logger.debug(
                    f"Batch funding refresh: {count} rates on {self.exchange_id}",
                    extra={"exchange": self.exchange_id},
                )
            except asyncio.CancelledError:
                return
            except Exception as e:
                consecutive_failures += 1
                if consecutive_failures <= 3:
                    logger.warning(
                        f"Batch funding poll error on {self.exchange_id}: {e}",
                        extra={"exchange": self.exchange_id, "retry": consecutive_failures},
                    )
                elif consecutive_failures % 10 == 0:
                    logger.error(
                        f"Batch funding poll has failed {consecutive_failures} times "
                        f"in a row on {self.exchange_id} — cached data may be STALE: {e}",
                        extra={"exchange": self.exchange_id, "retry": consecutive_failures},
                    )
            await asyncio.sleep(poll_interval)

    async def _sequential_funding_poll_loop(self, symbols: List[str]) -> None:
        """Poll funding rates sequentially with concurrency limit.
        Used for exchanges that don't support batch fetch (e.g. KuCoin).
        One task, 10 concurrent fetches, cycles through all symbols every ~60s.
        """
        sem = asyncio.Semaphore(10)
        consecutive_full_failures = 0
        while True:
            try:
                count = 0

                async def _fetch(sym: str):
                    nonlocal count
                    async with sem:
                        try:
                            data = await self._exchange.fetch_funding_rate(self._resolve_symbol(sym))
                            self._update_funding_cache(sym, data)
                            count += 1
                        except Exception as exc:
                            logger.debug(f"[{self.exchange_id}] Sequential funding fetch failed for {sym}: {exc}")

                await asyncio.gather(*[_fetch(s) for s in symbols], return_exceptions=True)
                if count == 0 and symbols:
                    consecutive_full_failures += 1
                    if consecutive_full_failures <= 3:
                        logger.warning(
                            f"Sequential funding refresh: 0/{len(symbols)} succeeded on {self.exchange_id}",
                            extra={"exchange": self.exchange_id,
                                   "retry": consecutive_full_failures},
                        )
                    elif consecutive_full_failures % 10 == 0:
                        logger.error(
                            f"Sequential funding poll fully failed {consecutive_full_failures} "
                            f"cycles in a row on {self.exchange_id} — cached data may be STALE",
                            extra={"exchange": self.exchange_id,
                                   "retry": consecutive_full_failures},
                        )
                else:
                    consecutive_full_failures = 0
                logger.debug(
                    f"Sequential funding refresh: {count}/{len(symbols)} on {self.exchange_id}",
                    extra={"exchange": self.exchange_id},
                )
            except asyncio.CancelledError:
                return
            except Exception as e:
                consecutive_full_failures += 1
                if consecutive_full_failures <= 3:
                    logger.warning(
                        f"Sequential funding poll error on {self.exchange_id}: {e}",
                        extra={"exchange": self.exchange_id,
                               "retry": consecutive_full_failures},
                    )
                elif consecutive_full_failures % 10 == 0:
                    logger.error(
                        f"Sequential funding poll error {consecutive_full_failures} "
                        f"cycles in a row on {self.exchange_id}: {e}",
                        extra={"exchange": self.exchange_id,
                               "retry": consecutive_full_failures},
                    )
            await asyncio.sleep(30)  # wait between full cycles

    async def _price_poll_loop(self, symbols: List[str]) -> None:
        """Periodically batch-fetch ticker prices as markPrice fallback.
        
        Runs every 15 seconds. Provides real prices for exchanges that don't
        include markPrice in their funding rate response (e.g. KuCoin).
        Stores: markPrice from ticker → last traded price → skips if nothing available.
        """
        poll_interval = 15
        while True:
            try:
                resolved = [self._resolve_symbol(s) for s in symbols]
                tickers = await self._exchange.fetch_tickers(resolved)
                updated = 0
                for sym_raw, ticker in tickers.items():
                    sym = self._normalize_symbol(sym_raw)
                    if sym not in symbols:
                        continue
                    # Prefer markPrice, fall back to last traded price
                    price = ticker.get("markPrice") or ticker.get("last")
                    if price:
                        self._price_cache[sym] = float(price)
                        updated += 1
                logger.debug(
                    f"[{self.exchange_id}] Price poll: updated {updated}/{len(symbols)} symbols",
                    extra={"exchange": self.exchange_id, "action": "price_poll"},
                )
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.debug(
                    f"[{self.exchange_id}] Price poll error (non-critical): {e}",
                    extra={"exchange": self.exchange_id, "action": "price_poll_error"},
                )
            await asyncio.sleep(poll_interval)

    async def disconnect(self) -> None:
        if self._exchange:
            await self._exchange.close()
            logger.info(f"Disconnected from {self.exchange_id}",
                        extra={"exchange": self.exchange_id, "action": "disconnect"})

    async def _maybe_resync_clock(self) -> None:
        """Re-sync clock offset if stale (every 5 minutes).

        Without periodic re-sync, long-running bots accumulate clock drift
        which causes 'timestamp 1000ms ahead of server time' errors on
        Binance/Bybit.
        """
        if not self._exchange or not hasattr(self._exchange, "load_time_difference"):
            return
        now = _time.time()
        if now - self._last_clock_sync < self._CLOCK_RESYNC_INTERVAL:
            return
        try:
            await self._exchange.load_time_difference()
            self._last_clock_sync = now
            offset = self._exchange.options.get("timeDifference", 0)
            if abs(offset) > 500:
                logger.info(
                    f"{self.exchange_id}: clock re-synced (drift={offset}ms)",
                    extra={"exchange": self.exchange_id},
                )
        except Exception as e:
            logger.debug(f"{self.exchange_id}: clock re-sync failed: {e}")

    async def maybe_reload_markets(self) -> None:
        """Reload exchange markets every 4 hours to keep taker fees and contract
        specs up to date (e.g. after account tier upgrades or fee schedule changes).
        Clears the instrument cache so new fees are picked up on the next scan.
        """
        if not self._exchange:
            return
        now = _time.time()
        if now - self._last_markets_reload < self._MARKETS_RELOAD_INTERVAL:
            return
        try:
            await self._exchange.load_markets(reload=True)
            self._instrument_cache.clear()
            self._last_markets_reload = now
            logger.info(
                f"{self.exchange_id}: markets reloaded ({len(self._exchange.markets)} contracts, fees refreshed)",
                extra={"exchange": self.exchange_id, "action": "markets_reloaded"},
            )
        except Exception as e:
            logger.warning(
                f"{self.exchange_id}: markets reload failed: {e}",
                extra={"exchange": self.exchange_id},
            )

    # ── Trading settings ─────────────────────────────────────────

    async def ensure_trading_settings(self, symbol: str) -> None:
        """Set leverage / margin-mode / position-mode (idempotent)."""
        if symbol in self._settings_applied:
            return
        ex = self._exchange
        native_sym = self._resolve_symbol(symbol)  # use original symbol for exchange API
        lev_raw = self._cfg.get("leverage", 1) or 1
        max_lev = int(self._cfg.get("max_leverage", 125) or 125)
        lev = max(1, min(int(lev_raw), max_lev))
        margin = self._cfg.get("margin_mode", "cross")
        pos_mode = self._cfg.get("position_mode", "oneway")

        ok_keywords = ("No need to change", "leverage not modified", "already",
                       "not modified", "no changes", "same")
        # 1) Set margin mode FIRST — OKX requires this before leverage
        try:
            if hasattr(ex, "set_margin_mode"):
                mode_params = {"lever": str(lev)} if self.exchange_id == "okx" else {}
                await ex.set_margin_mode(margin, native_sym, mode_params)
                logger.info(f"{self.exchange_id} {symbol}: margin mode → {margin}",
                            extra={"exchange": self.exchange_id, "symbol": symbol})
        except Exception as e:
            msg = str(e).lower()
            if not any(kw.lower() in msg for kw in ok_keywords):
                logger.warning(f"Margin mode issue on {self.exchange_id} {symbol}: {e}",
                               extra={"exchange": self.exchange_id, "symbol": symbol})

        # 2) Set leverage — include mgnMode param for OKX, marginMode for KuCoin
        try:
            if hasattr(ex, "set_leverage"):
                if self.exchange_id == "okx":
                    lev_params = {"mgnMode": margin}
                elif self.exchange_id == "kucoin":
                    lev_params = {"marginMode": "cross"}
                else:
                    lev_params = {}
                await ex.set_leverage(lev, native_sym, lev_params)
                logger.info(f"{self.exchange_id} {symbol}: leverage → {lev}x",
                            extra={"exchange": self.exchange_id, "symbol": symbol})
        except Exception as e:
            msg = str(e).lower()
            if not any(kw.lower() in msg for kw in ok_keywords):
                logger.warning(f"Leverage issue on {self.exchange_id} {symbol}: {e}",
                               extra={"exchange": self.exchange_id, "symbol": symbol})

        # 3) Position mode
        try:
            if hasattr(ex, "set_position_mode"):
                hedged = (pos_mode == "hedged")
                await ex.set_position_mode(hedged, native_sym)
        except Exception as e:
            msg = str(e).lower()
            if not any(kw.lower() in msg for kw in ok_keywords):
                logger.warning(f"Position mode issue on {self.exchange_id} {symbol}: {e}",
                               extra={"exchange": self.exchange_id, "symbol": symbol})

        logger.info(
            f"Applied settings on {self.exchange_id} {symbol}: lev={lev} margin={margin} pos={pos_mode}",
            extra={"exchange": self.exchange_id, "symbol": symbol, "action": "settings_applied"},
        )
        self._settings_applied.add(symbol)

    # ── Market data ──────────────────────────────────────────────

    async def get_instrument_spec(self, symbol: str) -> Optional[InstrumentSpec]:
        if symbol in self._instrument_cache:
            return self._instrument_cache[symbol]

        mkt = self._exchange.markets.get(symbol)
        if not mkt:
            return None

        # Use CCXT taker fee if available, otherwise conservative fallback 
        # (prevents "$0.00 Fees" accounting and over-optimistic scanning)
        taker_fee = Decimal(str(mkt.get("taker") or 0))
        if taker_fee == 0:
            taker_fee = Decimal("0.0005")  # 0.05% conservative default

        spec = InstrumentSpec(
            exchange=self.exchange_id,
            symbol=symbol,
            base=mkt.get("base", ""),
            quote=mkt.get("quote", ""),
            contract_size=Decimal(str(mkt.get("contractSize", 1))),
            tick_size=Decimal(str(mkt.get("precision", {}).get("price", "0.01"))),
            lot_size=Decimal(str(mkt.get("precision", {}).get("amount", "0.001"))),
            min_notional=Decimal(str(mkt.get("limits", {}).get("cost", {}).get("min", 0) or 0)),
            maker_fee=Decimal(str(mkt.get("maker", 0) or 0)),
            taker_fee=taker_fee,
        )
        self._instrument_cache[symbol] = spec
        return spec

    def update_taker_fee_from_fill(self, symbol: str, fill: dict) -> None:
        """Update cached taker_fee for symbol using the actual fee rate from a fill.

        The fill returned by the exchange contains:
          fill["fee"]["rate"]  — the actual rate charged (e.g. 0.00048)
        If present and non-zero, replace the cached spec so all future
        spread calculations use the real fee for this account.
        """
        fee = fill.get("fee") if isinstance(fill, dict) else None
        if not isinstance(fee, dict):
            return
        rate = fee.get("rate")
        if rate is None:
            # some exchanges put it in fees list
            for f in (fill.get("fees") or []):
                if isinstance(f, dict) and f.get("rate") is not None:
                    rate = f["rate"]
                    break
        if rate is None:
            return
        try:
            new_rate = Decimal(str(rate))
        except Exception:
            return
        if new_rate <= 0:
            return
        existing = self._instrument_cache.get(symbol)
        if existing is None:
            return
        if new_rate == existing.taker_fee:
            return  # no change
        updated = InstrumentSpec(
            exchange=existing.exchange,
            symbol=existing.symbol,
            base=existing.base,
            quote=existing.quote,
            contract_size=existing.contract_size,
            tick_size=existing.tick_size,
            lot_size=existing.lot_size,
            min_notional=existing.min_notional,
            maker_fee=existing.maker_fee,
            taker_fee=new_rate,
        )
        self._instrument_cache[symbol] = updated
        logger.info(
            f"[{self.exchange_id}] {symbol} taker_fee updated from fill: "
            f"{float(existing.taker_fee)*100:.4f}% → {float(new_rate)*100:.4f}%",
            extra={"exchange": self.exchange_id, "symbol": symbol, "action": "fee_updated"},
        )

    async def get_ticker(self, symbol: str) -> Dict[str, Any]:
        async with self._rest_semaphore:
            return await self._exchange.fetch_ticker(self._resolve_symbol(symbol))

    async def get_funding_rate(self, symbol: str) -> Dict[str, Any]:
        async with self._rest_semaphore:
            data = await self._exchange.fetch_funding_rate(self._resolve_symbol(symbol))
        interval_hours = self._get_funding_interval(symbol, data)
        # Pick the EARLIEST future timestamp from both CCXT fields.
        # OKX: fundingTimestamp = upcoming payment, nextFundingTimestamp = the one after.
        # Bitget/others: fundingTimestamp = past payment, nextFundingTimestamp = upcoming.
        _now_ms_pick = _time.time() * 1000
        _ts_a = data.get("nextFundingTimestamp")
        _ts_b = data.get("fundingTimestamp")
        _future_candidates = [t for t in (_ts_a, _ts_b) if t and t > _now_ms_pick]
        next_ts = min(_future_candidates) if _future_candidates else (_ts_a or _ts_b)
        rate = Decimal(str(data.get("fundingRate", 0)))
        
        # 🔍 DEBUG: Log REST fetch
        logger.info(
            f"📡 [{self.exchange_id}] REST fetch_funding_rate for {symbol}: "
            f"raw_rate={data.get('fundingRate')}, rate_decimal={rate:.8f}",
            extra={
                "exchange": self.exchange_id,
                "symbol": symbol,
                "action": "rest_funding_fetch",
                "raw_rate": str(data.get("fundingRate")),
            },
        )

        # Sanity check: clamp insane rates to zero
        if abs(rate) > self._MAX_SANE_RATE:
            logger.warning(
                f"⚠️  Clamping insane rate {rate} to 0 for {symbol} on {self.exchange_id}",
                extra={"exchange": self.exchange_id, "symbol": symbol},
            )
            rate = Decimal("0")

        # If next_timestamp is in the past, advance by interval until future
        now_ms = _time.time() * 1000
        interval_ms = interval_hours * 3_600_000

        # If exchange doesn't provide next funding time, compute from interval
        if not next_ts and interval_ms > 0:
            next_ts = (int(now_ms // interval_ms) + 1) * interval_ms

        if next_ts and interval_ms > 0:
            while next_ts <= now_ms:
                next_ts += interval_ms

        return {
            "rate": rate,
            "timestamp": data.get("timestamp"),
            "datetime": data.get("datetime"),
            "next_timestamp": next_ts,
            "interval_hours": interval_hours,
        }

    def _get_funding_interval(self, symbol: str, funding_data: dict) -> int:
        """Detect funding interval in hours from CCXT data.

        Priority:
          1) Raw API info dict — most reliable (exchange's own field, not computed)
             • Gate.io:  info.funding_interval     (seconds, snake_case) e.g. 28800 → 8h
             • Bybit:    info.fundingInterval      (minutes, camelCase)  e.g.  480 → 8h
             • Binance:  info.fundingIntervalHours                       e.g.    8 → 8h
             • KuCoin:   info.granularity          (milliseconds)        e.g. 28800000 → 8h
             • Bitget:   info.fundingRateInterval  (hours, string)       e.g.   "8" → 8h
                      or info.ratePeriod           (hours, string)       e.g.   "8" → 8h
          2) CCXT normalized 'interval' string (e.g. '1h', '8h') — used only when
             raw info is absent.  CAUTION: for some exchanges (e.g. Gate.io) CCXT
             may compute this as (next_funding_ts - now) / 3600, giving a spuriously
             small value near funding payment times (e.g. '1h' when exchange is 8h).
          3) Market info (static, loaded at startup) — same field cascade as step 1
          4) Pre-fetched Binance fundingInfo table
          5) Default 8h

        Change-confirmation guard: a detected interval different from the stored
        value must appear in 2 consecutive polls before being accepted.  This
        filters transient CCXT mis-computations near payment events.
        """
        detected: int | None = None

        # 1) Raw API info — highest priority; exchange provides explicit field
        info = funding_data.get("info", {}) or {}
        if isinstance(info, dict):
            # Gate.io: funding_interval in seconds (snake_case)
            fi_sec = info.get("funding_interval")
            if fi_sec is not None:
                try:
                    seconds = int(fi_sec)
                    if seconds > 0:
                        detected = max(1, seconds // 3600)
                except (ValueError, TypeError):
                    pass
            # Bybit: fundingInterval in minutes (camelCase)
            if detected is None:
                fi_min = info.get("fundingInterval")
                if fi_min is not None:
                    try:
                        detected = max(1, int(fi_min) // 60)
                    except (ValueError, TypeError):
                        pass
            # Binance: fundingIntervalHours
            if detected is None:
                fi_h = info.get("fundingIntervalHours")
                if fi_h is not None:
                    try:
                        detected = int(fi_h)
                    except (ValueError, TypeError):
                        pass
            # KuCoin Futures: granularity in milliseconds (e.g. 28800000 = 8h)
            if detected is None:
                fi_ms = info.get("granularity")
                if fi_ms is not None:
                    try:
                        ms = int(fi_ms)
                        if ms > 0:
                            detected = max(1, ms // 3_600_000)
                    except (ValueError, TypeError):
                        pass
            # Bitget: fundingRateInterval or ratePeriod in hours (string "8")
            if detected is None:
                fi_bitget = info.get("fundingRateInterval") or info.get("ratePeriod")
                if fi_bitget is not None:
                    try:
                        detected = int(fi_bitget)
                    except (ValueError, TypeError):
                        pass

        # 2) CCXT normalized 'interval' string — fallback only
        #    (may be computed from timestamps, unreliable near payment time)
        if detected is None:
            interval_str = funding_data.get("interval") or ""
            if interval_str:
                try:
                    detected = int(interval_str.replace("h", ""))
                except ValueError:
                    pass

        # 3) Fallback: market info (static from exchange load)
        if detected is None:
            mkt = self._exchange.markets.get(symbol)
            if mkt:
                mkt_info = mkt.get("info", {}) or {}
                # Gate.io market info also uses snake_case seconds
                fi_sec = mkt_info.get("funding_interval")
                if fi_sec is not None:
                    try:
                        seconds = int(fi_sec)
                        if seconds > 0:
                            detected = max(1, seconds // 3600)
                    except (ValueError, TypeError):
                        pass
                if detected is None:
                    fi_min = mkt_info.get("fundingInterval")
                    if fi_min:
                        try:
                            detected = max(1, int(fi_min) // 60)
                        except (ValueError, TypeError):
                            pass
                # KuCoin market info: fundingFeeRate granularity in ms
                if detected is None:
                    fi_ms = mkt_info.get("granularity")
                    if fi_ms is not None:
                        try:
                            ms = int(fi_ms)
                            if ms > 0:
                                detected = max(1, ms // 3_600_000)
                        except (ValueError, TypeError):
                            pass
                # Bitget market info: fundingInterval or ratePeriod in hours
                if detected is None:
                    fi_bitget = mkt_info.get("fundingRateInterval") or mkt_info.get("ratePeriod") or mkt_info.get("fundInterval")
                    if fi_bitget is not None:
                        try:
                            detected = int(fi_bitget)
                        except (ValueError, TypeError):
                            pass

        # 4) Pre-fetched from Binance /fapi/v1/fundingInfo
        if detected is None and symbol in self._funding_intervals:
            return self._funding_intervals[symbol]

        # 5) Default 8h
        if detected is None:
            return 8

        # Reject invalid zero intervals — would break timestamp advancement
        if detected <= 0:
            return self._funding_intervals.get(symbol) or 8

        # ── Change-confirmation guard ──────────────────────────────────
        # If the detected interval differs from the stored one, require it
        # to appear in 2 CONSECUTIVE polls before accepting the change.
        # This prevents transient CCXT mis-computations (e.g. near payment
        # time) from permanently flipping the stored interval.
        old = self._funding_intervals.get(symbol)
        if old is not None and old != detected:
            candidate, count = self._interval_change_candidates.get(symbol, (detected, 0))
            if candidate == detected:
                count += 1
            else:
                # Different candidate — reset counter
                count = 1
            self._interval_change_candidates[symbol] = (detected, count)
            if count < 2:
                # Not yet confirmed — keep old interval, log a debug notice
                logger.debug(
                    f"⏱️ Interval candidate {detected}h for {symbol} on {self.exchange_id} "
                    f"(stored={old}h, need 2 consecutive, have {count})",
                    extra={"exchange": self.exchange_id, "symbol": symbol,
                           "action": "interval_candidate"},
                )
                return old  # keep old until confirmed
            # Confirmed change
            del self._interval_change_candidates[symbol]
            logger.warning(
                f"⏱️ Funding interval CHANGED for {symbol} on {self.exchange_id}: "
                f"{old}h → {detected}h (confirmed over 2 polls)",
                extra={"exchange": self.exchange_id, "symbol": symbol,
                       "action": "interval_changed",
                       "old_hours": old, "new_hours": detected},
            )
        else:
            # No change — clear any stale candidate
            self._interval_change_candidates.pop(symbol, None)

        self._funding_intervals[symbol] = detected
        return detected

    # ── Account ──────────────────────────────────────────────────

    async def get_balance(self) -> Dict[str, Any]:
        async with self._rest_semaphore:
            bal = await self._exchange.fetch_balance()
        # Try USDT first, fall back to USD (e.g. Kraken Futures settles in USD)
        usdt = bal.get("USDT", {})
        if not usdt.get("total"):
            usdt = bal.get("USD", {})
        return {
            "total": Decimal(str(usdt.get("total", 0) or 0)),
            "free":  Decimal(str(usdt.get("free", 0) or 0)),
            "used":  Decimal(str(usdt.get("used", 0) or 0)),
        }

    async def fetch_funding_history(
        self,
        symbol: str,
        since_ms: Optional[int] = None,
        until_ms: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Fetch actual funding payments from the exchange for a symbol, filtered by time range.

        Returns a dict:
            {
                "net_usd":      float,   # positive = received, negative = paid
                "received_usd": float,   # sum of positive payments
                "paid_usd":     float,   # sum of negative payments (abs value)
                "payments":     list,    # raw list of {timestamp, amount, rate?, info}
                "source":       str,     # "exchange" | "unavailable"
            }
        """
        resolved = self._resolve_symbol(symbol)
        payments: List[Dict] = []

        try:
            # ── Binance: uses fetch_income_history with type='FUNDING_FEE' ──
            if self.exchange_id in ("binanceusdm", "binance", "binancecoinm"):
                has_income = getattr(self._exchange, "has", {}).get("fetchIncomeHistory", False)
                if has_income:
                    params: Dict[str, Any] = {"type": "FUNDING_FEE"}
                    if since_ms:
                        params["startTime"] = since_ms
                    if until_ms:
                        params["endTime"] = until_ms
                    raw = await self._exchange.fetch_income_history(resolved, params=params)
                    for r in (raw or []):
                        ts = r.get("timestamp") or r.get("time", 0)
                        if since_ms and ts < since_ms:
                            continue
                        if until_ms and ts > until_ms:
                            continue
                        payments.append({
                            "timestamp": ts,
                            "amount": float(r.get("amount", 0) or 0),
                            "info": r.get("info", {}),
                        })

            # ── All others: ccxt fetch_funding_history ──
            elif getattr(self._exchange, "has", {}).get("fetchFundingHistory", False):
                raw = await self._exchange.fetch_funding_history(
                    resolved, since=since_ms, limit=50
                )
                for r in (raw or []):
                    ts = r.get("timestamp", 0) or 0
                    if since_ms and ts < (since_ms - 60_000):  # 1 min tolerance
                        continue
                    if until_ms and ts > (until_ms + 60_000):
                        continue
                    amt = float(r.get("amount", 0) or 0)
                    payments.append({
                        "timestamp": ts,
                        "amount": amt,
                        "info": r.get("info", {}),
                    })

        except Exception as e:
            logger.warning(
                f"[{self.exchange_id}] fetch_funding_history({symbol}) failed: {e}",
                extra={"exchange": self.exchange_id, "symbol": symbol},
            )
            return {
                "net_usd": 0.0, "received_usd": 0.0, "paid_usd": 0.0,
                "payments": [], "source": "unavailable",
            }

        if not payments:
            return {
                "net_usd": 0.0, "received_usd": 0.0, "paid_usd": 0.0,
                "payments": payments, "source": "unavailable",
            }

        received = sum(p["amount"] for p in payments if p["amount"] > 0)
        paid_abs = sum(abs(p["amount"]) for p in payments if p["amount"] < 0)
        net = sum(p["amount"] for p in payments)
        return {
            "net_usd": net,
            "received_usd": received,
            "paid_usd": paid_abs,
            "payments": payments,
            "source": "exchange",
        }

    async def get_positions(self, symbol: Optional[str] = None) -> List[Position]:
        symbols = [self._resolve_symbol(symbol)] if symbol else None

        # Retry up to 2 times on transient API failures (rate-limit, timeout)
        last_err: Optional[Exception] = None
        for attempt in range(3):
            try:
                async with self._rest_semaphore:
                    raw = await self._exchange.fetch_positions(symbols)
                break
            except Exception as e:
                last_err = e
                if attempt < 2:
                    await asyncio.sleep(0.5 * (attempt + 1))
        else:
            raise last_err  # type: ignore[misc]

        positions: List[Position] = []
        for p in raw:
            amt = float(p.get("contracts", 0) or 0)
            if abs(amt) < 1e-12:
                continue

            # Convert from contracts to base currency (tokens)
            sym = self._normalize_symbol(p["symbol"])  # convert back to normalized
            mkt = self._exchange.markets.get(sym)
            contract_sz = float(mkt.get("contractSize", 1) or 1) if mkt else 1.0
            amt_base = amt * contract_sz

            side_raw = (p.get("side") or "").lower()
            if side_raw in ("long", "buy"):
                side = OrderSide.BUY
            elif side_raw in ("short", "sell"):
                side = OrderSide.SELL
            else:
                side = OrderSide.BUY if amt_base > 0 else OrderSide.SELL

            positions.append(Position(
                exchange=self.exchange_id,
                symbol=sym,
                side=side,
                quantity=Decimal(str(abs(amt_base))),
                entry_price=Decimal(str(p.get("entryPrice", 0) or 0)),
                unrealized_pnl=Decimal(str(p.get("unrealizedPnl", 0) or 0)),
                leverage=int(p.get("leverage", 1) or 1),
            ))
        return positions

    # ── Order fill verification (fallback for exchanges where fetchOrder fails) ──

    async def _verify_fill_via_position(
        self, symbol: str, resolved_symbol: str, side: OrderSide,
        expected_native_qty: float, order_id: Optional[str] = None,
        reduce_only: bool = False,
    ) -> float:
        """Fallback fill verification by checking actual position on the exchange.

        When fetchOrder() fails (e.g. Bybit "last 500 orders" error), we check
        if a matching position exists.

        For ENTRY orders (reduce_only=False):
          If a position in the expected direction exists → order was filled.

        For CLOSE orders (reduce_only=True):
          If the position is GONE (no position found) → close order filled.
          We sent a reduce_only order, so absence of position = success.

        Returns filled quantity in NATIVE units (contracts), or 0.0 if unverifiable.
        """
        try:
            positions = await self._exchange.fetch_positions([resolved_symbol])

            if reduce_only:
                # For close orders: check that the position is gone or reduced.
                # The order side is the OPPOSITE of the position side:
                #   reduce_only SELL = closing a LONG
                #   reduce_only BUY  = closing a SHORT
                position_side = "long" if side == OrderSide.SELL else "short"
                for pos in positions:
                    amt = float(pos.get("contracts", 0) or 0)
                    if abs(amt) < 1e-12:
                        continue
                    pos_side = (pos.get("side") or "").lower()
                    if pos_side not in ("long", "buy", "short", "sell"):
                        pos_side = "long" if amt > 0 else "short"
                    else:
                        pos_side = "long" if pos_side in ("long", "buy") else "short"
                    if pos_side == position_side:
                        # Position still exists — close order may not have filled
                        remaining = abs(amt)
                        if remaining >= expected_native_qty * 0.95:
                            logger.warning(
                                f"Close order NOT verified on {self.exchange_id}/{symbol}: "
                                f"{position_side} position still has {remaining} contracts "
                                f"(expected to close {expected_native_qty}). "
                                f"order_id={order_id}",
                                extra={"exchange": self.exchange_id, "symbol": symbol},
                            )
                            return 0.0
                        else:
                            # Position partially reduced
                            closed_qty = expected_native_qty - remaining
                            logger.warning(
                                f"✅ Position-verified PARTIAL close on {self.exchange_id}/{symbol}: "
                                f"{position_side} reduced from {expected_native_qty} to {remaining}. "
                                f"order_id={order_id}",
                                extra={"exchange": self.exchange_id, "symbol": symbol,
                                       "action": "position_verified_fill"},
                            )
                            return closed_qty

                # No position found in the expected direction → fully closed
                logger.warning(
                    f"✅ Position-verified CLOSE on {self.exchange_id}/{symbol}: "
                    f"{position_side} position gone ({expected_native_qty} contracts closed). "
                    f"order_id={order_id} — fetchOrder() was unreliable.",
                    extra={"exchange": self.exchange_id, "symbol": symbol,
                           "action": "position_verified_fill"},
                )
                return expected_native_qty
            else:
                # Entry order: check that position EXISTS
                expected_side = "long" if side == OrderSide.BUY else "short"
                for pos in positions:
                    amt = float(pos.get("contracts", 0) or 0)
                    if abs(amt) < 1e-12:
                        continue
                    pos_side = (pos.get("side") or "").lower()
                    if pos_side not in ("long", "buy", "short", "sell"):
                        pos_side = "long" if amt > 0 else "short"
                    else:
                        pos_side = "long" if pos_side in ("long", "buy") else "short"

                    if pos_side == expected_side:
                        actual_qty = abs(amt)
                        logger.warning(
                            f"✅ Position-verified fill on {self.exchange_id}/{symbol}: "
                            f"found {expected_side} position with {actual_qty} contracts "
                            f"(expected {expected_native_qty}). "
                            f"order_id={order_id} — fetchOrder() was unreliable.",
                            extra={
                                "exchange": self.exchange_id,
                                "symbol": symbol,
                                "action": "position_verified_fill",
                            },
                        )
                        return min(actual_qty, expected_native_qty)

                logger.warning(
                    f"Order filled=0 after 3 re-fetches AND no matching position on "
                    f"{self.exchange_id}/{symbol} (order_id={order_id}) — "
                    f"genuinely unfilled or position already closed.",
                    extra={"exchange": self.exchange_id, "symbol": symbol},
                )
                return 0.0
        except Exception as e:
            logger.error(
                f"Position-based fill verification failed on "
                f"{self.exchange_id}/{symbol}: {e}",
                extra={"exchange": self.exchange_id, "symbol": symbol},
            )
            return 0.0

    # ── Order execution ──────────────────────────────────────────

    async def place_order(self, req: OrderRequest) -> Dict[str, Any]:
        """Place a market order. Returns the ccxt order dict."""
        await self.ensure_trading_settings(req.symbol)

        params: Dict[str, Any] = {}
        if req.reduce_only:
            params["reduceOnly"] = True

        # KuCoin: always pass marginMode in order params
        if self.exchange_id == "kucoin":
            params["marginMode"] = self._cfg.get("margin_mode", "cross")

        # Exchange-specific position side for hedged mode only
        pos_mode = self._cfg.get("position_mode", "oneway")
        if pos_mode == "hedged":
            if self.exchange_id == "binanceusdm":
                if req.reduce_only:
                    params["positionSide"] = "LONG" if req.side == OrderSide.SELL else "SHORT"
                else:
                    params["positionSide"] = "LONG" if req.side == OrderSide.BUY else "SHORT"
            elif self.exchange_id == "okx":
                if req.reduce_only:
                    params["posSide"] = "long" if req.side == OrderSide.SELL else "short"
                else:
                    params["posSide"] = "long" if req.side == OrderSide.BUY else "short"

        # Normalize quantity — req.quantity is always in BASE CURRENCY (tokens)
        spec = await self.get_instrument_spec(req.symbol)
        base_qty = float(req.quantity)
        contract_size = float(spec.contract_size) if spec and spec.contract_size else 1.0

        # Convert from base currency (tokens) to exchange-native units (contracts)
        # For Bybit/Binance contractSize=1 → no change. For OKX it can be != 1.
        native_qty = base_qty / contract_size if contract_size > 0 else base_qty

        # Round to exchange's native lot step (precision.amount — in contracts)
        if spec and float(spec.lot_size) > 0:
            lot = float(spec.lot_size)
            native_qty = round(native_qty / lot) * lot
            native_qty = max(native_qty, lot)

        # Re-sync clock if stale — prevents "timestamp ahead of server time"
        await self._maybe_resync_clock()

        order = await self._exchange.create_order(
            symbol=self._resolve_symbol(req.symbol),
            type="market",
            side=req.side.value,
            amount=native_qty,
            params=params,
        )

        # Convert filled amount BACK to base currency (tokens) for the caller
        filled_native = float(order.get("filled", 0) or 0)

        # ── Re-fetch order if filled=0 ──────────────────────────────
        # Some exchanges (gateio, kucoin, okx) return filled=0/None on
        # create_order for market orders, requiring a follow-up fetch to
        # get the actual fill.  Without this, the bot records a zero-fill
        # and the "or order_qty" fallback silently masks the discrepancy,
        # creating one-sided (unhedged) positions.
        if filled_native == 0 and order.get("id"):
            _resolved = self._resolve_symbol(req.symbol)
            # Bybit: pass acknowledged=True to suppress "last 500 orders" warning
            _fetch_params: Dict[str, Any] = {}
            if self.exchange_id == "bybit":
                _fetch_params["acknowledged"] = True
            for _attempt in range(1, 4):            # 3 attempts, 1s apart
                try:
                    await asyncio.sleep(1)
                    updated = await self._exchange.fetch_order(
                        order["id"], _resolved, _fetch_params if _fetch_params else None,
                    )
                    filled_native = float(updated.get("filled", 0) or 0)
                    if filled_native > 0:
                        order.update(updated)       # merge full fill details
                        logger.info(
                            f"Order re-fetched on {self.exchange_id} "
                            f"(attempt {_attempt}): filled={filled_native} {req.symbol}",
                            extra={"exchange": self.exchange_id, "symbol": req.symbol},
                        )
                        break
                except Exception as _rfe:
                    logger.warning(
                        f"Order re-fetch attempt {_attempt} failed on "
                        f"{self.exchange_id}/{req.symbol}: {_rfe}",
                        extra={"exchange": self.exchange_id, "symbol": req.symbol},
                    )
            else:
                # ── Bybit fallback: fetchOrder() can fail with "last 500 orders"
                # error. Fall back to checking actual positions to confirm fill.
                filled_native = await self._verify_fill_via_position(
                    req.symbol, _resolved, req.side, native_qty, order.get("id"),
                    reduce_only=req.reduce_only,
                )
                if filled_native > 0:
                    order["filled"] = filled_native  # will be overwritten below with base conversion
                    order["average"] = order.get("average")  # keep whatever we have

        filled_base = filled_native * contract_size
        order["filled"] = filled_base
        # Also store the base-currency qty we requested (for logging)
        order["_base_qty_requested"] = base_qty
        order["_contract_size"] = contract_size

        logger.info(
            f"Order placed on {self.exchange_id}: {req.side.value} "
            f"{native_qty} contracts (={filled_base:.6f} base) {req.symbol}",
            extra={
                "exchange": self.exchange_id,
                "symbol": req.symbol,
                "action": "order_placed",
                "data": {
                    "order_id": order.get("id"),
                    "side": req.side.value,
                    "native_qty": native_qty,
                    "base_qty": filled_base,
                    "contract_size": contract_size,
                    "reduce_only": req.reduce_only,
                    "filled_native": filled_native,
                    "avg_price": order.get("average"),
                },
            },
        )
        return order

    # ── Warm up ──────────────────────────────────────────────────

    async def warm_up_symbols(self, symbols: List[str]) -> None:
        """Pre-fetch instrument specs for all watched symbols."""
        tasks = [self.get_instrument_spec(s) for s in symbols]
        await asyncio.gather(*tasks, return_exceptions=True)
        logger.info(
            f"Warmed up {len(symbols)} symbols on {self.exchange_id}",
            extra={"exchange": self.exchange_id, "action": "warm_up"},
        )


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

    def all(self) -> Mapping[str, ExchangeAdapter]:
        """Return a read-only view of the internal adapters dict.
        
        Uses ``MappingProxyType`` — zero-copy, prevents accidental mutation
        by callers while avoiding a full dict copy every 5-second status cycle.
        """
        return MappingProxyType(self._adapters)

    async def connect_all(self) -> None:
        for adapter in self._adapters.values():
            try:
                await adapter.connect()
            except Exception as e:
                logger.error(f"Failed to connect {adapter.exchange_id}: {e}",
                             extra={"exchange": adapter.exchange_id})

    async def verify_all(self) -> list[str]:
        """Verify credentials on every adapter; remove & disconnect failures.

        Returns list of exchange ids that passed.
        """
        failed: list[str] = []
        for eid, adapter in list(self._adapters.items()):
            ok = await adapter.verify_credentials()
            if not ok:
                failed.append(eid)
                await adapter.disconnect()
                del self._adapters[eid]
                logger.warning(f"Removed {eid} — invalid credentials",
                               extra={"exchange": eid, "action": "exchange_removed"})
        return list(self._adapters.keys())

    async def disconnect_all(self) -> None:
        for adapter in self._adapters.values():
            try:
                await adapter.disconnect()
            except Exception as exc:
                logger.debug(f"Disconnect failed for {adapter.exchange_id}: {exc}")
