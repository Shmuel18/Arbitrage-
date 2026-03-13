"""Funding cache mixin — cache updates, mark-price reads, interval detection.

Extracted from _funding_mixin.py to keep file size under 500 lines.
Do NOT import this module directly; _FundingMixin inherits from it.
"""

from __future__ import annotations

import asyncio
import logging
import time as _time
from decimal import Decimal
from typing import Any, Dict, Optional

from src.core.logging import get_logger

logger = get_logger("exchanges")


class _FundingCacheMixin:
    """Cache management, public accessors, and interval detection for funding rates."""

    def _update_price_cache_from_ticker(
        self,
        symbol: str,
        ticker: Dict[str, Any],
        *,
        source: str,
    ) -> None:
        """Update cached mark/last/ask/bid prices and their freshness timestamps."""
        now_ms = _time.time() * 1000
        tick_ts_raw = ticker.get("timestamp")
        tick_ts = float(tick_ts_raw) if isinstance(tick_ts_raw, (int, float)) and tick_ts_raw > 0 else now_ms

        price = ticker.get("markPrice") or ticker.get("last")
        if price is not None:
            self._price_cache[symbol] = float(price)
            self._price_timestamp_cache[symbol] = tick_ts

        ask = ticker.get("ask")
        if ask is not None:
            self._ask_cache[symbol] = float(ask)
            self._ask_timestamp_cache[symbol] = tick_ts

        bid = ticker.get("bid")
        if bid is not None:
            self._bid_cache[symbol] = float(bid)
            self._bid_timestamp_cache[symbol] = tick_ts

        # Notify hot-scan queue so the scanner can re-evaluate this symbol immediately.
        queue = getattr(self, "_price_update_queue", None)
        if queue is not None:
            try:
                queue.put_nowait((self.exchange_id, symbol))
            except asyncio.QueueFull:
                pass  # overflow: symbol will be evaluated in the next periodic scan

        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                f"[{self.exchange_id}] Cached ticker for {symbol} from {source}: "
                f"last={ticker.get('last')} ask={ticker.get('ask')} bid={ticker.get('bid')} ts={tick_ts:.0f}",
                extra={
                    "exchange": self.exchange_id,
                    "symbol": symbol,
                    "action": f"ticker_cached_{source}",
                },
            )

    def _update_funding_cache(self, symbol: str, data: dict) -> None:
        """Update in-memory cache with latest funding rate."""
        raw_rate = data.get("fundingRate")
        if raw_rate is None:
            return  # Skip symbols with no funding rate data
        try:
            rate = Decimal(str(raw_rate))
        except Exception:
            logger.debug(
                f"[{self.exchange_id}] Invalid fundingRate for {symbol}: {raw_rate!r}",
                extra={"exchange": self.exchange_id, "symbol": symbol},
            )
            return

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
            "cached_at_ms": now_ms,
            "timestamp": data.get("timestamp"),
            "datetime": data.get("datetime"),
            "next_timestamp": next_ts,
            "interval_hours": interval_hours,
            "markPrice": data.get("markPrice"),  # stored for price basis checks
            "indexPrice": data.get("indexPrice"),
        }

        # Guard f-string formatting: called on every WebSocket tick
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

    def get_mark_price_age_ms(self, symbol: str) -> Optional[float]:
        """Return age of the best mark/last price in milliseconds."""
        now_ms = _time.time() * 1000
        cached = self._funding_rate_cache.get(symbol) or {}
        if cached.get("markPrice") is not None or cached.get("indexPrice") is not None:
            cached_at_ms = cached.get("cached_at_ms")
            if cached_at_ms is None:
                return None
            return now_ms - float(cached_at_ms)
        ts = self._price_timestamp_cache.get(symbol)
        if ts is None:
            return None
        return now_ms - ts

    def get_best_ask(self, symbol: str) -> Optional[float]:
        """Return best cached ask price for symbol (no API call).

        Used by the scanner to compute a realistic long-entry price spread.
        Falls back to mark price if ask is not yet cached.
        """
        return self._ask_cache.get(symbol) or self.get_mark_price(symbol)

    def get_best_ask_age_ms(self, symbol: str) -> Optional[float]:
        """Return age of the best cached ask in milliseconds."""
        now_ms = _time.time() * 1000
        ts = self._ask_timestamp_cache.get(symbol)
        if ts is not None:
            return now_ms - ts
        return self.get_mark_price_age_ms(symbol)

    def get_best_bid(self, symbol: str) -> Optional[float]:
        """Return best cached bid price for symbol (no API call).

        Used by the scanner to compute a realistic short-entry price spread.
        Falls back to mark price if bid is not yet cached.
        """
        return self._bid_cache.get(symbol) or self.get_mark_price(symbol)

    def get_best_bid_age_ms(self, symbol: str) -> Optional[float]:
        """Return age of the best cached bid in milliseconds."""
        now_ms = _time.time() * 1000
        ts = self._bid_timestamp_cache.get(symbol)
        if ts is not None:
            return now_ms - ts
        return self.get_mark_price_age_ms(symbol)

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
        raw_rate = data.get("fundingRate")
        rate = Decimal(str(raw_rate)) if raw_rate is not None else Decimal("0")

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
