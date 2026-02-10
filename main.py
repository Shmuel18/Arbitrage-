"""
Trinity Arbitrage Engine V2.1-FINAL
Main Application Entry Point
"""

import argparse
import asyncio
import signal
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

from src.core.config import get_config, init_config
from src.core.logging import get_logger, init_logging

logger = None


class TrinityEngine:
    """
    Main Trinity Arbitrage Engine
    """
    
    def __init__(self, config_path: str = "config.yaml"):
        """Initialize engine"""
        # Load configuration
        self.config = init_config(config_path)
        
        # Initialize logging
        global logger
        logger = init_logging()
        
        # Validate configuration
        self.config.validate_safety()
        
        # Components will be initialized in start()
        self.exchange_manager = None
        self.health_monitor = None
        self.redis_client = None
        
        # Shutdown flag
        self._shutdown_event = asyncio.Event()
        
        logger.info("=" * 80)
        logger.info(f"Trinity Arbitrage Engine V{self.config.version}")
        logger.info(f"Environment: {self.config.environment}")
        logger.info(f"Paper Trading: {self.config.paper_trading}")
        logger.info(f"Dry Run: {self.config.dry_run}")
        logger.info("=" * 80)
    
    async def start(self):
        """Start the engine"""
        try:
            logger.info("Starting Trinity Engine...")
            
            # Import components (lazy import to ensure config is loaded)
            from src.ingestion.health_monitor import HealthMonitor
            from src.exchanges.base import ExchangeManager
            
            # Initialize Redis (with fallback for paper trading)
            from src.storage.redis_client import get_redis
            logger.info("Connecting to Redis...")
            self.redis_client = await get_redis()
            logger.info("Redis ready")
            
            # Initialize health monitor
            logger.info("Starting health monitor...")
            self.health_monitor = HealthMonitor()
            await self.health_monitor.start()
            
            # Initialize exchange manager
            logger.info("Initializing exchange adapters...")
            self.exchange_manager = ExchangeManager()
            
            # Log enabled exchanges
            logger.info(f"Enabled exchanges: {', '.join(self.config.enabled_exchanges)}")
            
            logger.info("=" * 80)
            logger.info("✅ Trinity Engine started successfully")
            logger.info("=" * 80)
            
            if self.config.paper_trading:
                logger.warning("⚠️  PAPER TRADING MODE - No real orders will be placed")
            elif self.config.dry_run:
                logger.warning("⚠️  DRY RUN MODE - Orders will be simulated")
            else:
                logger.critical("🔴 LIVE TRADING MODE - Real capital at risk!")
            
            logger.info("=" * 80)
            
            # Main loop
            await self._main_loop()
            
        except Exception as e:
            logger.critical(f"Fatal error during startup: {e}", exc_info=True)
            raise
    
    async def _main_loop(self):
        """Main engine loop"""
        logger.info("Entering main loop...")
        
        try:
            # Wait for shutdown signal
            await self._shutdown_event.wait()
            
        except asyncio.CancelledError:
            logger.info("Main loop cancelled")
    
    async def stop(self):
        """Stop the engine"""
        logger.info("Stopping Trinity Engine...")
        
        try:
            # Signal shutdown
            self._shutdown_event.set()
            
            # Stop health monitor
            if self.health_monitor:
                await self.health_monitor.stop()
            
            # Disconnect exchanges
            if self.exchange_manager:
                await self.exchange_manager.disconnect_all()
            
            # Disconnect Redis
            if self.redis_client:
                await self.redis_client.disconnect()
            
            logger.info("=" * 80)
            logger.info("✅ Trinity Engine stopped gracefully")
            logger.info("=" * 80)
            
        except Exception as e:
            logger.error(f"Error during shutdown: {e}", exc_info=True)
    
    def _setup_signal_handlers(self):
        """Setup signal handlers for graceful shutdown"""
        def signal_handler(signum, frame):
            logger.info(f"Received signal {signum}, initiating shutdown...")
            asyncio.create_task(self.stop())
        
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)


async def main():
    """Main entry point"""
    # Parse arguments
    parser = argparse.ArgumentParser(
        description="Trinity Arbitrage Engine V2.1-FINAL"
    )
    parser.add_argument(
        "--config",
        type=str,
        default="config.yaml",
        help="Path to configuration file"
    )
    parser.add_argument(
        "--paper",
        action="store_true",
        help="Enable paper trading mode"
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Enable live trading mode (overrides config)"
    )
    
    args = parser.parse_args()
    
    # Create engine instance
    engine = TrinityEngine(config_path=args.config)
    
    # Override paper trading mode if specified
    if args.paper:
        engine.config.paper_trading = True
        engine.config.dry_run = True
        logger.info("Paper trading mode enabled via CLI")
    elif args.live:
        engine.config.paper_trading = False
        engine.config.dry_run = False
        logger.critical("Live trading mode enabled via CLI - REAL CAPITAL AT RISK!")
        
        # Extra confirmation for live mode
        print("\n" + "=" * 80)
        print("⚠️  WARNING: You are about to start LIVE TRADING MODE")
        print("Real capital will be at risk. Are you sure? (yes/no)")
        print("=" * 80)
        
        confirmation = input("> ").strip().lower()
        if confirmation != "yes":
            print("Aborted.")
            return
    
    # Setup signal handlers
    engine._setup_signal_handlers()
    
    # Start engine
    try:
        await engine.start()
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received")
    except Exception as e:
        logger.critical(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)
    finally:
        await engine.stop()


if __name__ == "__main__":
    # Run main with asyncio
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nShutdown complete.")
