"""Funding rate mixin — watchers, polling loops.

Cache management, interval detection, and public accessors
are in _funding_cache_mixin.py (_FundingCacheMixin).

Do NOT import this module directly; ExchangeAdapter uses _FundingMixin.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time as _time
from decimal import Decimal
from typing import Any, Dict, List, Optional

from src.core.logging import get_logger
from src.exchanges._funding_cache_mixin import _FundingCacheMixin

logger = get_logger("exchanges")

_TIMEOUT_ERROR_PHRASES = (
    "ping-pong keepalive",
    "connection timeout",
    "timed out",
    "closing code 1006",
    "cannot connect to host",
    "timeout while contacting dns servers",
)


class _FundingMixin(_FundingCacheMixin):
    """Funding rate watchers, polling loops, and warm-up."""

    # Maximum plausible absolute funding rate per interval — configurable via config.yaml
    _DEFAULT_MAX_SANE_RATE = Decimal("0.10")

    # Some exchanges (e.g. KuCoin) limit watchTickers() to 100 symbols per call.
    # Batching to this size is safe for all exchanges and avoids API rejections.
    _WS_TICKER_BATCH_SIZE = 100

    # KuCoin futures enforces a maximum number of WS subscriptions per session.
    # Keep a safety margin below the hard 400 limit to avoid session churn.
    _KUCOIN_WS_TICKER_MAX_SUBSCRIPTIONS = 380

    async def _refresh_prices_via_poll_once(self, symbols: List[str]) -> int:
        """Run a single REST ticker refresh to keep bid/ask cache alive during WS issues."""
        resolved = [self._resolve_symbol(s) for s in symbols]
        async with self._rest_semaphore:
            tickers = await self._exchange.fetch_tickers(resolved)

        updated = 0
        for sym_raw, ticker in tickers.items():
            sym = self._normalize_symbol(sym_raw)
            if sym not in symbols:
                continue
            self._update_price_cache_from_ticker(sym, ticker, source="poll")
            if ticker.get("bid") is not None or ticker.get("ask") is not None:
                updated += 1
        return updated

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

        # Prefer WebSocket tickers for near-real-time bid/ask freshness. Fall back
        # to REST polling on exchanges that do not support batch ticker streams.
        # Batch into chunks of _WS_TICKER_BATCH_SIZE to respect per-exchange
        # API limits (e.g. KuCoin allows max 100 symbols per watchTickers call).
        if self._ws_ticker_supported and hasattr(self._exchange, "watch_tickers"):
            ws_symbols = eligible
            poll_fallback_symbols: List[str] = []

            if self.exchange_id == "kucoin" and len(eligible) > self._KUCOIN_WS_TICKER_MAX_SUBSCRIPTIONS:
                ws_symbols = eligible[: self._KUCOIN_WS_TICKER_MAX_SUBSCRIPTIONS]
                poll_fallback_symbols = eligible[self._KUCOIN_WS_TICKER_MAX_SUBSCRIPTIONS :]
                logger.info(
                    f"[kucoin] Limiting price-ws subscriptions to {len(ws_symbols)} symbols "
                    f"(overflow {len(poll_fallback_symbols)} via polling)",
                    extra={"exchange": self.exchange_id, "action": "ws_start"},
                )

            if ws_symbols:
                chunks = [
                    ws_symbols[i : i + self._WS_TICKER_BATCH_SIZE]
                    for i in range(0, len(ws_symbols), self._WS_TICKER_BATCH_SIZE)
                ]
                for idx, chunk in enumerate(chunks):
                    self._create_supervised_task(
                        lambda syms=chunk: self._watch_price_tickers_loop(syms),
                        name=f"price-ws-{idx}",
                    )
                logger.info(
                    f"[{self.exchange_id}] Started {len(chunks)} price-ws task(s) "
                    f"({len(ws_symbols)} symbols, batch={self._WS_TICKER_BATCH_SIZE})",
                    extra={"exchange": self.exchange_id, "action": "ws_start"},
                )

            if poll_fallback_symbols:
                self._create_supervised_task(
                    lambda syms=poll_fallback_symbols: self._price_poll_loop(syms),
                    name="price-poll-overflow",
                )
        else:
            self._create_supervised_task(
                lambda syms=eligible: self._price_poll_loop(syms),
                name="price-poll",
            )

    async def _watch_price_tickers_loop(self, symbols: List[str]) -> None:
        """Watch top-of-book ticker updates via WebSocket when supported."""
        resolved = [self._resolve_symbol(s) for s in symbols]
        consecutive_failures = 0
        while True:
            try:
                tickers = await self._exchange.watch_tickers(resolved)
                updated = 0
                for sym_raw, ticker in (tickers or {}).items():
                    sym = self._normalize_symbol(sym_raw)
                    if sym not in symbols:
                        continue
                    self._update_price_cache_from_ticker(sym, ticker, source="ws")
                    updated += 1
                consecutive_failures = 0
                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug(
                        f"[{self.exchange_id}] Price WS: updated {updated}/{len(symbols)} symbols",
                        extra={"exchange": self.exchange_id, "action": "price_ws"},
                    )
            except asyncio.CancelledError:
                return
            except Exception as exc:
                exc_str = str(exc)
                msg = exc_str.lower()

                # Bitget (and potentially other exchanges) reject subscriptions for
                # delisted/non-existent symbols. Extract the offending symbol from
                # the error message, remove it from the batch, and retry immediately
                # rather than flooding logs and hammering the exchange.
                if "doesn't exist" in msg or "does not exist" in msg or "does not have market" in msg:
                    bad_inst: Optional[str] = None
                    _m = re.search(r'"instId"\s*:\s*"([^"]+)"', exc_str)
                    if not _m:
                        _m = re.search(r'instId:([A-Z0-9]+)', exc_str)
                    if not _m:
                        # ccxt format: "exchange does not have market symbol FOO/USDT:USDT"
                        _m = re.search(r'market symbol\s+([A-Z0-9]+/[A-Z0-9:]+)', exc_str)
                    if _m:
                        bad_inst = _m.group(1)  # e.g. "MBOXUSDT" or "ACN/USDT:USDT"
                    if bad_inst:
                        bad_resolved = next((s for s in resolved if bad_inst in s), None)
                        if bad_resolved and bad_resolved in resolved:
                            bad_idx = resolved.index(bad_resolved)
                            bad_sym = symbols[bad_idx] if bad_idx < len(symbols) else bad_resolved
                            resolved.remove(bad_resolved)
                            if bad_sym in symbols:
                                symbols.remove(bad_sym)
                            # Clean ccxt's internal subscription state so the
                            # symbol is NOT re-subscribed on WS reconnect.
                            try:
                                if hasattr(self._exchange, 'un_watch_ticker'):
                                    await self._exchange.un_watch_ticker(bad_resolved)
                            except Exception:
                                pass  # best-effort cleanup; symbol is already removed from our list
                            logger.warning(
                                f"[{self.exchange_id}] Dropping delisted symbol {bad_sym} "
                                f"({bad_inst}) from price WS — exchange rejected subscription",
                                extra={"exchange": self.exchange_id},
                            )
                            if not resolved:
                                logger.warning(
                                    f"[{self.exchange_id}] All symbols removed from WS batch — stopping",
                                    extra={"exchange": self.exchange_id},
                                )
                                return
                            consecutive_failures = 0
                            continue  # retry immediately without the bad symbol
                    # Unknown "doesn't exist" format — fall through to normal handling

                consecutive_failures += 1
                if "not supported" in msg or "does not support" in msg:
                    self._ws_ticker_supported = False
                    if not self._ws_ticker_disabled_logged:
                        self._ws_ticker_disabled_logged = True
                        logger.warning(
                            f"{self.exchange_id} watch_tickers() not supported — falling back to price polling",
                            extra={"exchange": self.exchange_id, "action": "ws_ticker_disabled"},
                        )
                    await self._price_poll_loop(symbols)
                    return

                if consecutive_failures <= 3:
                    if self._should_log_transient_error(f"price_ws_warn:{self.exchange_id}", 10.0):
                        logger.warning(
                            f"Price WebSocket error on {self.exchange_id}: {exc}",
                            extra={"exchange": self.exchange_id, "retry": consecutive_failures},
                        )
                elif consecutive_failures % 10 == 0:
                    logger.error(
                        f"Price WebSocket has failed {consecutive_failures} times in a row on "
                        f"{self.exchange_id} — bid/ask freshness may be STALE: {exc}",
                        extra={"exchange": self.exchange_id, "retry": consecutive_failures},
                    )

                if any(phrase in msg for phrase in _TIMEOUT_ERROR_PHRASES):
                    should_refresh = consecutive_failures == 3 or consecutive_failures % 5 == 0
                    if should_refresh:
                        try:
                            updated = await self._refresh_prices_via_poll_once(symbols)
                            if updated > 0 and logger.isEnabledFor(logging.DEBUG):
                                logger.debug(
                                    f"[{self.exchange_id}] REST price refresh kept {updated}/{len(symbols)} symbols fresh during WS outage",
                                    extra={"exchange": self.exchange_id, "action": "price_poll_fallback"},
                                )
                        except Exception as refresh_exc:
                            if self._should_log_transient_error(f"price_poll_refresh:{self.exchange_id}", 20.0):
                                logger.debug(
                                    f"[{self.exchange_id}] REST price refresh during WS outage failed: {refresh_exc}",
                                    extra={"exchange": self.exchange_id, "action": "price_poll_fallback_error"},
                                )

                await asyncio.sleep(min(2 ** min(consecutive_failures - 1, 5), 30))

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
                if consecutive_failures <= 3 and self._should_log_transient_error(
                    f"batch_funding_warn:{self.exchange_id}", 30.0,
                ):
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
                    if consecutive_full_failures <= 3 and self._should_log_transient_error(
                        f"sequential_funding_zero:{self.exchange_id}", 30.0,
                    ):
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
                if consecutive_full_failures <= 3 and self._should_log_transient_error(
                    f"sequential_funding_err:{self.exchange_id}", 30.0,
                ):
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
                    self._update_price_cache_from_ticker(sym, ticker, source="poll")
                    if ticker.get("markPrice") is not None or ticker.get("last") is not None:
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
