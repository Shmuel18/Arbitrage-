#!/usr/bin/env python3
"""
Diagnostic script to check if Redis cached funding rates are fresh and being updated.
Run this on the friend's machine to verify cache staleness.
"""
import json
import redis
import time
from datetime import datetime
from src.core.config import Config
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s'
)
logger = logging.getLogger(__name__)

def check_redis_connection():
    """Test Redis connection"""
    try:
        r = redis.Redis(host='localhost', port=6379, db=0)
        r.ping()
        logger.info("✅ Redis connection OK")
        return r
    except Exception as e:
        logger.error(f"❌ Redis connection failed: {e}")
        return None

def get_cached_rates(r):
    """Retrieve all cached funding rates from Redis"""
    try:
        # Get all funding rate cache keys
        keys = r.keys('trinity:funding_rate:*')
        logger.info(f"Found {len(keys)} cached funding rates")
        
        if not keys:
            logger.warning("No cached funding rates found in Redis!")
            return {}
        
        rates_by_exchange = {}
        now = time.time()
        
        for key in keys[:20]:  # Sample first 20
            data = r.get(key)
            if data:
                symbol_data = json.loads(data)
                exchange = key.decode().split(':')[3]
                symbol = key.decode().split(':')[4] if ':' in key.decode()[len('trinity:funding_rate:'):] else 'unknown'
                
                age = symbol_data.get('timestamp', 0)
                age_seconds = now - age if age else -1
                
                if exchange not in rates_by_exchange:
                    rates_by_exchange[exchange] = []
                
                rates_by_exchange[exchange].append({
                    'symbol': symbol,
                    'rate': symbol_data.get('rate'),
                    'timestamp': datetime.fromtimestamp(age).isoformat() if age else 'unknown',
                    'age_seconds': age_seconds
                })
        
        return rates_by_exchange
    except Exception as e:
        logger.error(f"Error getting cached rates: {e}")
        return {}

def check_specific_symbols(r):
    """Check cache for specific symbols we know the bot is trading"""
    target_symbols = ['LA/USDT:USDT', 'PIXEL/USDT:USDT', 'HIPPO/USDT:USDT']
    
    logger.info("\n📍 Checking specific symbols in cache:")
    for symbol in target_symbols:
        for exchange in ['binance', 'okx', 'bitget', 'gateio', 'bybit', 'kucoin']:
            key = f'trinity:funding_rate:{exchange}:{symbol}'
            try:
                data = r.get(key)
                if data:
                    symbol_data = json.loads(data)
                    age = time.time() - symbol_data.get('timestamp', 0)
                    rate = symbol_data.get('rate', 0)
                    print(f"  {exchange:8} {symbol:12} → {rate:+.6f} (age: {age:.1f}s)")
            except Exception as e:
                pass

def get_scan_results(r):
    """Get latest scan results to see what opportunities were found"""
    try:
        opportunities = r.get('trinity:opportunities')
        if opportunities:
            opps = json.loads(opportunities)
            logger.info(f"\n📊 Latest scan results:")
            logger.info(f"   Total opportunities: {len(opps)}")
            
            if opps:
                # Show top 3
                sorted_opps = sorted(opps, key=lambda x: float(x.get('net_pct', -999)), reverse=True)
                for i, opp in enumerate(sorted_opps[:3], 1):
                    symbol = opp.get('symbol', 'N/A')
                    spread = opp.get('funding_spread_pct', 0)
                    net = opp.get('net_pct', 0)
                    logger.info(f"   {i}. {symbol:15} → Spread: {spread:+.4f}% | Net: {net:+.4f}%")
    except Exception as e:
        logger.error(f"Error getting scan results: {e}")

def main():
    logger.info("=" * 60)
    logger.info("REDIS CACHE FRESHNESS DIAGNOSTIC")
    logger.info("=" * 60)
    
    r = check_redis_connection()
    if not r:
        return
    
    # Check cache status
    rates = get_cached_rates(r)
    logger.info(f"\nCache breakdown by exchange:")
    for exchange, data in rates.items():
        if data:
            avg_age = sum(d['age_seconds'] for d in data) / len(data)
            logger.info(f"  {exchange:10} → {len(data)} rates, avg age: {avg_age:.1f}s")
    
    # Check specific symbols
    check_specific_symbols(r)
    
    # Check latest scan
    get_scan_results(r)
    
    logger.info("\n" + "=" * 60)
    logger.info("INTERPRETATION:")
    logger.info("- Age < 2 seconds = Cache is being updated in real-time ✅")
    logger.info("- Age > 10 seconds = Cache might be stale ⚠️")
    logger.info("- Age > 60 seconds = Cache is definitely stale ❌")
    logger.info("=" * 60)

if __name__ == '__main__':
    main()
