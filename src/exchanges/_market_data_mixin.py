"""Market data mixin — instruments, tickers, balances, positions, history."""

from __future__ import annotations

import asyncio
import logging
import time as _time
from decimal import Decimal
from typing import Any, Dict, List, Optional

from src.core.contracts import InstrumentSpec, OrderSide, Position
from src.core.logging import get_logger

logger = get_logger("exchanges")

_ZERO = Decimal("0")


class _MarketDataMixin:
    """Instrument specs, tickers, balances, positions, and funding history."""

    # ── Symbol resolution ────────────────────────────────────────

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

    # ── Instrument spec ──────────────────────────────────────────

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

    # ── Ticker / balance ─────────────────────────────────────────

    async def get_ticker(self, symbol: str) -> Dict[str, Any]:
        async with self._rest_semaphore:
            return await self._exchange.fetch_ticker(self._resolve_symbol(symbol))

    async def get_executable_price(
        self,
        symbol: str,
        qty: Decimal,
        side: str,  # "sell" (closing long) or "buy" (closing short)
        ob_depth: int = 20,
    ) -> Decimal:
        """Return the realistic VWAP fill price for *qty* units by walking the order book.

        Simulates a market order filling through the book level by level.
        Falls back to the Level-1 ticker bid/ask if the book fetch fails or
        if the book has insufficient depth to fill the full quantity.

        Args:
            symbol:   Normalized symbol, e.g. 'LYN/USDT:USDT'.
            qty:      Quantity to fill (positive Decimal).
            side:     'sell' consumes bids; 'buy' consumes asks.
            ob_depth: How many price levels to request from the exchange.

        Returns:
            VWAP fill price as Decimal.  Returns Level-1 price on any error.
        """
        _ZERO = Decimal("0")
        try:
            async with self._rest_semaphore:
                ob = await self._exchange.fetch_order_book(
                    self._resolve_symbol(symbol), limit=ob_depth
                )
            levels: list[list[float]] = ob["bids"] if side == "sell" else ob["asks"]
        except Exception as e:
            logger.debug(
                f"[{self.exchange_id}] {symbol} order book fetch failed: {e} — "
                "falling back to ticker"
            )
            levels = []

        if levels:
            remaining = qty
            total_cost = _ZERO
            for price_f, size_f in levels:
                if remaining <= _ZERO:
                    break
                level_price = Decimal(str(price_f))
                level_size = Decimal(str(size_f))
                fill = min(remaining, level_size)
                total_cost += fill * level_price
                remaining -= fill

            if remaining <= _ZERO:
                # Full quantity filled — return VWAP
                vwap = total_cost / qty
                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug(
                        f"[{self.exchange_id}] {symbol} executable_price({side} {qty}): "
                        f"VWAP={float(vwap):.6f} (L1={float(levels[0][0]):.6f})"
                    )
                return vwap
            else:
                # Book too shallow — log warning and fall through to ticker
                filled_pct = float((qty - remaining) / qty * 100)
                logger.warning(
                    f"[{self.exchange_id}] {symbol} order book too shallow to fill "
                    f"{float(qty)} units ({side}): only {filled_pct:.1f}% available — "
                    "falling back to ticker"
                )

        # Fallback: Level-1 ticker
        try:
            async with self._rest_semaphore:
                ticker = await self._exchange.fetch_ticker(self._resolve_symbol(symbol))
            key = "bid" if side == "sell" else "ask"
            price = ticker.get(key) or ticker.get("last") or ticker.get("close")
            if price:
                return Decimal(str(price))
        except Exception as e:
            logger.debug(
                f"[{self.exchange_id}] {symbol} ticker fallback failed: {e}"
            )

        return _ZERO

    async def get_vwap_and_depth(
        self,
        symbol: str,
        qty: Decimal,
        side: str,  # "buy" (consumes asks) or "sell" (consumes bids)
        ob_depth: int = 20,
    ) -> tuple[Decimal, bool]:
        """Walk the live order book and return (vwap_price, book_sufficient).

        book_sufficient=True  — full *qty* covered by resting orders.
        book_sufficient=False — book too shallow (or fetch failed); returned
                               price is the L1 bid/ask fallback or partial VWAP.

        Used for pre-entry liquidity gating: if False, skip entry instead of
        accepting adverse slippage on a market order.
        """
        _ZERO = Decimal("0")
        try:
            async with self._rest_semaphore:
                ob = await self._exchange.fetch_order_book(
                    self._resolve_symbol(symbol), limit=ob_depth
                )
            levels: list[list[float]] = ob["bids"] if side == "sell" else ob["asks"]
        except Exception as e:
            logger.debug(
                f"[{self.exchange_id}] {symbol} order book fetch failed "
                f"for depth check: {e}"
            )
            levels = []

        if levels:
            remaining = qty
            total_cost = _ZERO
            for price_f, size_f in levels:
                if remaining <= _ZERO:
                    break
                level_price = Decimal(str(price_f))
                level_size = Decimal(str(size_f))
                fill = min(remaining, level_size)
                total_cost += fill * level_price
                remaining -= fill

            if remaining <= _ZERO:
                # Full qty filled by book — return VWAP
                return total_cost / qty, True

            # Partial fill — book is shallow
            filled_qty = qty - remaining
            if filled_qty > _ZERO and total_cost > _ZERO:
                partial_vwap = total_cost / filled_qty
            else:
                partial_vwap = Decimal(str(levels[0][0]))
            return partial_vwap, False

        # Book fetch failed — fall back to L1 ticker (sufficient=False)
        try:
            async with self._rest_semaphore:
                ticker = await self._exchange.fetch_ticker(self._resolve_symbol(symbol))
            key = "bid" if side == "sell" else "ask"
            price = ticker.get(key) or ticker.get("last") or ticker.get("close")
            if price:
                return Decimal(str(price)), False
        except Exception as e:
            logger.debug(
                f"[{self.exchange_id}] {symbol} ticker fallback for depth check failed: {e}"
            )

        return _ZERO, False

    async def get_balance(self) -> Dict[str, Any]:
        async with self._rest_semaphore:
            bal = await self._exchange.fetch_balance()
        # Try USDT first, fall back to USD (e.g. Kraken Futures settles in USD)
        usdt = bal.get("USDT", {})
        if not usdt.get("total"):
            usdt = bal.get("USD", {})
        free_val = Decimal(str(usdt.get("free", 0) or 0))
        used_val = Decimal(str(usdt.get("used", 0) or 0))
        total_val = Decimal(str(usdt.get("total", 0) or 0))
        if total_val <= _ZERO:
            recomputed_total = free_val + used_val
            if recomputed_total > _ZERO:
                total_val = recomputed_total
        result = {
            "total": total_val,
            "free": free_val,
            "used": used_val,
        }
        # Refresh the short-TTL cache so concurrent get_balance_cached
        # calls within the next few seconds skip the REST round-trip.
        self._balance_cache = (_time.time(), result)
        return result

    async def get_balance_cached(self, max_age_sec: float = 3.0) -> Dict[str, Any]:
        """Return the most recent get_balance() result if it's < max_age_sec
        old, else fetch fresh.

        Used in the entry hot-path (sizer) to eliminate ~300-700ms of REST
        latency on every trade. The status_publisher already polls balances
        every few seconds for the dashboard, so the cache is usually warm
        by the time an entry fires. Worst case is one stale fetch — but
        the position-sizer applies a 90 % margin-safety cap downstream,
        so a slightly stale balance can only OVER-shrink the order, never
        over-expose us.
        """
        cached = getattr(self, "_balance_cache", None)
        if cached is not None:
            ts, val = cached
            if (_time.time() - ts) < max_age_sec:
                return val
        return await self.get_balance()

    # ── Funding history ──────────────────────────────────────────

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

    # ── Positions ────────────────────────────────────────────────

    async def get_positions(self, symbol: Optional[str] = None) -> List[Position]:
        symbols = [self._resolve_symbol(symbol)] if symbol else None

        # Retry up to 2 times on transient API failures (rate-limit, timeout)
        last_err: Optional[Exception] = None
        for attempt in range(3):
            try:
                await self._maybe_resync_clock()
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

    async def has_open_position(self, symbol: str) -> bool:
        """Return True if any non-zero position exists for *symbol*.

        P1-4: This abstraction replaces direct ``adapter._exchange.fetch_positions``
        access in close/retry logic.  Uses the existing ``get_positions`` path
        (rate-limit semaphore + retries + Position normalisation) instead of
        bypassing the adapter layer with a raw ccxt call.
        """
        try:
            positions = await self.get_positions(symbol)
            # P2-1: Use lot_size/2 as the minimum meaningful threshold instead of 1e-12.
            # Several exchanges (Gate.io, Bitget, OKX) return tiny ledger-rounding residuals
            # (~1e-6 to 1e-9 base units) after a close that are NOT real positions — they
            # don't consume margin and dissolve on the next settlement.  With 1e-12 every
            # normal close fires DUST DETECTED, flooding logs and drowning real alerts.
            # Using lot_size/2 means: "anything smaller than half a tradeable step is noise."
            spec = self.get_cached_instrument_spec(symbol)
            _min_threshold: float = (
                float(spec.lot_size) / 2 if (spec and spec.lot_size > 0) else 1e-6
            )
            return any(abs(float(p.quantity)) > _min_threshold for p in positions)
        except Exception as exc:
            logger.debug(
                f"has_open_position({symbol}) failed on {self.exchange_id}: {exc}"
            )
            # Default to True (conservative) — better to believe position
            # still open and retry close than to silently declare it gone.
            return True

    # ── Warm up ──────────────────────────────────────────────────

    async def warm_up_symbols(self, symbols: List[str]) -> None:
        """Pre-fetch instrument specs for all watched symbols."""
        tasks = [self.get_instrument_spec(s) for s in symbols]
        await asyncio.gather(*tasks, return_exceptions=True)
        logger.info(
            f"Warmed up {len(symbols)} symbols on {self.exchange_id}",
            extra={"exchange": self.exchange_id, "action": "warm_up"},
        )

    async def warm_up_trading_settings(self, symbols: List[str]) -> int:
        """Apply margin-mode / leverage / position-mode for ALL symbols at startup.

        Runs with bounded parallelism (semaphore) so REST rate-limits
        are respected.  Returns the count of symbols successfully configured.

        After this call, every symbol is in ``_settings_applied`` and
        ``ensure_trading_settings`` returns instantly (~0 ms) for all
        subsequent entry paths — no per-trade latency penalty.
        """
        if not symbols:
            return 0

        sem = asyncio.Semaphore(5)  # conservative — 3 REST calls per symbol
        ok_count = 0
        fail_count = 0

        async def _apply(symbol: str) -> bool:
            async with sem:
                try:
                    await self.ensure_trading_settings(symbol)
                    return True
                except Exception as exc:
                    logger.debug(
                        f"Trading settings warm-up failed for "
                        f"{self.exchange_id}/{symbol}: {exc}",
                    )
                    return False

        results = await asyncio.gather(
            *[_apply(s) for s in symbols], return_exceptions=True,
        )
        for res in results:
            if res is True:
                ok_count += 1
            else:
                fail_count += 1

        logger.info(
            f"Trading settings warm-up on {self.exchange_id}: "
            f"{ok_count}/{len(symbols)} symbols configured "
            f"(margin=cross, {fail_count} failed)",
            extra={"exchange": self.exchange_id, "action": "settings_warm_up"},
        )
        return ok_count
