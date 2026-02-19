"""Check ALL symbols for funding in the next 30-50 minutes across all exchanges."""
import asyncio
import time
import ccxt.pro as ccxtpro


async def check_exchange(eid, target_min=40, window=15):
    """Check all symbols on an exchange for funding within target_min ± window minutes."""
    results = []
    try:
        ex = getattr(ccxtpro, eid)()
        await ex.load_markets()
        
        # Only linear USDT perps
        perp_symbols = [s for s in ex.symbols if s.endswith(":USDT")]
        
        # Batch fetch if supported
        try:
            all_rates = await ex.fetch_funding_rates()
        except Exception:
            await ex.close()
            return results
        
        now_ms = time.time() * 1000
        lo = (target_min - window) * 60000
        hi = (target_min + window) * 60000
        
        for sym, data in all_rates.items():
            if not sym.endswith(":USDT"):
                continue
            ts = data.get("fundingTimestamp")
            rate = data.get("fundingRate")
            if ts and rate:
                diff_ms = ts - now_ms
                if lo <= diff_ms <= hi:
                    mins = diff_ms / 60000
                    results.append((sym, eid, rate, mins))
        
        await ex.close()
    except Exception as e:
        print(f"{eid}: ERROR - {e}")
    return results


async def main():
    target = 40  # minutes
    window = 15  # ± minutes
    
    print(f"Searching for symbols with funding in {target-window} to {target+window} minutes...")
    print(f"Current UTC: {time.strftime('%H:%M:%S', time.gmtime())}")
    print()
    
    exchanges = ["binance", "bybit"]
    
    # Run exchanges sequentially to avoid connection issues
    all_results = []
    for eid in exchanges:
        print(f"Checking {eid}...", flush=True)
        results = await check_exchange(eid, target, window)
        all_results.extend(results)
        print(f"  Found {len(results)} symbols")
    
    if all_results:
        # Sort by time
        all_results.sort(key=lambda x: x[3])
        print(f"\n{'='*70}")
        print(f"SYMBOLS WITH FUNDING IN ~{target} MINUTES ({target-window}-{target+window} min):")
        print(f"{'='*70}")
        for sym, eid, rate, mins in all_results:
            print(f"  {sym:25s} | {eid:8s} | rate={rate:+.8f} ({rate*100:+.6f}%) | in {mins:.0f}min")
    else:
        print(f"\nNo symbols found with funding in {target-window}-{target+window} minutes.")


if __name__ == "__main__":
    asyncio.run(main())
