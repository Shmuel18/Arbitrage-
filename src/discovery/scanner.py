"""
Discovery Scanner
Continuously scans for funding arbitrage opportunities
"""

import asyncio
from decimal import Decimal
from typing import Dict, List, Optional

from src.core.config import get_config
from src.core.contracts import OrderSide
from src.core.logging import get_logger
from src.discovery.calculator import WorstCaseCalculator

logger = get_logger("discovery_scanner")


class OpportunityEvent:
    """Discovered arbitrage opportunity"""
    
    def __init__(
        self,
        symbol: str,
        long_exchange: str,
        short_exchange: str,
        net_bps: Decimal,
        notional_usd: Decimal,
        long_price: Decimal,
        short_price: Decimal,
        long_funding: Decimal,
        short_funding: Decimal
    ):
        self.symbol = symbol
        self.long_exchange = long_exchange
        self.short_exchange = short_exchange
        self.net_bps = net_bps
        self.notional_usd = notional_usd
        self.long_price = long_price
        self.short_price = short_price
        self.long_funding = long_funding
        self.short_funding = short_funding


class DiscoveryScanner:
    """
    Continuously scans exchanges for funding arbitrage opportunities
    """
    
    def __init__(self, exchange_manager, execution_controller):
        self.config = get_config()
        self.exchange_manager = exchange_manager
        self.execution_controller = execution_controller
        self.calculator = WorstCaseCalculator()
        
        self._running = False
        self._scan_task = None
        
        self.scan_interval_sec = 10  # Scan every 10 seconds
        self.min_net_bps = self.config.trading.min_net_bps
    
    async def start(self):
        """Start the discovery scanner"""
        if self._running:
            return
        
        self._running = True
        self._scan_task = asyncio.create_task(self._scan_loop())
        logger.info("Discovery scanner started", interval_sec=self.scan_interval_sec)
    
    async def stop(self):
        """Stop the discovery scanner"""
        self._running = False
        
        if self._scan_task:
            self._scan_task.cancel()
            try:
                await self._scan_task
            except asyncio.CancelledError:
                pass
        
        logger.info("Discovery scanner stopped")
    
    async def _scan_loop(self):
        """Main scanning loop"""
        while self._running:
            try:
                await self._scan_once()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error in scan loop", error=str(e), exc_info=True)
            
            try:
                await asyncio.sleep(self.scan_interval_sec)
            except asyncio.CancelledError:
                break
    
    async def _scan_once(self):
        """Single scan iteration"""
        
        # Get all symbols from watchlist
        symbols = self.config.watchlist
        
        if not symbols:
            logger.warning("Empty watchlist, nothing to scan")
            return
        
        # Get all exchange IDs
        exchange_ids = list(self.exchange_manager.adapters.keys())
        
        if len(exchange_ids) < 2:
            logger.warning("Need at least 2 exchanges for arbitrage")
            return
        
        opportunities = []
        
        # Scan each symbol across all exchange pairs
        for symbol in symbols:
            try:
                symbol_opportunities = await self._scan_symbol(symbol, exchange_ids)
                opportunities.extend(symbol_opportunities)
            except Exception as e:
                logger.warning("Error scanning symbol", symbol=symbol, error=str(e))
        
        # Log scan results
        if opportunities:
            logger.info(
                "Scan complete",
                symbols_scanned=len(symbols),
                opportunities_found=len(opportunities),
                best_bps=max(opp.net_bps for opp in opportunities)
            )
            
            # Execute best opportunities
            await self._execute_opportunities(opportunities)
        else:
            logger.debug("Scan complete, no opportunities found", symbols_scanned=len(symbols))
    
    async def _scan_symbol(self, symbol: str, exchange_ids: List[str]) -> List[OpportunityEvent]:
        """Scan a single symbol across all exchange pairs"""
        
        opportunities = []
        
        # Fetch funding rates and prices from all exchanges
        exchange_data = {}
        
        for exchange_id in exchange_ids:
            adapter = self.exchange_manager.get_adapter(exchange_id)
            if not adapter:
                continue
            
            try:
                # Get instrument spec (contains funding rate)
                spec = await adapter.get_instrument_spec(symbol)
                
                # Get current price (use mark price for funding calculations)
                # For now we'll use a simple approach - fetch ticker
                if adapter.exchange and hasattr(adapter.exchange, 'fetch_ticker'):
                    ticker = await adapter.exchange.fetch_ticker(symbol)
                    mark_price = Decimal(str(ticker.get('last', 0)))
                else:
                    mark_price = Decimal('0')
                
                if mark_price <= 0:
                    continue
                
                exchange_data[exchange_id] = {
                    'spec': spec,
                    'funding_rate': spec.funding_rate,
                    'mark_price': mark_price
                }
                
            except Exception as e:
                logger.debug(
                    "Failed to fetch data",
                    exchange=exchange_id,
                    symbol=symbol,
                    error=str(e)
                )
        
        # Need at least 2 exchanges with data
        if len(exchange_data) < 2:
            return opportunities
        
        # Compare all pairs
        exchange_list = list(exchange_data.keys())
        
        for i, exchange_a in enumerate(exchange_list):
            for exchange_b in exchange_list[i+1:]:
                
                data_a = exchange_data[exchange_a]
                data_b = exchange_data[exchange_b]
                
                # Try both directions
                for long_ex, short_ex in [(exchange_a, exchange_b), (exchange_b, exchange_a)]:
                    long_data = exchange_data[long_ex]
                    short_data = exchange_data[short_ex]
                    
                    try:
                        opportunity = self._evaluate_pair(
                            symbol=symbol,
                            long_exchange=long_ex,
                            short_exchange=short_ex,
                            long_data=long_data,
                            short_data=short_data
                        )
                        
                        if opportunity and opportunity.net_bps >= self.min_net_bps:
                            opportunities.append(opportunity)
                            
                    except Exception as e:
                        logger.debug(
                            "Error evaluating pair",
                            symbol=symbol,
                            long=long_ex,
                            short=short_ex,
                            error=str(e)
                        )
        
        return opportunities
    
    def _evaluate_pair(
        self,
        symbol: str,
        long_exchange: str,
        short_exchange: str,
        long_data: dict,
        short_data: dict
    ) -> Optional[OpportunityEvent]:
        """Evaluate a specific exchange pair for arbitrage"""
        
        long_spec = long_data['spec']
        short_spec = short_data['spec']
        long_funding = long_data['funding_rate']
        short_funding = short_data['funding_rate']
        long_price = long_data['mark_price']
        short_price = short_data['mark_price']
        
        # Calculate funding edge
        funding_edge_bps = self.calculator.calculate_funding_edge(
            funding_rate_long=long_funding,
            funding_rate_short=short_funding,
            funding_hours=long_spec.funding_interval_hours
        )
        
        # Calculate costs
        total_cost_bps = self.calculator.calculate_total_cost(
            long_spec=long_spec,
            short_spec=short_spec,
            long_price=long_price,
            short_price=short_price
        )
        
        # Net profit
        net_bps = funding_edge_bps - total_cost_bps
        
        # Use configured position size
        notional_usd = Decimal(str(self.config.risk.max_position_size_usd))
        
        if net_bps < self.min_net_bps:
            return None
        
        return OpportunityEvent(
            symbol=symbol,
            long_exchange=long_exchange,
            short_exchange=short_exchange,
            net_bps=net_bps,
            notional_usd=notional_usd,
            long_price=long_price,
            short_price=short_price,
            long_funding=long_funding,
            short_funding=short_funding
        )
    
    async def _execute_opportunities(self, opportunities: List[OpportunityEvent]):
        """Execute discovered opportunities"""
        
        # Sort by net BPS (best first)
        opportunities.sort(key=lambda x: x.net_bps, reverse=True)
        
        # Limit concurrent executions
        max_concurrent = self.config.execution.concurrent_opportunities
        
        for opportunity in opportunities[:max_concurrent]:
            try:
                logger.info(
                    "Executing opportunity",
                    symbol=opportunity.symbol,
                    long_exchange=opportunity.long_exchange,
                    short_exchange=opportunity.short_exchange,
                    net_bps=float(opportunity.net_bps),
                    notional_usd=float(opportunity.notional_usd)
                )
                
                # Execute through controller
                await self.execution_controller.execute_opportunity(
                    symbol=opportunity.symbol,
                    long_exchange=opportunity.long_exchange,
                    short_exchange=opportunity.short_exchange,
                    notional_usd=opportunity.notional_usd,
                    expected_net_bps=opportunity.net_bps
                )
                
            except Exception as e:
                logger.error(
                    "Failed to execute opportunity",
                    symbol=opportunity.symbol,
                    error=str(e),
                    exc_info=True
                )
