"""
Data Normalization Engine
Converts exchange-specific data to standard format
"""

from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Optional

from src.core.contracts import (
    InstrumentSpec,
    OrderbookLevel,
    StandardMarketEvent,
)
from src.core.logging import get_logger

logger = get_logger("normalizer")


class DataNormalizer:
    """
    Normalizes exchange-specific data formats to standard contracts
    """
    
    def __init__(self):
        self._instrument_specs: Dict[str, InstrumentSpec] = {}
    
    def register_instrument(self, spec: InstrumentSpec):
        """Register instrument specification"""
        key = f"{spec.exchange}:{spec.symbol}"
        self._instrument_specs[key] = spec
        logger.debug(
            f"Registered instrument",
            exchange=spec.exchange,
            symbol=spec.symbol,
            multiplier=float(spec.contract_multiplier)
        )
    
    def get_instrument(self, exchange: str, symbol: str) -> Optional[InstrumentSpec]:
        """Get instrument specification"""
        key = f"{exchange}:{symbol}"
        return self._instrument_specs.get(key)
    
    def normalize_symbol(self, exchange: str, symbol: str) -> str:
        """
        Normalize symbol to internal format
        Example: BTCUSDT -> BTC/USDT
        """
        # Already in standard format
        if "/" in symbol:
            return symbol
        
        # Common patterns
        if symbol.endswith("USDT"):
            base = symbol[:-4]
            return f"{base}/USDT"
        elif symbol.endswith("USD"):
            base = symbol[:-3]
            return f"{base}/USD"
        
        return symbol
    
    def normalize_ticker(
        self,
        exchange: str,
        raw_ticker: Dict
    ) -> Optional[StandardMarketEvent]:
        """
        Normalize ticker data
        
        CCXT ticker format:
        {
            'symbol': 'BTC/USDT',
            'timestamp': 1234567890,
            'bid': 50000.0,
            'ask': 50001.0,
            'last': 50000.5,
            'info': {...}  # Exchange-specific
        }
        """
        try:
            symbol = self.normalize_symbol(exchange, raw_ticker.get('symbol', ''))
            
            # Extract prices
            bid = Decimal(str(raw_ticker.get('bid', 0)))
            ask = Decimal(str(raw_ticker.get('ask', 0)))
            last = Decimal(str(raw_ticker.get('last', 0)))
            
            # Use last as mark if no mark price
            mark = Decimal(str(raw_ticker.get('markPrice', last)))
            
            timestamp = datetime.utcfromtimestamp(
                raw_ticker.get('timestamp', 0) / 1000
            )
            
            # For now, create minimal event (will be enriched with funding/orderbook)
            return StandardMarketEvent(
                symbol_internal=symbol,
                exchange=exchange,
                timestamp=timestamp,
                bid=bid,
                ask=ask,
                mark_price=mark,
                funding_rate=Decimal('0'),  # Will be updated
                funding_timestamp=timestamp,
                next_funding=timestamp,
                bids=[],  # Will be updated
                asks=[]
            )
            
        except Exception as e:
            logger.error(
                f"Failed to normalize ticker",
                exchange=exchange,
                error=str(e),
                exc_info=True
            )
            return None
    
    def normalize_orderbook(
        self,
        exchange: str,
        symbol: str,
        raw_orderbook: Dict
    ) -> tuple[List[OrderbookLevel], List[OrderbookLevel]]:
        """
        Normalize orderbook data
        
        CCXT orderbook format:
        {
            'bids': [[price, quantity], ...],
            'asks': [[price, quantity], ...],
            'timestamp': 1234567890
        }
        """
        try:
            bids = []
            asks = []
            
            # Process bids
            for price, qty in raw_orderbook.get('bids', []):
                bids.append(OrderbookLevel(
                    price=Decimal(str(price)),
                    quantity=Decimal(str(qty))
                ))
            
            # Process asks
            for price, qty in raw_orderbook.get('asks', []):
                asks.append(OrderbookLevel(
                    price=Decimal(str(price)),
                    quantity=Decimal(str(qty))
                ))
            
            return bids, asks
            
        except Exception as e:
            logger.error(
                f"Failed to normalize orderbook",
                exchange=exchange,
                symbol=symbol,
                error=str(e),
                exc_info=True
            )
            return [], []
    
    def normalize_funding_rate(
        self,
        exchange: str,
        symbol: str,
        raw_funding: Dict
    ) -> tuple[Decimal, datetime, datetime]:
        """
        Normalize funding rate data
        
        CCXT funding rate format:
        {
            'symbol': 'BTC/USDT',
            'fundingRate': 0.0001,
            'fundingTimestamp': 1234567890,
            'fundingDatetime': '2024-01-01T00:00:00.000Z'
        }
        """
        try:
            rate = Decimal(str(raw_funding.get('fundingRate', 0)))
            
            timestamp = datetime.utcfromtimestamp(
                raw_funding.get('fundingTimestamp', 0) / 1000
            )
            
            # Calculate next funding (usually 8 hours)
            spec = self.get_instrument(exchange, symbol)
            interval_hours = spec.funding_interval_hours if spec else 8
            
            next_funding = datetime.utcfromtimestamp(
                (raw_funding.get('fundingTimestamp', 0) / 1000) +
                (interval_hours * 3600)
            )
            
            return rate, timestamp, next_funding
            
        except Exception as e:
            logger.error(
                f"Failed to normalize funding rate",
                exchange=exchange,
                symbol=symbol,
                error=str(e),
                exc_info=True
            )
            return Decimal('0'), datetime.utcnow(), datetime.utcnow()
    
    def normalize_position(self, exchange: str, raw_position: Dict) -> Dict:
        """
        Normalize position data
        
        CCXT position format varies by exchange
        """
        try:
            symbol = self.normalize_symbol(exchange, raw_position.get('symbol', ''))
            
            # Contracts (can be negative for short)
            contracts = Decimal(str(raw_position.get('contracts', 0)))
            
            # Entry price
            entry_price = Decimal(str(raw_position.get('entryPrice', 0)))
            
            # Mark price
            mark_price = Decimal(str(raw_position.get('markPrice', 0)))
            
            # Liquidation price
            liq_price = raw_position.get('liquidationPrice')
            liq_price = Decimal(str(liq_price)) if liq_price else None
            
            # Unrealized PnL
            upnl = Decimal(str(raw_position.get('unrealizedPnl', 0)))
            
            # Margin
            margin = Decimal(str(raw_position.get('initialMargin', 0)))
            
            # Side
            side = raw_position.get('side', 'long')
            
            return {
                'exchange': exchange,
                'symbol': symbol,
                'quantity': contracts if side == 'long' else -contracts,
                'entry_price': entry_price,
                'mark_price': mark_price,
                'liquidation_price': liq_price,
                'unrealized_pnl': upnl,
                'margin_used': margin,
                'timestamp': datetime.utcnow()
            }
            
        except Exception as e:
            logger.error(
                f"Failed to normalize position",
                exchange=exchange,
                error=str(e),
                exc_info=True
            )
            return {}
    
    def normalize_order(self, exchange: str, raw_order: Dict) -> Dict:
        """
        Normalize order data
        
        CCXT order format:
        {
            'id': '12345',
            'symbol': 'BTC/USDT',
            'type': 'limit',
            'side': 'buy',
            'price': 50000.0,
            'amount': 0.1,
            'filled': 0.05,
            'remaining': 0.05,
            'status': 'open',
            'timestamp': 1234567890,
            'fee': {'cost': 1.0, 'currency': 'USDT'}
        }
        """
        try:
            symbol = self.normalize_symbol(exchange, raw_order.get('symbol', ''))
            
            return {
                'exchange_order_id': raw_order.get('id'),
                'symbol': symbol,
                'side': raw_order.get('side'),
                'type': raw_order.get('type'),
                'price': Decimal(str(raw_order.get('price', 0))),
                'quantity': Decimal(str(raw_order.get('amount', 0))),
                'filled': Decimal(str(raw_order.get('filled', 0))),
                'remaining': Decimal(str(raw_order.get('remaining', 0))),
                'status': raw_order.get('status'),
                'timestamp': datetime.utcfromtimestamp(
                    raw_order.get('timestamp', 0) / 1000
                ),
                'fee': Decimal(str(raw_order.get('fee', {}).get('cost', 0))),
                'fee_currency': raw_order.get('fee', {}).get('currency', 'USDT')
            }
            
        except Exception as e:
            logger.error(
                f"Failed to normalize order",
                exchange=exchange,
                error=str(e),
                exc_info=True
            )
            return {}
    
    def create_market_event(
        self,
        exchange: str,
        symbol: str,
        bid: Decimal,
        ask: Decimal,
        mark: Decimal,
        funding_rate: Decimal,
        funding_ts: datetime,
        next_funding: datetime,
        bids: List[OrderbookLevel],
        asks: List[OrderbookLevel]
    ) -> StandardMarketEvent:
        """Create complete market event"""
        return StandardMarketEvent(
            symbol_internal=symbol,
            exchange=exchange,
            timestamp=datetime.utcnow(),
            bid=bid,
            ask=ask,
            mark_price=mark,
            funding_rate=funding_rate,
            funding_timestamp=funding_ts,
            next_funding=next_funding,
            bids=bids,
            asks=asks
        )
    
    def calculate_orderbook_depth_usd(
        self,
        levels: List[OrderbookLevel],
        max_levels: int = 10
    ) -> Decimal:
        """Calculate USD depth in orderbook"""
        depth = Decimal('0')
        for level in levels[:max_levels]:
            depth += level.notional
        return depth
    
    def validate_market_event(self, event: StandardMarketEvent) -> bool:
        """Validate market event sanity"""
        if not event.is_healthy:
            logger.warning(
                f"Unhealthy market event",
                exchange=event.exchange,
                symbol=event.symbol_internal,
                bid=float(event.bid),
                ask=float(event.ask),
                spread_bps=float(event.spread_bps)
            )
            return False
        
        return True
