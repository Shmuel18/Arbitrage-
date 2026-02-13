"""
Discovery Scanner
Continuously scans for funding arbitrage opportunities
"""

import asyncio
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Dict, List, Optional

from src.core.config import get_config
from src.core.contracts import OpportunityCandidate, OrderSide, TradeState
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
    
    MAX_ACTIVE_TRADES = 1  # Only one trade at a time — the best one
    
    def __init__(self, exchange_manager, execution_controller):
        self.config = get_config()
        self.exchange_manager = exchange_manager
        self.execution_controller = execution_controller
        self.calculator = WorstCaseCalculator()
        
        self._running = False
        self._scan_task = None
        
        self.scan_interval_sec = 30  # Wait between scans (scan itself takes ~5 min for 500+ symbols)
        self.min_net_bps = self.config.trading_params.min_net_bps
        
        # Track symbols with active trades to prevent duplicates
        self._active_symbols: set = set()
        
        # Runtime blacklist: symbols that failed execution (delisted, etc.)
        # Cleared on restart, persists across scan cycles
        self._failed_symbols: set = set()
        
        # Top-5 overview every 5 minutes
        self._overview_interval_sec = 300  # 5 minutes
        self._last_overview_time: Optional[datetime] = None  # None = fire on first scan
    
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
                import traceback, sys
                traceback.print_exc(file=sys.stderr)
                logger.error(f"Error in scan loop: {e}")
            
            try:
                await asyncio.sleep(self.scan_interval_sec)
            except asyncio.CancelledError:
                break
    
    def _discover_common_symbols(self, exchange_ids: List[str]) -> List[str]:
        """Find all USDT pairs available on at least 2 connected exchanges."""
        symbol_counts: Dict[str, int] = {}
        for eid in exchange_ids:
            adapter = self.exchange_manager.get_adapter(eid)
            if not adapter:
                continue
            for sym in adapter.symbols:
                symbol_counts[sym] = symbol_counts.get(sym, 0) + 1

        # Keep only symbols present on 2+ exchanges and not blacklisted
        blacklist = set(self.config.blacklist) if self.config.blacklist else set()
        common = sorted(
            sym for sym, cnt in symbol_counts.items()
            if cnt >= 2 and sym not in blacklist
        )
        return common

    async def _scan_once(self):
        """Single scan iteration"""
        
        # Get all exchange IDs
        exchange_ids = list(self.exchange_manager.adapters.keys())
        
        if len(exchange_ids) < 2:
            logger.warning("Need at least 2 exchanges for arbitrage")
            return
        
        # Dynamic discovery: ALL USDT pairs on 2+ exchanges
        symbols = self._discover_common_symbols(exchange_ids)
        
        if not symbols:
            logger.warning("No common symbols found across exchanges")
            return
        
        logger.info(f"Scanning {len(symbols)} common USDT pairs across {len(exchange_ids)} exchanges...")
        
        opportunities = []  # ALL evaluated pairs (for overview)
        scan_errors = 0
        all_funding_diffs = []  # Collect funding diffs for overview
        
        # Concurrent scanning with rate-limit-safe semaphore
        sem = asyncio.Semaphore(5)  # conservative to avoid rate limits
        scanned = 0
        total = len(symbols)

        async def _scan_one(sym: str):
            nonlocal scanned
            async with sem:
                result = await self._scan_symbol(sym, exchange_ids)
                scanned += 1
                if scanned % 100 == 0:
                    logger.info(f"Scan progress: {scanned}/{total} symbols...")
                return result

        # Process in batches of 50 to avoid overwhelming connections
        batch_size = 50
        for batch_start in range(0, total, batch_size):
            batch = symbols[batch_start:batch_start + batch_size]
            tasks = [_scan_one(sym) for sym in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for sym, res in zip(batch, results):
                if isinstance(res, Exception):
                    scan_errors += 1
                else:
                    opportunities.extend(res)
        
        # ── Top-5 Market Overview (use data from scan, no extra API calls) ──
        self._log_overview_from_scan(opportunities, len(symbols))
        
        # Filter actionable opportunities (above threshold)
        actionable = [opp for opp in opportunities if opp.net_bps >= self.min_net_bps]
        
        # Log scan results
        if actionable:
            logger.info(
                "Scan complete",
                symbols_scanned=len(symbols),
                opportunities_found=len(actionable),
                errors=scan_errors,
                best_bps=float(max(opp.net_bps for opp in actionable))
            )
            
            # Execute best opportunities
            await self._execute_opportunities(actionable)
        else:
            logger.info(f"Scan complete: {len(symbols)} symbols scanned ({scan_errors} errors), no opportunities above {float(self.min_net_bps):.1f} bps threshold")
    
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
                # Get instrument spec
                spec = await adapter.get_instrument_spec(symbol)
                
                # Get current funding rate from exchange
                funding_data = await adapter.get_funding_rate(symbol)
                funding_rate = Decimal(str(funding_data.get('rate', 0))) if funding_data else Decimal('0')
                interval_hours = funding_data.get('interval_hours', 8) if funding_data else 8
                
                # A6: Check funding staleness - skip if < 5 min until next funding
                next_funding_ts = funding_data.get('next_timestamp') or funding_data.get('timestamp') if funding_data else None
                if next_funding_ts is not None:
                    try:
                        if isinstance(next_funding_ts, (int, float)):
                            ts_sec = next_funding_ts / 1000 if next_funding_ts > 1e12 else next_funding_ts
                            secs_until = ts_sec - datetime.utcnow().timestamp()
                        else:
                            secs_until = 600  # If string, assume ok
                        if 0 < secs_until < 300:  # Less than 5 min until funding
                            logger.debug("Skipping - too close to funding", symbol=symbol, exchange=exchange_id, secs_until=int(secs_until))
                            continue
                    except Exception:
                        pass  # If parsing fails, proceed normally
                
                # Get current price
                ticker = await adapter.get_ticker(symbol)
                mark_price = Decimal(str(ticker.get('last', 0)))
                
                if mark_price <= 0:
                    continue
                
                exchange_data[exchange_id] = {
                    'spec': spec,
                    'funding_rate': funding_rate,
                    'mark_price': mark_price,
                    'interval_hours': interval_hours,
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
                        
                        if opportunity:
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
        
        # Calculate funding edge (normalized to 8h equivalent)
        funding_edge_bps = self.calculator.calculate_funding_edge(
            funding_rate_long=long_funding,
            funding_rate_short=short_funding,
            funding_hours_long=long_data.get('interval_hours', 8),
            funding_hours_short=short_data.get('interval_hours', 8),
        )
        
        # Calculate ALL costs: fees + slippage + basis + safety buffers
        total_fee_bps = self.calculator.calculate_fees(
            spec_long=long_spec,
            spec_short=short_spec,
            round_trip=True
        )
        slippage_bps = Decimal(str(self.config.trading_params.slippage_buffer_bps))
        basis_bps = Decimal(str(self.config.trading_params.basis_buffer_bps))
        safety_bps = Decimal(str(self.config.trading_params.safety_buffer_bps))
        total_cost_bps = total_fee_bps + slippage_bps + basis_bps + safety_bps
        
        # Net profit after ALL worst-case costs
        net_bps = funding_edge_bps - total_cost_bps
        
        # Use configured position size
        notional_usd = Decimal(str(self.config.risk_limits.max_position_size_usd))
        
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
    
    def _log_overview_from_scan(self, opportunities: List[OpportunityEvent], total_symbols: int):
        """Log top-5 funding diffs from scan results (no extra API calls).
        Uses already-collected opportunity data. Fires every 5 minutes."""
        now = datetime.utcnow()
        if self._last_overview_time and (now - self._last_overview_time).total_seconds() < self._overview_interval_sec:
            return
        self._last_overview_time = now

        if not opportunities:
            lines = [f"\n══════════ MARKET OVERVIEW ({total_symbols} pairs scanned) ══════════"]
            lines.append("  No funding diffs above threshold found")
            lines.append("═" * 70)
            logger.info("\n".join(lines))
            return

        # Deduplicate: keep best opp per symbol
        best_by_symbol: Dict[str, OpportunityEvent] = {}
        for opp in opportunities:
            existing = best_by_symbol.get(opp.symbol)
            if not existing or opp.net_bps > existing.net_bps:
                best_by_symbol[opp.symbol] = opp

        # Sort by funding diff (net_bps) descending, take top 10
        sorted_opps = sorted(best_by_symbol.values(), key=lambda x: x.net_bps, reverse=True)
        top = sorted_opps[:10]

        lines = [f"\n══════════ MARKET OVERVIEW: Top {len(top)} Funding Diffs (out of {total_symbols} pairs) ══════════"]
        for i, opp in enumerate(top, 1):
            lines.append(
                f"  {i:>2}. {opp.symbol:>18s}  │  "
                f"Long {opp.long_exchange}={float(opp.long_funding)*100:+.4f}%  "
                f"Short {opp.short_exchange}={float(opp.short_funding)*100:+.4f}%  "
                f"│  Net={float(opp.net_bps):.1f} bps"
            )
        lines.append("═" * 80)
        logger.info("\n".join(lines))

    def _refresh_active_symbols(self):
        """Sync active symbols from execution controller"""
        self._active_symbols = set()
        for active in self.execution_controller.active_trades:
            self._active_symbols.add(active.opportunity.symbol)

    async def _check_balance(self, exchange_id: str, required_margin_usd: Decimal) -> bool:
        """Verify exchange has enough free margin"""
        try:
            adapter = self.exchange_manager.get_adapter(exchange_id)
            if not adapter:
                return False
            balance = await adapter.get_balance()
            free_usdt = balance.get('free', Decimal('0'))
            if free_usdt < required_margin_usd:
                logger.warning(
                    "Insufficient balance",
                    exchange=exchange_id,
                    free=float(free_usdt),
                    required=float(required_margin_usd),
                )
                return False
            return True
        except Exception as e:
            logger.warning("Balance check failed", exchange=exchange_id, error=str(e))
            return False

    async def _execute_opportunities(self, opportunities: List[OpportunityEvent]):
        """Execute the single best opportunity that fits our balance.
        
        Only attempts ONE trade — the best one that passes all pre-flight
        checks. If it fails, we wait for the next scan cycle.
        """
        
        # Refresh active symbols to prevent duplicates
        self._refresh_active_symbols()
        
        active_count = len(self.execution_controller.active_trades)
        if active_count >= self.MAX_ACTIVE_TRADES:
            logger.debug("Max active trades reached", active=active_count)
            return
        
        # Sort by net BPS (best first)
        opportunities.sort(key=lambda x: x.net_bps, reverse=True)
        
        # Pre-fetch balances once (avoid repeated API calls)
        exchange_balances: Dict[str, Decimal] = {}
        for eid in set(o.long_exchange for o in opportunities) | set(o.short_exchange for o in opportunities):
            adapter = self.exchange_manager.get_adapter(eid)
            if adapter:
                try:
                    bal = await adapter.get_balance()
                    exchange_balances[eid] = Decimal(str(bal.get('free', 0)))
                except Exception:
                    exchange_balances[eid] = Decimal('0')
        
        # Get leverage from first exchange config (all set to same value)
        first_ex = list(self.config.exchanges.keys())[0] if self.config.exchanges else None
        leverage = Decimal(str((self.config.exchanges[first_ex].leverage or 5) if first_ex else 5))
        max_margin_pct = Decimal(str(self.config.risk_limits.max_margin_usage))
        max_notional_cfg = Decimal(str(self.config.risk_limits.max_position_size_usd))
        
        # Find the FIRST valid candidate (best by net_bps)
        candidate = None
        chosen_opp = None
        
        for opp in opportunities:
            # DUPLICATE / BLACKLIST CHECK
            if opp.symbol in self._active_symbols or opp.symbol in self._failed_symbols:
                continue
            
            # COOLDOWN CHECK
            if self.execution_controller.redis_client:
                try:
                    if await self.execution_controller.redis_client.is_cooled_down(opp.symbol):
                        continue
                except Exception:
                    pass
            
            # SIZE TO ACTUAL BALANCE
            long_free = exchange_balances.get(opp.long_exchange, Decimal('0'))
            short_free = exchange_balances.get(opp.short_exchange, Decimal('0'))
            
            # Usable margin per side = free * max_margin_pct
            usable_long = long_free * max_margin_pct
            usable_short = short_free * max_margin_pct
            
            # Max notional each side can support = margin * leverage
            max_notional_long = usable_long * leverage
            max_notional_short = usable_short * leverage
            
            # Take the minimum of both sides and the config cap
            notional_usd = min(max_notional_long, max_notional_short, max_notional_cfg)
            
            if notional_usd < Decimal('5'):  # minimum $5 trade
                continue
            
            # Calculate quantity
            avg_price = (opp.long_price + opp.short_price) / Decimal('2')
            if avg_price <= 0:
                continue
            
            # Get instrument specs for quantity normalization
            long_adapter = self.exchange_manager.get_adapter(opp.long_exchange)
            short_adapter = self.exchange_manager.get_adapter(opp.short_exchange)
            if not long_adapter or not short_adapter:
                continue
            
            try:
                long_spec = await long_adapter.get_instrument_spec(opp.symbol)
                short_spec = await short_adapter.get_instrument_spec(opp.symbol)
            except Exception:
                continue
            
            # Normalize quantity to lot size
            raw_quantity = notional_usd / avg_price
            lot = max(long_spec.lot_size, short_spec.lot_size)
            if lot > 0:
                quantity = (raw_quantity // lot) * lot
            else:
                quantity = raw_quantity
            
            # Check minimum notional
            actual_notional = quantity * avg_price
            min_notional = max(long_spec.min_notional, short_spec.min_notional)
            if quantity <= 0 or actual_notional < min_notional:
                continue
            
            # Build OpportunityCandidate — this is our winner
            candidate = OpportunityCandidate(
                symbol=opp.symbol,
                exchange_long=opp.long_exchange,
                exchange_short=opp.short_exchange,
                quantity=quantity,
                size_usd=actual_notional,
                expected_net_bps=opp.net_bps,
                funding_edge_bps=opp.net_bps,
                total_fees_bps=Decimal('0'),
                total_slippage_bps=Decimal('0'),
                total_buffer_bps=Decimal('0'),
                max_slippage_bps=Decimal('10'),
                deadline_timestamp=datetime.utcnow() + timedelta(minutes=5),
                long_entry_price=opp.long_price,
                short_entry_price=opp.short_price,
            )
            chosen_opp = opp
            break  # Found best valid candidate
        
        if not candidate:
            if opportunities:
                logger.info(f"Found {len(opportunities)} opportunities but none passed pre-flight checks")
            return
        
        # Execute the ONE chosen opportunity
        logger.info(
            "Executing opportunity",
            symbol=candidate.symbol,
            long_exchange=candidate.exchange_long,
            short_exchange=candidate.exchange_short,
            net_bps=float(candidate.expected_net_bps),
            quantity=float(candidate.quantity),
            notional_usd=float(candidate.size_usd)
        )
        
        try:
            result = await self.execution_controller.execute_opportunity(candidate)
            
            if result.state == TradeState.ACTIVE_HEDGED:
                self._active_symbols.add(candidate.symbol)
                logger.info("Trade opened successfully", symbol=candidate.symbol)
            else:
                # Blacklist this symbol so next scan picks a different one
                self._failed_symbols.add(candidate.symbol)
                logger.warning(
                    f"Execution failed for {candidate.symbol} — blacklisted for future scans. "
                    f"Errors: {result.errors[:3] if hasattr(result, 'errors') else []}"
                )
        except Exception as e:
            self._failed_symbols.add(candidate.symbol)
            logger.error(
                f"Exception executing {candidate.symbol} — blacklisted. Error: {e}",
            )
