"""
Unified exchange adapter — one concrete class wrapping ccxt.pro.

No abstract base, no empty subclasses. All exchanges go through here.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional
import asyncio
import time as _time

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
        self._ws_tasks: List = []  # Track running WebSocket tasks
        self._ws_funding_supported = True
        self._ws_funding_disabled_logged = False
        self._batch_funding_supported = True  # set to False if fetchFundingRates fails

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

        # Filter to ACTIVE USDT-settled linear perpetuals only
        filtered = {
            k: v for k, v in self._exchange.markets.items()
            if v.get("swap") and v.get("linear") and v.get("settle") == "USDT"
            and v.get("active") is not False  # exclude delisted/settling markets
        }
        self._exchange.markets = filtered
        self._exchange.symbols = list(filtered.keys())

        logger.info(
            f"Connected to {self.exchange_id}",
            extra={"exchange": self.exchange_id,
                   "action": "connect",
                   "data": {"markets": len(filtered)}},
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

    async def start_funding_rate_watchers(self, symbols: List[str]) -> None:
        """Start funding rate polling — batch if supported, per-symbol otherwise."""
        eligible = [s for s in symbols if s in self._exchange.markets]
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
            task = asyncio.create_task(self._batch_funding_poll_loop(eligible))
            self._ws_tasks.append(task)
        else:
            # Batch not supported (e.g. KuCoin) — single task, sequential with semaphore
            logger.info(
                f"Starting funding rate SEQUENTIAL polling for {len(eligible)} symbols",
                extra={"exchange": self.exchange_id, "action": "ws_start"},
            )
            task = asyncio.create_task(self._sequential_funding_poll_loop(eligible))
            self._ws_tasks.append(task)

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
                data = await self._exchange.watch_funding_rate(symbol)
                self._update_funding_cache(symbol, data)
                logger.debug(
                    f"WS funding update: {symbol}",
                    extra={"exchange": self.exchange_id, "symbol": symbol},
                )
            except Exception as e:
                logger.debug(f"WebSocket funding error for {symbol}: {e}")
                raise  # Re-raise to trigger fallback/retry

    async def _watch_funding_rate_polling(self, symbol: str) -> None:
        """Fast polling fallback every 5 seconds."""
        while True:
            try:
                data = await self._exchange.fetch_funding_rate(symbol)
                self._update_funding_cache(symbol, data)
                await asyncio.sleep(5)  # Poll every 5 seconds instead of 30s scan
            except Exception as e:
                logger.debug(f"Funding poll error for {symbol}: {e}")
                await asyncio.sleep(5)

    def _update_funding_cache(self, symbol: str, data: dict) -> None:
        """Update in-memory cache with latest funding rate."""
        interval_hours = self._get_funding_interval(symbol, data)
        next_ts = data.get("fundingTimestamp")

        # If next_timestamp is in the past, advance by interval until future
        if next_ts:
            now_ms = _time.time() * 1000
            interval_ms = interval_hours * 3_600_000
            while next_ts <= now_ms and interval_ms > 0:
                next_ts += interval_ms

        self._funding_rate_cache[symbol] = {
            "rate": Decimal(str(data.get("fundingRate", 0))),
            "timestamp": data.get("timestamp"),
            "datetime": data.get("datetime"),
            "next_timestamp": next_ts,
            "interval_hours": interval_hours,
        }

    def get_funding_rate_cached(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Get latest cached funding rate (low-latency, no network call)."""
        return self._funding_rate_cache.get(symbol)

    async def warm_up_funding_rates(self, symbols: List[str] = None) -> int:
        """Batch-fetch ALL funding rates in one API call to pre-populate cache.
        Falls back to per-symbol fetch if batch not supported (e.g. KuCoin)."""
        # Try batch first
        try:
            all_rates = await self._exchange.fetch_funding_rates(symbols)
            count = 0
            for symbol, data in all_rates.items():
                if symbol in self._exchange.markets:
                    self._update_funding_cache(symbol, data)
                    count += 1
            logger.info(
                f"Warmed up {count} funding rates on {self.exchange_id}",
                extra={"exchange": self.exchange_id, "action": "funding_warm_up"},
            )
            return count
        except Exception as e:
            self._batch_funding_supported = False
            logger.warning(
                f"Batch fetch not supported on {self.exchange_id}, using per-symbol warmup",
                extra={"exchange": self.exchange_id, "action": "funding_warm_up_fallback"},
            )

        # Fallback: per-symbol fetch with concurrency limit
        if not symbols:
            symbols = list(self._exchange.markets.keys())
        sem = asyncio.Semaphore(20)
        count = 0

        async def _fetch_one(sym: str):
            nonlocal count
            async with sem:
                try:
                    data = await self._exchange.fetch_funding_rate(sym)
                    self._update_funding_cache(sym, data)
                    count += 1
                except Exception:
                    pass

        await asyncio.gather(*[_fetch_one(s) for s in symbols], return_exceptions=True)
        logger.info(
            f"Warmed up {count}/{len(symbols)} funding rates on {self.exchange_id} (per-symbol)",
            extra={"exchange": self.exchange_id, "action": "funding_warm_up"},
        )
        return count

    async def _batch_funding_poll_loop(self, symbols: List[str]) -> None:
        """Periodically fetch ALL funding rates in one batch API call."""
        poll_interval = 30  # seconds between batch refreshes
        consecutive_failures = 0
        while True:
            try:
                # Fetch without symbol filter — avoids OKX "must be same type" error
                all_rates = await self._exchange.fetch_funding_rates()
                count = 0
                for symbol, data in all_rates.items():
                    if symbol in self._exchange.markets:
                        self._update_funding_cache(symbol, data)
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
                            data = await self._exchange.fetch_funding_rate(sym)
                            self._update_funding_cache(sym, data)
                            count += 1
                        except Exception:
                            pass

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

    async def disconnect(self) -> None:
        if self._exchange:
            await self._exchange.close()
            logger.info(f"Disconnected from {self.exchange_id}",
                        extra={"exchange": self.exchange_id, "action": "disconnect"})

    # ── Trading settings ─────────────────────────────────────────

    async def ensure_trading_settings(self, symbol: str) -> None:
        """Set leverage / margin-mode / position-mode (idempotent)."""
        if symbol in self._settings_applied:
            return
        ex = self._exchange
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
                await ex.set_margin_mode(margin, symbol, mode_params)
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
                await ex.set_leverage(lev, symbol, lev_params)
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
                await ex.set_position_mode(hedged, symbol)
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

        mkt = self._exchange.market(symbol)
        if not mkt:
            return None

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
            taker_fee=Decimal(str(mkt.get("taker", 0) or 0)),
        )
        self._instrument_cache[symbol] = spec
        return spec

    async def get_ticker(self, symbol: str) -> Dict[str, Any]:
        return await self._exchange.fetch_ticker(symbol)

    async def get_funding_rate(self, symbol: str) -> Dict[str, Any]:
        data = await self._exchange.fetch_funding_rate(symbol)
        interval_hours = self._get_funding_interval(symbol, data)
        next_ts = data.get("fundingTimestamp")

        # If next_timestamp is in the past, advance by interval until future
        if next_ts:
            now_ms = _time.time() * 1000
            interval_ms = interval_hours * 3_600_000
            while next_ts <= now_ms and interval_ms > 0:
                next_ts += interval_ms

        return {
            "rate": Decimal(str(data.get("fundingRate", 0))),
            "timestamp": data.get("timestamp"),
            "datetime": data.get("datetime"),
            "next_timestamp": next_ts,
            "interval_hours": interval_hours,
        }

    def _get_funding_interval(self, symbol: str, funding_data: dict) -> int:
        """Detect funding interval in hours from CCXT data."""
        # 1) CCXT normalized 'interval' field (e.g. '1h', '4h', '8h')
        interval_str = funding_data.get("interval") or ""
        if interval_str:
            try:
                return int(interval_str.replace("h", ""))
            except ValueError:
                pass

        # 2) Market info (Bybit provides fundingInterval in minutes)
        mkt = self._exchange.market(symbol) if symbol in self._exchange.markets else None
        if mkt:
            info = mkt.get("info", {})
            # Bybit: fundingInterval (minutes)
            fi_min = info.get("fundingInterval")
            if fi_min:
                try:
                    return max(1, int(fi_min) // 60)
                except (ValueError, TypeError):
                    pass

        # 3) Default 8h
        return 8

    # ── Account ──────────────────────────────────────────────────

    async def get_balance(self) -> Dict[str, Any]:
        bal = await self._exchange.fetch_balance()
        usdt = bal.get("USDT", {})
        return {
            "total": Decimal(str(usdt.get("total", 0) or 0)),
            "free":  Decimal(str(usdt.get("free", 0) or 0)),
            "used":  Decimal(str(usdt.get("used", 0) or 0)),
        }

    async def get_positions(self, symbol: Optional[str] = None) -> List[Position]:
        symbols = [symbol] if symbol else None
        raw = await self._exchange.fetch_positions(symbols)
        positions: List[Position] = []
        for p in raw:
            amt = float(p.get("contracts", 0) or 0)
            if abs(amt) < 1e-12:
                continue

            # Convert from contracts to base currency (tokens)
            sym = p["symbol"]
            mkt = self._exchange.market(sym) if sym in self._exchange.markets else None
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

    # ── Order execution ──────────────────────────────────────────

    async def place_order(self, req: OrderRequest) -> Dict[str, Any]:
        """Place a market order. Returns the ccxt order dict."""
        await self.ensure_trading_settings(req.symbol)

        params: Dict[str, Any] = {}
        if req.reduce_only:
            params["reduceOnly"] = True

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

        order = await self._exchange.create_order(
            symbol=req.symbol,
            type="market",
            side=req.side.value,
            amount=native_qty,
            params=params,
        )

        # Convert filled amount BACK to base currency (tokens) for the caller
        filled_native = float(order.get("filled", 0) or 0)
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

    def all(self) -> Dict[str, ExchangeAdapter]:
        return dict(self._adapters)

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
            except Exception:
                pass
