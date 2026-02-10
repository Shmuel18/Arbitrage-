"""
CCXT Pro Adapter
Concrete implementation for exchange communication
"""

from decimal import Decimal
from typing import Dict, List, Optional

from src.core.config import ExchangeConfig
from src.core.contracts import InstrumentSpec, OrderRequest, OrderSide, Position
from src.core.logging import get_logger
from src.exchanges.base import ExchangeAdapter

logger = get_logger("ccxt_adapter")


def _safe_decimal(value, default: str = "0") -> Decimal:
    if value is None:
        return Decimal(default)
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal(default)


def _precision_to_step(precision: Optional[int], default: str) -> Decimal:
    if precision is None:
        return Decimal(default)
    return Decimal("1") / (Decimal("10") ** Decimal(str(precision)))


class CCXTProAdapter(ExchangeAdapter):
    """
    Generic CCXT adapter for futures/swap exchanges
    """

    async def connect(self):
        """Initialize exchange connection"""
        if self.exchange:
            return

        self.exchange = self._create_exchange_instance()

        if self.config.testnet and hasattr(self.exchange, "set_sandbox_mode"):
            self.exchange.set_sandbox_mode(True)

        await self.exchange.load_markets()
        self._connected = True
        logger.info("Exchange connected", exchange=self.exchange_id)

    async def disconnect(self):
        """Close exchange connection"""
        if self.exchange:
            await self.exchange.close()
            self.exchange = None
            self._connected = False
            logger.info("Exchange disconnected", exchange=self.exchange_id)

    async def health_check(self) -> bool:
        """Check if exchange is accessible"""
        try:
            if self.exchange is None:
                await self.connect()
            if self.exchange and self.exchange.has.get("fetchStatus"):
                status = await self.exchange.fetch_status()
                return status.get("status") == "ok"
            await self.exchange.fetch_time()
            return True
        except Exception as e:
            logger.warning("Health check failed", exchange=self.exchange_id, error=str(e))
            return False

    async def get_instrument_spec(self, symbol: str) -> InstrumentSpec:
        """Get instrument specification"""
        if self.exchange is None:
            await self.connect()

        market = self.exchange.market(symbol)

        price_precision = market.get("precision", {}).get("price")
        amount_precision = market.get("precision", {}).get("amount")
        tick_size = _precision_to_step(price_precision, "0.01")
        step_size = _precision_to_step(amount_precision, "0.001")

        min_notional = _safe_decimal(
            market.get("limits", {}).get("cost", {}).get("min"),
            default="0"
        )

        contract_multiplier = _safe_decimal(market.get("contractSize"), default="1")
        max_leverage = market.get("limits", {}).get("leverage", {}).get("max")

        taker_fee = _safe_decimal(market.get("taker"), default="0.0006")
        maker_fee = _safe_decimal(market.get("maker"), default="0.0002")

        funding_interval = market.get("fundingInterval")
        funding_interval_hours = 8
        if isinstance(funding_interval, (int, float)):
            funding_interval_hours = int(funding_interval / 3600)

        return InstrumentSpec(
            symbol=market.get("symbol", symbol),
            exchange=self.exchange_id,
            contract_multiplier=contract_multiplier,
            tick_size=tick_size,
            step_size=step_size,
            min_notional=min_notional,
            funding_interval_hours=funding_interval_hours,
            max_leverage=max_leverage or self.config.max_leverage,
            taker_fee=taker_fee,
            maker_fee=maker_fee,
        )

    async def get_ticker(self, symbol: str) -> Dict:
        """Get current ticker"""
        if self.exchange is None:
            await self.connect()
        return await self.exchange.fetch_ticker(symbol)

    async def get_orderbook(self, symbol: str, depth: int = 10) -> Dict:
        """Get orderbook"""
        if self.exchange is None:
            await self.connect()
        return await self.exchange.fetch_order_book(symbol, limit=depth)

    async def get_funding_rate(self, symbol: str) -> Dict:
        """Get current funding rate"""
        if self.exchange is None:
            await self.connect()
        if self.exchange.has.get("fetchFundingRate"):
            return await self.exchange.fetch_funding_rate(symbol)
        return {}

    async def get_positions(self, symbol: Optional[str] = None) -> List[Position]:
        """Get open positions"""
        if self.exchange is None:
            await self.connect()
        if not self.exchange.has.get("fetchPositions"):
            return []

        if symbol:
            raw_positions = await self.exchange.fetch_positions([symbol])
        else:
            raw_positions = await self.exchange.fetch_positions()
        positions: List[Position] = []

        for raw in raw_positions:
            qty = _safe_decimal(raw.get("contracts") or raw.get("positionAmt"), default="0")
            if qty == 0:
                continue
            positions.append(
                Position(
                    exchange=self.exchange_id,
                    symbol=raw.get("symbol", symbol or ""),
                    quantity=qty,
                    entry_price=_safe_decimal(raw.get("entryPrice"), default="0"),
                    mark_price=_safe_decimal(raw.get("markPrice"), default="0"),
                    liquidation_price=_safe_decimal(raw.get("liquidationPrice"), default="0"),
                    unrealized_pnl=_safe_decimal(raw.get("unrealizedPnl"), default="0"),
                    margin_used=_safe_decimal(raw.get("initialMargin"), default="0"),
                )
            )

        return positions

    async def get_balance(self) -> Dict[str, Decimal]:
        """Get account balance"""
        if self.exchange is None:
            await self.connect()
        balance = await self.exchange.fetch_balance()
        totals = balance.get("total", {})
        return {asset: _safe_decimal(value) for asset, value in totals.items()}

    async def place_order(self, order: OrderRequest) -> Dict:
        """Place order"""
        if self.exchange is None:
            await self.connect()

        side = "buy" if order.side == OrderSide.LONG else "sell"
        order_type = "market" if order.price is None else "limit"

        return await self.exchange.create_order(
            symbol=order.symbol,
            type=order_type,
            side=side,
            amount=float(order.quantity),
            price=float(order.price) if order.price else None,
        )

    async def cancel_order(self, symbol: str, order_id: str) -> bool:
        """Cancel order"""
        if self.exchange is None:
            await self.connect()
        await self.exchange.cancel_order(order_id, symbol)
        return True

    async def get_order(self, symbol: str, order_id: str) -> Dict:
        """Get order status"""
        if self.exchange is None:
            await self.connect()
        return await self.exchange.fetch_order(order_id, symbol)

    async def watch_ticker(self, symbol: str):
        """Subscribe to ticker stream"""
        if self.exchange is None:
            await self.connect()
        return await self.exchange.watch_ticker(symbol)

    async def watch_orderbook(self, symbol: str):
        """Subscribe to orderbook stream"""
        if self.exchange is None:
            await self.connect()
        return await self.exchange.watch_order_book(symbol)

    async def watch_funding(self, symbol: str):
        """Subscribe to funding rate stream"""
        if self.exchange is None:
            await self.connect()
        if not self.exchange.has.get("watchFundingRate"):
            raise NotImplementedError("Funding rate stream not supported")
        return await self.exchange.watch_funding_rate(symbol)

    async def watch_positions(self):
        """Subscribe to position updates"""
        if self.exchange is None:
            await self.connect()
        if not self.exchange.has.get("watchPositions"):
            raise NotImplementedError("Position stream not supported")
        return await self.exchange.watch_positions()

    async def watch_orders(self):
        """Subscribe to order updates"""
        if self.exchange is None:
            await self.connect()
        if not self.exchange.has.get("watchOrders"):
            raise NotImplementedError("Order stream not supported")
        return await self.exchange.watch_orders()
