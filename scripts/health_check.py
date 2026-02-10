"""
System Health Check Script
Validates all components are operational
"""

import asyncio
import sys
from pathlib import Path

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.config import get_config
from src.core.logging import get_logger, init_logging
from src.storage.redis_client import RedisClient
from sqlalchemy import create_engine, text

logger = init_logging()


class HealthChecker:
    """System health checker"""
    
    def __init__(self):
        self.config = get_config()
        self.results = {}
    
    def check_config(self) -> bool:
        """Check configuration"""
        logger.info("Checking configuration...")
        
        try:
            # Validate config
            self.config.validate_safety()
            
            logger.info(f"✅ Configuration valid")
            logger.info(f"   Environment: {self.config.environment}")
            logger.info(f"   Paper Trading: {self.config.paper_trading}")
            logger.info(f"   Enabled Exchanges: {', '.join(self.config.enabled_exchanges)}")
            
            self.results['config'] = True
            return True
            
        except Exception as e:
            logger.error(f"❌ Configuration invalid: {e}")
            self.results['config'] = False
            return False
    
    def check_database(self) -> bool:
        """Check database connection"""
        logger.info("Checking database connection...")
        
        try:
            engine = create_engine(self.config.database.dsn)
            
            with engine.connect() as conn:
                result = conn.execute(text("SELECT 1"))
                result.scalar()
            
            logger.info(f"✅ Database connection OK")
            logger.info(f"   Host: {self.config.database.host}")
            logger.info(f"   Database: {self.config.database.database}")
            
            self.results['database'] = True
            return True
            
        except Exception as e:
            logger.error(f"❌ Database connection failed: {e}")
            self.results['database'] = False
            return False
    
    async def check_redis(self) -> bool:
        """Check Redis connection"""
        logger.info("Checking Redis connection...")
        
        try:
            redis_client = RedisClient()
            await redis_client.connect()
            
            # Test ping
            health = await redis_client.health_check()
            
            if health:
                logger.info(f"✅ Redis connection OK")
                logger.info(f"   Host: {self.config.redis.host}")
                logger.info(f"   Database: {self.config.redis.db}")
                
                self.results['redis'] = True
                result = True
            else:
                logger.error(f"❌ Redis ping failed")
                self.results['redis'] = False
                result = False
            
            await redis_client.disconnect()
            return result
            
        except Exception as e:
            logger.error(f"❌ Redis connection failed: {e}")
            self.results['redis'] = False
            return False
    
    def check_api_keys(self) -> bool:
        """Check exchange API keys"""
        logger.info("Checking exchange API keys...")
        
        all_ok = True
        
        for exchange_id in self.config.enabled_exchanges:
            exchange = self.config.get_exchange_config(exchange_id)
            
            if not exchange:
                logger.error(f"❌ {exchange_id}: Not configured")
                all_ok = False
                continue
            
            if not exchange.api_key or not exchange.api_secret:
                logger.error(f"❌ {exchange_id}: Missing API credentials")
                all_ok = False
            else:
                logger.info(f"✅ {exchange_id}: API credentials configured")
                
                # Warn if not testnet in non-production
                if not exchange.testnet and self.config.environment != "production":
                    logger.warning(f"⚠️  {exchange_id}: Using MAINNET in {self.config.environment} environment")
        
        self.results['api_keys'] = all_ok
        return all_ok
    
    def check_monitoring(self) -> bool:
        """Check monitoring configuration"""
        logger.info("Checking monitoring setup...")
        
        issues = []
        
        # Check Telegram
        if self.config.monitoring.enable_telegram:
            if not self.config.monitoring.telegram_bot_token:
                issues.append("Telegram bot token missing")
            if not self.config.monitoring.telegram_chat_id:
                issues.append("Telegram chat ID missing")
        
        # Check Sentry
        if self.config.monitoring.enable_sentry:
            if not self.config.monitoring.sentry_dsn:
                issues.append("Sentry DSN missing")
        
        if issues:
            for issue in issues:
                logger.warning(f"⚠️  {issue}")
            logger.info("✅ Monitoring configured (with warnings)")
            self.results['monitoring'] = True
            return True
        else:
            logger.info("✅ Monitoring fully configured")
            self.results['monitoring'] = True
            return True
    
    def print_summary(self):
        """Print summary"""
        logger.info("")
        logger.info("=" * 80)
        logger.info("HEALTH CHECK SUMMARY")
        logger.info("=" * 80)
        
        all_passed = True
        
        for component, status in self.results.items():
            status_str = "✅ PASS" if status else "❌ FAIL"
            logger.info(f"  {component.upper():<20}: {status_str}")
            
            if not status:
                all_passed = False
        
        logger.info("=" * 80)
        
        if all_passed:
            logger.info("✅ All health checks passed - System ready")
        else:
            logger.warning("⚠️  Some health checks failed - Review errors above")
        
        logger.info("=" * 80)
        
        return all_passed


async def main():
    """Main health check"""
    checker = HealthChecker()
    
    logger.info("=" * 80)
    logger.info("Trinity System Health Check")
    logger.info("=" * 80)
    logger.info("")
    
    # Run checks
    checker.check_config()
    checker.check_database()
    await checker.check_redis()
    checker.check_api_keys()
    checker.check_monitoring()
    
    # Print summary
    all_passed = checker.print_summary()
    
    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    asyncio.run(main())
