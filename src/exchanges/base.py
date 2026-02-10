"""
Abstract Exchange Adapter
All exchanges must implement this interface
"""

from abc import ABC, abstractmethod
from decimal import Decimal
from typing import Dict, List, Optional

try:
    import ccxt.pro as ccxtpro
except Exception:
    import ccxt.async_support as ccxtpro

from src.core.config import ExchangeConfig
from src.core.contracts import InstrumentSpec, OrderRequest, Position
from src.core.logging import get_logger

logger = get_logger("exchange_adapter")


class ExchangeAdapter(ABC):
    """
    Abstract base class for exchange adapters
    
    All adapters MUST implement these methods
    NO business logic here - only exchange communication
    """
    
    def __init__(self, config: ExchangeConfig):
        self.config = config
        self.exchange_id = config.ccxt_id
        self.exchange: Optional[ccxtpro.Exchange] = None
        self._connected = False
    
    @abstractmethod
    async def connect(self):
        """Initialize exchange connection"""
        pass
    
    @abstractmethod
    async def disconnect(self):
        """Close exchange connection"""
        pass
    
    @abstractmethod
    async def health_check(self) -> bool:
        """Check if exchange is accessible"""
        pass
    
    @abstractmethod
    async def get_instrument_spec(self, symbol: str) -> InstrumentSpec:
        """Get instrument specification"""
        pass
    
    @abstractmethod
    async def get_ticker(self, symbol: str) -> Dict:
        """Get current ticker"""
        pass
    
    @abstractmethod
    async def get_orderbook(self, symbol: str, depth: int = 10) -> Dict:
        """Get orderbook"""
        pass
    
    @abstractmethod
    async def get_funding_rate(self, symbol: str) -> Dict:
        """Get current funding rate"""
        pass
    
    @abstractmethod
    async def get_positions(self, symbol: Optional[str] = None) -> List[Position]:
        """Get open positions"""
        pass
    
    @abstractmethod
    async def get_balance(self) -> Dict[str, Decimal]:
        """Get account balance"""
        pass
    
    @abstractmethod
    async def place_order(self, order: OrderRequest) -> Dict:
        """Place order"""
        pass
    
    @abstractmethod
    async def cancel_order(self, symbol: str, order_id: str) -> bool:
        """Cancel order"""
        pass
    
    @abstractmethod
    async def get_order(self, symbol: str, order_id: str) -> Dict:
        """Get order status"""
        pass
    
    # ==================== WEBSOCKET STREAMS ====================
    
    @abstractmethod
    async def watch_ticker(self, symbol: str):
        """Subscribe to ticker stream"""
        pass
    
    @abstractmethod
    async def watch_orderbook(self, symbol: str):
        """Subscribe to orderbook stream"""
        pass
    
    @abstractmethod
    async def watch_funding(self, symbol: str):
        """Subscribe to funding rate stream"""
        pass
    
    @abstractmethod
    async def watch_positions(self):
        """Subscribe to position updates"""
        pass
    
    @abstractmethod
    async def watch_orders(self):
        """Subscribe to order updates"""
        pass
    
    # ==================== HELPERS ====================
    
    @property
    def is_connected(self) -> bool:
        """Check if connected"""
        return self._connected
    
    def _create_exchange_instance(self) -> ccxtpro.Exchange:
        """Create CCXT Pro exchange instance"""
        exchange_class = getattr(ccxtpro, self.config.ccxt_id)
        
        exchange_config = {
            'apiKey': self.config.api_key,
            'secret': self.config.api_secret,
            'enableRateLimit': True,
            'rateLimit': self.config.rate_limit_ms,
            'options': {
                'defaultType': self.config.default_type,
            }
        }
        
        # Add passphrase for exchanges that need it (OKX)
        if self.config.api_passphrase:
            exchange_config['password'] = self.config.api_passphrase
        
        # Testnet support
        if self.config.testnet:
            exchange_config['options']['testnet'] = True
        
        return exchange_class(exchange_config)
    
    async def _handle_rate_limit(self):
        """Handle rate limiting"""
        if self.exchange:
            await self.exchange.sleep(self.config.rate_limit_ms)
    
    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(exchange={self.exchange_id}, connected={self._connected})"


class ExchangeManager:
    """
    Manages multiple exchange adapters
    """
    
    def __init__(self):
        self.adapters: Dict[str, ExchangeAdapter] = {}
    
    def register_adapter(self, exchange_id: str, adapter: ExchangeAdapter):
        """Register exchange adapter"""
        self.adapters[exchange_id] = adapter
        logger.info(f"Registered exchange adapter: {exchange_id}")
    
    def get_adapter(self, exchange_id: str) -> Optional[ExchangeAdapter]:
        """Get exchange adapter"""
        return self.adapters.get(exchange_id)
    
    async def connect_all(self):
        """Connect all registered adapters"""
        for exchange_id, adapter in self.adapters.items():
            try:
                await adapter.connect()
                logger.info(f"Connected to {exchange_id}")
            except Exception as e:
                logger.error(
                    f"Failed to connect to {exchange_id}",
                    exc_info=True,
                    error=str(e)
                )
    
    async def disconnect_all(self):
        """Disconnect all adapters"""
        for exchange_id, adapter in self.adapters.items():
            try:
                await adapter.disconnect()
                logger.info(f"Disconnected from {exchange_id}")
            except Exception as e:
                logger.error(
                    f"Error disconnecting from {exchange_id}",
                    exc_info=True
                )
    
    async def health_check_all(self) -> Dict[str, bool]:
        """Health check all exchanges"""
        results = {}
        for exchange_id, adapter in self.adapters.items():
            try:
                results[exchange_id] = await adapter.health_check()
            except Exception as e:
                logger.error(
                    f"Health check failed for {exchange_id}",
                    error=str(e)
                )
                results[exchange_id] = False
        return results
