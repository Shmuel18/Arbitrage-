"""Funding rate mixin — watchers, polling loops.

Cache management, interval detection, and public accessors
are in _funding_cache_mixin.py (_FundingCacheMixin).

Do NOT import this module directly; ExchangeAdapter uses _FundingMixin.
"""

from __future__ import annotations

import asyncio
import logging
import time as _time
from decimal import Decimal
from typing import Any, Dict, List, Optional

from src.core.logging import get_logger
from src.exchanges._funding_cache_mixin import _FundingCacheMixin

logger = get_logger("exchanges")


class _FundingMixin(_FundingCacheMixin):
    """Funding rate watchers, polling loops, and warm-up."""

    # Maximum plausible absolute funding rate per interval — configurable via config.yaml
    _DEFAULT_MAX_SANE_RATE = Decimal("0.10")

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
                        try:
                            self._update_funding_cache(sym, data)
                            count += 1
                        except Exception as sym_exc:
                            logger.debug(
                                f"[{self.exchange_id}] Failed to cache funding for {sym}: {sym_exc}",
                                extra={"exchange": self.exchange_id, "symbol": sym},
                            )
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
