"""
Worst-Case Math Calculator
Conservative profit/cost estimation
"""

from decimal import Decimal
from typing import Tuple, Dict, Optional

from src.core.config import get_config
from src.core.contracts import InstrumentSpec, StandardMarketEvent
from src.core.logging import get_logger

logger = get_logger("calculator")


class WorstCaseCalculator:
    """
    Calculates expected net profit using WORST CASE assumptions
    
    Philosophy:
    - Every order pays taker fees
    - Minimum slippage on every trade
    - Basis risk buffer
    - Safety buffer
    - Rounding losses
    
    If it's profitable after all this, it's worth considering
    """
    
    def __init__(self):
        self.config = get_config()
    
    def calculate_funding_edge(
        self,
        funding_rate_long: Decimal,
        funding_rate_short: Decimal,
        funding_hours_long: int = 8,
        funding_hours_short: int = 8,
    ) -> Decimal:
        """
        Calculate funding rate edge in bps, normalized to 8h equivalent.
        
        Args:
            funding_rate_long: Funding rate on long side
            funding_rate_short: Funding rate on short side
            funding_hours_long: Hours between fundings on long exchange
            funding_hours_short: Hours between fundings on short exchange
        
        Returns:
            Net funding edge in basis points (8h equivalent)
        """
        # Normalize both rates to 8h equivalent so 1h and 8h are comparable
        normalized_long = funding_rate_long * Decimal(str(8 / funding_hours_long))
        normalized_short = funding_rate_short * Decimal(str(8 / funding_hours_short))
        
        # Net funding per 8h period
        net_funding = normalized_short - normalized_long
        
        # Convert to bps (basis points)
        funding_edge_bps = net_funding * Decimal('10000')
        
        return funding_edge_bps
    
    def calculate_fees(
        self,
        spec_long: InstrumentSpec,
        spec_short: InstrumentSpec,
        round_trip: bool = True
    ) -> Decimal:
        """
        Calculate trading fees in bps
        WORST CASE: Always assume taker fees
        
        Args:
            spec_long: Instrument spec for long side
            spec_short: Instrument spec for short side
            round_trip: Include open + close (True) or just open (False)
        
        Returns:
            Total fees in basis points
        """
        # Use taker fees (worst case)
        fee_long = spec_long.taker_fee
        fee_short = spec_short.taker_fee
        
        # Open position fees
        open_fees = fee_long + fee_short
        
        if round_trip:
            # Close position fees (same as open)
            close_fees = fee_long + fee_short
            total_fees = open_fees + close_fees
        else:
            total_fees = open_fees
        
        # Convert to bps
        fees_bps = total_fees * Decimal('10000')
        
        return fees_bps
    
    def calculate_slippage(
        self,
        market_long: StandardMarketEvent,
        market_short: StandardMarketEvent,
        round_trip: bool = True
    ) -> Decimal:
        """
        Calculate expected slippage in bps
        WORST CASE: Cross spread on every execution
        
        Args:
            market_long: Market data for long side
            market_short: Market data for short side
            round_trip: Include open + close
        
        Returns:
            Total slippage in basis points
        """
        # Open slippage: cross the spread
        spread_long_bps = market_long.spread_bps
        spread_short_bps = market_short.spread_bps
        
        open_slippage = spread_long_bps + spread_short_bps
        
        if round_trip:
            # Close slippage: cross spread again
            close_slippage = spread_long_bps + spread_short_bps
            total_slippage = open_slippage + close_slippage
        else:
            total_slippage = open_slippage
        
        # Add configured buffer
        slippage_buffer = self.config.trading_params.slippage_buffer_bps
        total_slippage += slippage_buffer
        
        return total_slippage
    
    def calculate_net_profit(
        self,
        funding_rate_long: Decimal,
        funding_rate_short: Decimal,
        spec_long: InstrumentSpec,
        spec_short: InstrumentSpec,
        market_long: StandardMarketEvent,
        market_short: StandardMarketEvent,
        holding_periods: int = 1
    ) -> Tuple[Decimal, Dict]:
        """
        Calculate expected net profit in bps after ALL costs
        
        Args:
            funding_rate_long: Funding rate on long exchange
            funding_rate_short: Funding rate on short exchange
            spec_long: Instrument specification for long
            spec_short: Instrument specification for short
            market_long: Market data for long
            market_short: Market data for short
            holding_periods: Number of funding periods to hold
        
        Returns:
            (net_profit_bps, breakdown_dict)
        """
        # 1. Funding edge (revenue)
        funding_edge = self.calculate_funding_edge(
            funding_rate_long,
            funding_rate_short,
            funding_hours_long=spec_long.funding_interval_hours,
            funding_hours_short=spec_short.funding_interval_hours,
        )
        
        # Multiple by holding periods
        total_funding = funding_edge * Decimal(str(holding_periods))
        
        # 2. Trading fees (cost)
        fees = self.calculate_fees(spec_long, spec_short, round_trip=True)
        
        # 3. Slippage (cost)
        slippage = self.calculate_slippage(market_long, market_short, round_trip=True)
        
        # 4. Basis buffer (cost)
        basis_buffer = self.config.trading_params.basis_buffer_bps
        
        # 5. Safety buffer (cost)
        safety_buffer = self.config.trading_params.safety_buffer_bps
        
        # Calculate net
        net_profit = total_funding - fees - slippage - basis_buffer - safety_buffer
        
        # Breakdown
        breakdown = {
            'funding_edge_bps': float(total_funding),
            'fees_bps': float(fees),
            'slippage_bps': float(slippage),
            'basis_buffer_bps': float(basis_buffer),
            'safety_buffer_bps': float(safety_buffer),
            'net_profit_bps': float(net_profit),
            'holding_periods': holding_periods
        }
        
        logger.debug(
            "Profit calculation",
            **breakdown
        )
        
        return net_profit, breakdown
    
    def is_profitable(
        self,
        net_profit_bps: Decimal,
        min_threshold_bps: Optional[Decimal] = None
    ) -> bool:
        """
        Check if opportunity is profitable
        
        Args:
            net_profit_bps: Calculated net profit
            min_threshold_bps: Minimum required (defaults to config)
        
        Returns:
            True if profitable after all costs
        """
        if min_threshold_bps is None:
            min_threshold_bps = self.config.trading_params.min_net_bps
        
        return net_profit_bps >= min_threshold_bps
    
    def calculate_position_size(
        self,
        available_capital_usd: Decimal,
        mark_price: Decimal,
        leverage: int = 1
    ) -> Decimal:
        """
        Calculate position size in contracts
        
        Args:
            available_capital_usd: Available capital
            mark_price: Current mark price
            leverage: Leverage to use
        
        Returns:
            Position size in contracts
        """
        # Maximum position value
        max_position_value = min(
            available_capital_usd * Decimal(str(leverage)),
            self.config.risk_limits.max_position_size_usd
        )
        
        # Convert to contracts
        contracts = max_position_value / mark_price
        
        return contracts
    
    def calculate_required_margin(
        self,
        position_size_usd: Decimal,
        leverage: int,
        both_sides: bool = True
    ) -> Decimal:
        """
        Calculate required margin
        
        Args:
            position_size_usd: Position size in USD
            leverage: Leverage used
            both_sides: Calculate for both legs
        
        Returns:
            Required margin in USD
        """
        margin_per_side = position_size_usd / Decimal(str(leverage))
        
        if both_sides:
            return margin_per_side * Decimal('2')
        else:
            return margin_per_side
    
    def validate_orderbook_depth(
        self,
        market: StandardMarketEvent,
        required_size_usd: Decimal
    ) -> Tuple[bool, Decimal]:
        """
        Validate orderbook has sufficient depth
        
        Args:
            market: Market data
            required_size_usd: Required size in USD
        
        Returns:
            (has_depth, available_depth_usd)
        """
        # Calculate depth on both sides
        bid_depth = sum(level.notional for level in market.bids[:10])
        ask_depth = sum(level.notional for level in market.asks[:10])
        
        # Use minimum of both sides
        available_depth = min(bid_depth, ask_depth)
        
        # Check against minimum requirement
        min_depth = max(
            required_size_usd * Decimal('2'),  # 2x position size
            self.config.discovery.min_orderbook_depth_usd
        )
        
        has_depth = available_depth >= min_depth
        
        return has_depth, available_depth

