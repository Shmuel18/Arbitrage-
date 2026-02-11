"""
Run Trinity without Redis (paper trading mode)
"""
import os
import sys
from pathlib import Path

# Set environment variables to bypass Redis
os.environ['PAPER_TRADING'] = 'true'
os.environ['DRY_RUN'] = 'true'

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

# Import and run
from src.core.config import init_config
from src.core.logging import init_logging

# Load config
config = init_config()
logger = init_logging()

logger.info("="*80)
logger.info("🧪 PAPER TRADING MODE - NO REDIS REQUIRED")
logger.info("="*80)
logger.info(f"Environment: {config.environment}")
logger.info(f"Paper Trading: {config.paper_trading}")
logger.info(f"Dry Run: {config.dry_run}")

# Test exchanges without running full bot
from src.exchanges.base import ExchangeManager
from src.exchanges.binance import BinanceAdapter

logger.info("\nInitializing exchange adapters...")
exchange_manager = ExchangeManager(watchlist=config.watchlist)

logger.info("✅ Ready! You can now test individual components.")
logger.info("\nTo run the full bot with Redis, use:")
logger.info("  docker-compose up -d redis")
logger.info("  python main.py")
