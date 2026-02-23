#!/usr/bin/env python3
"""
Diagnostic: Check if cached funding rates are fresh or STALE.

The bot finds opportunities using get_funding_rate_cached() - meaning it relies
on WebSocket/polling cache. If the cache is stale, opportunities are fake artifacts.

This script:
1. Connects to the same exchanges as the bot
2. Checks the age of cached rates (how old is the data?)
3. Does fresh REST fetch to compare against cache
4. Reports discrepancies that would invalidate opportunity findings
"""

import asyncio
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal
import time as _time

# Add workspace root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from src.core.config import init_config
from src.exchanges.adapter import ExchangeManager


async def check_freshness():
    """Check if cached rates are fresh enough to rely on."""
    cfg = init_config()
    mgr = ExchangeManager(cfg.exchanges, cfg)
    
    await mgr.connect()
    
    # Test symbols from your logs
    test_symbols = [
        "LA/USDT:USDT",
        "PIXEL/USDT:USDT",
        "HIPPO/USDT:USDT",
        "ERA/USDT:USDT",
        "Magic/USDT:USDT",
        "COMP/USDT:USDT",
    ]
    
    exchanges_to_test = ["binance", "okx", "gateio", "bitget", "bybit"]
    
    print("\n" + "="*100)
    print("FUNDING RATE CACHE FRESHNESS DIAGNOSTIC".center(100))
    print("="*100)
    print(f"Timestamp: {datetime.now(timezone.utc).isoformat()}\n")
    
    stale_count = 0
    fresh_count = 0
    mismatch_count = 0
    
    for symbol in test_symbols:
        print(f"\n{'='*100}")
        print(f"Symbol: {symbol}")
        print(f"{'='*100}")
        
        for ex_id in exchanges_to_test:
            adapter = mgr.get(ex_id)
            if not adapter:
                print(f"  {ex_id:15} | NOT CONFIGURED")
                continue
            
            if symbol not in adapter._exchange.symbols:
                print(f"  {ex_id:15} | NOT LISTED")
                continue
            
            # 1. Get cached rate
            cached = adapter.get_funding_rate_cached(symbol)
            
            if not cached:
                print(f"  {ex_id:15} | ❌ NO CACHE (ws/polling failed)")
                stale_count += 1
                continue
            
            cache_age_ms = (_time.time() * 1000) - (cached.get('timestamp') or 0)
            cache_age_sec = cache_age_ms / 1000
            
            if cache_age_sec > 30:  # older than 30 seconds is suspicious
                status = "⚠️  STALE"
                stale_count += 1
            elif cache_age_sec > 10:
                status = "⚠️  OLD"
                stale_count += 1
            else:
                status = "✅ FRESH"
                fresh_count += 1
            
            # 2. Do a REST fetch for comparison
            try:
                rest_data = await adapter.get_funding_rate(symbol)
                rest_rate = float(rest_data['rate'])
                cached_rate = float(cached['rate'])
                
                rate_diff_bps = abs(rest_rate - cached_rate) * 10000  # basis points
                
                if rate_diff_bps > 1:  # >0.01% difference is concerning
                    mismatch_status = "❌ MISMATCH"
                    mismatch_count += 1
                else:
                    mismatch_status = "✅ MATCH"
                
                print(
                    f"  {ex_id:15} | {status} ({cache_age_sec:6.1f}s old) | "
                    f"Cached: {cached_rate:10.8f} | REST: {rest_rate:10.8f} | "
                    f"Δ={rate_diff_bps:6.2f}bps | {mismatch_status}"
                )
                
            except Exception as e:
                print(
                    f"  {ex_id:15} | {status} ({cache_age_sec:6.1f}s old) | "
                    f"Cached: {float(cached['rate']):10.8f} | REST fetch failed: {e}"
                )
    
    print("\n" + "="*100)
    print("SUMMARY")
    print("="*100)
    print(f"Fresh rates:           {fresh_count}")
    print(f"Stale/missing rates:   {stale_count}")
    print(f"Cache-vs-REST matches: {len(test_symbols) * len(exchanges_to_test) - mismatch_count} / {len(test_symbols) * len(exchanges_to_test)}")
    print(f"Mismatches detected:   {mismatch_count}")
    
    if stale_count > 0 or mismatch_count > 5:
        print("\n⚠️  WARNING: Cached rates are STALE or don't match live rates!")
        print("The opportunities shown by the scanner may be FAKE ARTIFACTS of old data.")
        print("\nAction items:")
        print("  1. Check WebSocket watchers - are they connecting and receiving updates?")
        print("  2. Check polling fallback - is it running (should update every 5 seconds)?")
        print("  3. Restart the bot to force cache warmup from REST endpoints")
    else:
        print("\n✅ Cached rates are FRESH and match live REST data.")
        print("The opportunities found are likely LEGITIMATE.")
    
    await mgr.close_all()


if __name__ == "__main__":
    asyncio.run(check_freshness())
