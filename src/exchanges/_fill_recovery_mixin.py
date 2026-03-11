"""Fill verification and trade recovery mixin.

Extracted from _order_mixin.py to keep file size under 500 lines.
Do NOT import this module directly; _OrderMixin inherits from it.
"""

from __future__ import annotations

import time as _time
from decimal import Decimal
from typing import Dict, Optional

from src.core.contracts import OrderRequest, OrderSide
from src.core.logging import get_logger

logger = get_logger("exchanges")


class _FillRecoveryMixin:
    """Fill verification via position check and trade history APIs."""

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

    async def fetch_fill_price_from_trades(
        self, symbol: str, order_id: Optional[str] = None,
    ) -> Optional[Decimal]:
        """Recover average fill price via fetchMyTrades when fetchOrder returns null.

        Calls the exchange's ``fetchMyTrades`` endpoint and filters for trades
        matching *order_id*.  Falls back to the most-recent trade on the symbol
        if no order_id match is found (e.g. some exchanges do not tag order ids).

        Returns the volume-weighted average price as ``Decimal``, or ``None``
        if no trades can be retrieved.
        """
        resolved = self._resolve_symbol(symbol)
        try:
            # Fetch the last 20 trades on this symbol (covers any recent fill)
            raw_trades = await self._exchange.fetch_my_trades(
                resolved, limit=20,
            )
        except Exception as exc:
            logger.warning(
                f"fetchMyTrades failed on {self.exchange_id}/{symbol}: {exc}",
                extra={"exchange": self.exchange_id, "symbol": symbol},
            )
            return None

        if not raw_trades:
            return None

        # Try to match by order_id first
        matched = [
            t for t in raw_trades
            if order_id and t.get("order") == order_id
        ] if order_id else []

        if not matched:
            # Fallback: use the single most-recent trade (within last 30s)
            most_recent = raw_trades[-1]
            trade_ts = most_recent.get("timestamp") or 0
            now_ms = int(_time.time() * 1000)
            if now_ms - trade_ts > 30_000:
                logger.debug(
                    f"[{self.exchange_id}/{symbol}] Most recent trade is "
                    f"{(now_ms - trade_ts) / 1000:.0f}s old — too stale for fallback",
                )
                return None
            matched = [most_recent]

        # Volume-weighted average price
        total_cost = Decimal("0")
        total_qty = Decimal("0")
        for t in matched:
            price = Decimal(str(t.get("price", 0) or 0))
            amount = Decimal(str(t.get("amount", 0) or 0))
            if price > 0 and amount > 0:
                total_cost += price * amount
                total_qty += amount

        if total_qty <= 0:
            return None

        vwap = total_cost / total_qty
        logger.info(
            f"[{self.exchange_id}/{symbol}] Fill price recovered from trades API: "
            f"{vwap} (from {len(matched)} trade(s), order_id={order_id})",
            extra={"exchange": self.exchange_id, "symbol": symbol,
                   "action": "fill_price_from_trades"},
        )
        return vwap

    async def fetch_fill_details_from_trades(
        self, symbol: str, order_id: Optional[str] = None,
    ) -> Optional[Dict[str, Decimal]]:
        """Fetch actual fill details (price + fees) from myTrades API.

        Returns dict with:
            - avg_price: Decimal (volume-weighted average fill price)
            - total_fee: Decimal (sum of all fill fees, converted to USDT)
            - filled: Decimal (total filled quantity in base currency)
        Or ``None`` if no matching trades are found.
        """
        resolved = self._resolve_symbol(symbol)
        try:
            async with self._rest_semaphore:
                raw_trades = await self._exchange.fetch_my_trades(
                    resolved, limit=20,
                )
        except Exception as exc:
            logger.warning(
                f"fetchMyTrades (details) failed on {self.exchange_id}/{symbol}: {exc}",
                extra={"exchange": self.exchange_id, "symbol": symbol},
            )
            return None

        if not raw_trades:
            return None

        # Match by order_id first
        matched = [
            t for t in raw_trades
            if order_id and t.get("order") == order_id
        ] if order_id else []

        if not matched:
            # Fallback: most recent trade within 30s
            most_recent = raw_trades[-1]
            trade_ts = most_recent.get("timestamp") or 0
            now_ms = int(_time.time() * 1000)
            if now_ms - trade_ts > 30_000:
                return None
            matched = [most_recent]

        # Compute VWAP and total fees
        total_cost = Decimal("0")
        total_qty = Decimal("0")
        total_fee = Decimal("0")

        for t in matched:
            qty = Decimal(str(t.get("amount", 0) or 0))
            price = Decimal(str(t.get("price", 0) or 0))
            if qty <= 0 or price <= 0:
                continue
            total_cost += qty * price
            total_qty += qty

            # Extract fee — convert base-currency fees to USDT
            fee_info = t.get("fee")
            if isinstance(fee_info, dict) and fee_info.get("cost") is not None:
                fee_cost = Decimal(str(fee_info["cost"]))
                fee_currency = (fee_info.get("currency") or "").upper()
                if fee_currency and fee_currency not in (
                    "USDT", "BUSD", "USDC", "USD",
                ):
                    # Fee in base asset — convert using fill price
                    fee_cost = abs(fee_cost) * price
                else:
                    fee_cost = abs(fee_cost)
                total_fee += fee_cost

        if total_qty <= 0:
            return None

        vwap = total_cost / total_qty
        logger.info(
            f"[{self.exchange_id}/{symbol}] Fill details from trades API: "
            f"price={vwap:.6f}  fee=${float(total_fee):.6f}  "
            f"qty={total_qty} ({len(matched)} fill(s), order_id={order_id})",
            extra={"exchange": self.exchange_id, "symbol": symbol,
                   "action": "fill_details_from_trades"},
        )
        return {
            "avg_price": vwap,
            "total_fee": total_fee,
            "filled": total_qty,
        }

    async def check_timed_out_fill(self, req: OrderRequest) -> float:
        """Check whether an entry order filled on the exchange despite a client-side timeout.

        Market orders cannot be cancelled once submitted — the exchange may have
        executed the order while the client was still waiting for the response.
        This method detects that scenario so the caller can immediately orphan-close
        the position instead of leaving a naked unhedged leg open.

        Only meaningful for ENTRY orders (reduce_only=False).
        Returns filled quantity in BASE currency (tokens), 0.0 if not filled.
        """
        try:
            spec = await self.get_instrument_spec(req.symbol)
            contract_size = float(spec.contract_size) if spec and spec.contract_size else 1.0
            base_qty = float(req.quantity)
            native_qty = base_qty / contract_size if contract_size > 0 else base_qty

            resolved = self._resolve_symbol(req.symbol)
            filled_native = await self._verify_fill_via_position(
                req.symbol, resolved, req.side, native_qty,
                order_id=None, reduce_only=False,
            )
            return filled_native * contract_size
        except Exception as exc:
            logger.warning(
                f"check_timed_out_fill failed on {self.exchange_id}/{req.symbol}: {exc}",
                extra={"exchange": self.exchange_id, "symbol": req.symbol},
            )
            return 0.0
