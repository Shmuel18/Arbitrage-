"""Lifecycle mixin — connect, disconnect, credential verification, clock sync."""

from __future__ import annotations

import asyncio
import logging
import time as _time
import types
from typing import Any, Dict, List, Optional

import aiohttp
import ccxt.pro as ccxtpro

from src.core.logging import get_logger

logger = get_logger("exchanges")


class _LifecycleMixin:
    """Connection lifecycle, supervised tasks, and credential management."""

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
        # Prune completed/cancelled tasks before appending — prevents unbounded
        # list growth over long uptimes (each crashed+restarted task is a new object).
        self._ws_tasks = [t for t in self._ws_tasks if not t.done()]
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
                # adjustForTimeDifference + load_time_difference() at connect time
                # compensate for clock skew automatically. In practice, under
                # exchange/API congestion a 5s signed-request window is too
                # tight for long-running bots and can trigger false timestamp
                # drift rejections (notably on Bybit) even after clock sync.
                "adjustForTimeDifference": True,
                "recvWindow": 10000,
            },
        }
        if pw := self._cfg.get("api_passphrase"):
            opts["password"] = pw
        if self._cfg.get("testnet"):
            opts["sandbox"] = True

        self._exchange = cls(opts)
        del opts  # purge plaintext credentials from this scope immediately
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

        def _patched_parse_funding_rate(_ex, ticker, market=None):
            market_id = _ex.safe_string(ticker, 'symbol')
            symbol = _ex.symbol(market_id)
            timestamp = _ex.parse8601(_ex.safe_string(ticker, 'lastTime'))
            mark_price_str = _ex.safe_string(ticker, 'markPrice')
            funding_rate_str = _ex.safe_string(ticker, 'fundingRate')
            next_rate_str = _ex.safe_string(ticker, 'fundingRatePrediction')

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
                'markPrice': _ex.parse_number(mark_price_str),
                'indexPrice': _ex.safe_number(ticker, 'indexPrice'),
                'interestRate': None,
                'estimatedSettlePrice': None,
                'timestamp': timestamp,
                'datetime': _ex.iso8601(timestamp),
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

        # Bind as a proper instance method so it behaves identically to a
        # subclass override — `self` inside the function will be the ccxt
        # exchange instance, matching how ccxt calls it internally.
        ex.parse_funding_rate = types.MethodType(_patched_parse_funding_rate, ex)
        logger.debug(
            f"Patched parse_funding_rate on {self.exchange_id}",
            extra={"exchange": self.exchange_id},
        )

    async def verify_credentials(self) -> bool:
        """Test an authenticated call. Returns False only if keys are truly invalid.

        Retries up to 3 times on network errors before giving up.
        Distinguishes auth failures (wrong API key) from transient
        network issues (timeout, DNS, rate-limit).
        """
        max_retries = 3
        for attempt in range(1, max_retries + 1):
            try:
                await self._exchange.fetch_balance()
                return True
            except (
                ccxtpro.AuthenticationError,
                ccxtpro.PermissionDenied,
                ccxtpro.AccountNotEnabled,
            ) as e:
                logger.warning(
                    f"Credentials invalid for {self.exchange_id}: {e}",
                    extra={"exchange": self.exchange_id, "action": "auth_fail"},
                )
                return False
            except (
                ccxtpro.NetworkError,
                ccxtpro.RequestTimeout,
                ccxtpro.ExchangeNotAvailable,
                ccxtpro.DDoSProtection,
                OSError,
            ) as e:
                if attempt < max_retries:
                    wait = 5 * attempt
                    logger.warning(
                        f"Network error verifying {self.exchange_id} "
                        f"(attempt {attempt}/{max_retries}): {e} — retrying in {wait}s",
                        extra={"exchange": self.exchange_id, "action": "auth_retry"},
                    )
                    await asyncio.sleep(wait)
                else:
                    logger.error(
                        f"Could not verify {self.exchange_id} after {max_retries} "
                        f"attempts (network issue, NOT invalid keys): {e}",
                        extra={"exchange": self.exchange_id, "action": "auth_network_fail"},
                    )
                    return False
            except Exception as e:
                logger.warning(
                    f"Unexpected error verifying {self.exchange_id}: {e}",
                    extra={"exchange": self.exchange_id, "action": "auth_fail"},
                )
                return False
        return False  # unreachable but satisfies type checker

    async def disconnect(self) -> None:
        # Cancel all supervised WebSocket tasks first — prevents them from making
        # calls on a closed exchange object after disconnect returns.
        if self._ws_tasks:
            for task in self._ws_tasks:
                task.cancel()
            await asyncio.gather(*self._ws_tasks, return_exceptions=True)
            self._ws_tasks.clear()
        if self._exchange:
            await self._exchange.close()
            logger.info(f"Disconnected from {self.exchange_id}",
                        extra={"exchange": self.exchange_id, "action": "disconnect"})

    def cancel_ws_tasks(self) -> None:
        """Cancel all supervised WebSocket tasks synchronously.

        Use this from synchronous shutdown paths (e.g. ``Scanner.stop()``) where
        awaiting ``disconnect()`` is not possible.  The tasks are cancelled but
        not awaited; the event-loop will clean them up on the next iteration.
        """
        for task in self._ws_tasks:
            task.cancel()

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
