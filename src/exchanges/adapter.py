"""
Unified exchange adapter — one concrete class wrapping ccxt.pro.

No abstract base, no empty subclasses. All exchanges go through here.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

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

    # ── Lifecycle ────────────────────────────────────────────────

    async def connect(self) -> None:
        cls = getattr(ccxtpro, self._cfg.get("ccxt_id", self.exchange_id))
        opts: Dict[str, Any] = {
            "apiKey": self._cfg.get("api_key"),
            "secret": self._cfg.get("api_secret"),
            "enableRateLimit": True,
            "options": {"defaultType": self._cfg.get("default_type", "swap")},
        }
        if pw := self._cfg.get("api_passphrase"):
            opts["password"] = pw
        if self._cfg.get("testnet"):
            opts["sandbox"] = True

        self._exchange = cls(opts)
        await self._exchange.load_markets()

        # Filter to USDT-settled linear perpetuals only
        filtered = {
            k: v for k, v in self._exchange.markets.items()
            if v.get("swap") and v.get("linear") and v.get("settle") == "USDT"
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
        lev = self._cfg.get("leverage", 1)
        margin = self._cfg.get("margin_mode", "cross")
        pos_mode = self._cfg.get("position_mode", "oneway")

        try:
            if hasattr(ex, "set_leverage"):
                await ex.set_leverage(lev, symbol)
            if hasattr(ex, "set_margin_mode"):
                await ex.set_margin_mode(margin, symbol)
            if hasattr(ex, "set_position_mode"):
                hedged = (pos_mode == "hedged")
                await ex.set_position_mode(hedged, symbol)
            self._settings_applied.add(symbol)
        except Exception as e:
            # Many exchanges reject duplicate calls — that's fine.
            msg = str(e)
            if "No need to change" not in msg and "leverage not modified" not in msg:
                logger.warning(f"Trading settings issue on {self.exchange_id}: {e}",
                               extra={"exchange": self.exchange_id, "symbol": symbol})

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
        return {
            "rate": Decimal(str(data.get("fundingRate", 0))),
            "timestamp": data.get("timestamp"),
            "datetime": data.get("datetime"),
            "next_timestamp": data.get("fundingTimestamp"),
            "interval_hours": self._get_funding_interval(symbol, data),
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

            side_raw = (p.get("side") or "").lower()
            if side_raw in ("long", "buy"):
                side = OrderSide.BUY
            elif side_raw in ("short", "sell"):
                side = OrderSide.SELL
            else:
                side = OrderSide.BUY if amt > 0 else OrderSide.SELL

            positions.append(Position(
                exchange=self.exchange_id,
                symbol=p["symbol"],
                side=side,
                quantity=Decimal(str(abs(amt))),
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

        # Normalize quantity
        spec = await self.get_instrument_spec(req.symbol)
        quantity = float(req.quantity)
        if spec and float(spec.lot_size) > 0:
            lot = float(spec.lot_size)
            quantity = round(quantity / lot) * lot
            quantity = max(quantity, lot)

        order = await self._exchange.create_order(
            symbol=req.symbol,
            type="market",
            side=req.side.value,
            amount=quantity,
            params=params,
        )

        logger.info(
            f"Order placed on {self.exchange_id}: {req.side.value} {quantity} {req.symbol}",
            extra={
                "exchange": self.exchange_id,
                "symbol": req.symbol,
                "action": "order_placed",
                "data": {
                    "order_id": order.get("id"),
                    "side": req.side.value,
                    "qty": quantity,
                    "reduce_only": req.reduce_only,
                    "filled": order.get("filled"),
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
