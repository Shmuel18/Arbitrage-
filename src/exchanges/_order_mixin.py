"""Order execution mixin — place orders and ensure trading settings.

Fill verification and trade recovery methods are in _fill_recovery_mixin.py.
Do NOT import this module directly; ExchangeAdapter uses _OrderMixin.
"""

from __future__ import annotations

import asyncio
import logging
import time as _time
from decimal import Decimal
from typing import Any, Dict, List, Optional

from src.core.contracts import OrderRequest, OrderSide
from src.core.logging import get_logger
from src.exchanges._fill_recovery_mixin import _FillRecoveryMixin

logger = get_logger("exchanges")


class _OrderMixin(_FillRecoveryMixin):
    """Order placement, fill verification, and trading settings."""

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
                       "not modified", "no changes", "no_change", "same", "is not supported")
        # 1) Set margin mode FIRST — OKX requires this before leverage
        try:
            if hasattr(ex, "set_margin_mode"):
                mode_params = {"lever": str(lev)} if self.exchange_id == "okx" else {}
                await ex.set_margin_mode(margin, native_sym, mode_params)
                logger.debug(f"{self.exchange_id} {symbol}: margin mode → {margin}",
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
                logger.debug(f"{self.exchange_id} {symbol}: leverage → {lev}x",
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

        logger.debug(
            f"Applied settings on {self.exchange_id} {symbol}: lev={lev} margin={margin} pos={pos_mode}",
            extra={"exchange": self.exchange_id, "symbol": symbol, "action": "settings_applied"},
        )
        self._settings_applied.add(symbol)

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

            if req.reduce_only:
                # For CLOSE orders: skip slow fetchOrder retries and verify
                # via position presence immediately — saves ~3s vs 3 re-fetches
                # that often fail on kucoin/bitget anyway.
                await asyncio.sleep(0.5)  # brief settle
                filled_native = await self._verify_fill_via_position(
                    req.symbol, _resolved, req.side, native_qty, order.get("id"),
                    reduce_only=True,
                )
                if filled_native > 0:
                    order["filled"] = filled_native
                    # Recover avg price from trades API if missing
                    if order.get("average") is None:
                        _recovered = await self.fetch_fill_price_from_trades(
                            req.symbol, order.get("id"),
                        )
                        if _recovered is not None:
                            order["average"] = float(_recovered)
                    else:
                        order["average"] = order.get("average")
            else:
                # For ENTRY orders: fetchOrder retries are worth the wait
                _fetch_params: Dict[str, Any] = {}
                if self.exchange_id == "bybit":
                    _fetch_params["acknowledged"] = True
                for _attempt in range(1, 4):            # 3 attempts, 1s apart
                    try:
                        await asyncio.sleep(1)
                        updated = await self._exchange.fetch_order(
                            order["id"], _resolved, _fetch_params,
                        )
                        if updated is None:
                            logger.warning(
                                f"Order re-fetch attempt {_attempt} returned None on "
                                f"{self.exchange_id}/{req.symbol}",
                                extra={"exchange": self.exchange_id, "symbol": req.symbol},
                            )
                            continue
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
                    # Fall back to checking actual positions to confirm fill.
                    filled_native = await self._verify_fill_via_position(
                        req.symbol, _resolved, req.side, native_qty, order.get("id"),
                        reduce_only=req.reduce_only,
                    )
                    if filled_native > 0:
                        order["filled"] = filled_native
                        # Recover avg price from trades API if missing
                        if order.get("average") is None:
                            _recovered = await self.fetch_fill_price_from_trades(
                                req.symbol, order.get("id"),
                            )
                            if _recovered is not None:
                                order["average"] = float(_recovered)
                        else:
                            order["average"] = order.get("average")

        # Final fallback: if filled > 0 but still no avg price, try trades API
        filled_native_check = float(order.get("filled", 0) or 0)
        if filled_native_check > 0 and order.get("average") is None:
            _recovered = await self.fetch_fill_price_from_trades(
                req.symbol, order.get("id"),
            )
            if _recovered is not None:
                order["average"] = float(_recovered)

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
