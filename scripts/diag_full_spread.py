"""Full cross-exchange spread check for symbols with funding in ~40 minutes."""
import asyncio
import time
import ccxt.pro as ccxtpro
from collections import defaultdict


async def fetch_rates(eid):
    """Fetch all funding rates from one exchange."""
    results = {}
    try:
        ex = getattr(ccxtpro, eid)()
        await ex.load_markets()
        all_rates = await ex.fetch_funding_rates()
        now_ms = time.time() * 1000

        for sym, data in all_rates.items():
            if not sym.endswith(":USDT"):
                continue
            ts = data.get("fundingTimestamp")
            rate = data.get("fundingRate")
            if ts and rate is not None:
                mins = (ts - now_ms) / 60000
                results[sym] = {"rate": rate, "mins": mins, "ts": ts}

        await ex.close()
        print(f"  {eid}: {len(results)} symbols loaded")
    except Exception as e:
        print(f"  {eid}: ERROR - {e}")
        try:
            await ex.close()
        except:
            pass
    return eid, results


async def main():
    print(f"Current UTC: {time.strftime('%H:%M:%S', time.gmtime())}")
    print(f"Fetching funding rates from ALL exchanges...\n")

    exchanges = ["binance", "bybit", "okx", "kucoin", "gateio"]
    
    # Fetch all in parallel
    tasks = [fetch_rates(eid) for eid in exchanges]
    raw = await asyncio.gather(*tasks, return_exceptions=True)
    
    # Build per-exchange data
    exchange_data = {}
    for item in raw:
        if isinstance(item, Exception):
            continue
        eid, rates = item
        exchange_data[eid] = rates

    print(f"\nLoaded {len(exchange_data)} exchanges successfully")
    
    # Find symbols with funding in 25-55 minutes on ANY exchange
    target_min, window = 40, 20
    lo, hi = target_min - window, target_min + window
    
    # Group by symbol: which exchanges have this symbol with funding soon?
    symbol_exchanges = defaultdict(dict)  # symbol -> {eid: rate}
    for eid, rates in exchange_data.items():
        for sym, info in rates.items():
            if lo <= info["mins"] <= hi:
                symbol_exchanges[sym][eid] = info["rate"]
    
    print(f"\nSymbols with funding in {lo}-{hi} min on at least 1 exchange: {len(symbol_exchanges)}")
    
    # Now also get rates for those symbols on OTHER exchanges (even if funding time differs)
    # Because for arb we need: one side pays soon, other side we check the rate
    all_symbols_with_soon_funding = set(symbol_exchanges.keys())
    
    # For each symbol, collect rates from ALL exchanges (not just those with imminent funding)
    symbol_all_rates = defaultdict(dict)
    for sym in all_symbols_with_soon_funding:
        for eid, rates in exchange_data.items():
            if sym in rates:
                symbol_all_rates[sym][eid] = rates[sym]["rate"]
    
    # Compute cross-exchange spreads
    print(f"\n{'='*80}")
    print(f"CROSS-EXCHANGE SPREADS (funding in {lo}-{hi} min, sorted by spread)")
    print(f"{'='*80}")
    
    opportunities = []
    for sym, rates_by_exchange in symbol_all_rates.items():
        eids = list(rates_by_exchange.keys())
        if len(eids) < 2:
            continue
        for i in range(len(eids)):
            for j in range(i+1, len(eids)):
                eid_a, eid_b = eids[i], eids[j]
                rate_a, rate_b = rates_by_exchange[eid_a], rates_by_exchange[eid_b]
                
                # Try both directions
                # Direction 1: Long A, Short B → spread = (-rate_a) + rate_b
                spread1 = (-rate_a + rate_b) * 100
                # Direction 2: Long B, Short A → spread = (-rate_b) + rate_a
                spread2 = (-rate_b + rate_a) * 100
                
                best_spread = max(spread1, spread2)
                if best_spread > 0.3:  # show anything above 0.3%
                    if spread1 > spread2:
                        long_eid, short_eid = eid_a, eid_b
                        long_rate, short_rate = rate_a, rate_b
                    else:
                        long_eid, short_eid = eid_b, eid_a
                        long_rate, short_rate = rate_b, rate_a
                    
                    # Check if at least one side has funding soon
                    has_soon = (sym in symbol_exchanges.get(sym, {}) or
                               long_eid in symbol_exchanges.get(sym, {}) or
                               short_eid in symbol_exchanges.get(sym, {}))
                    
                    opportunities.append({
                        "symbol": sym,
                        "long": long_eid,
                        "short": short_eid,
                        "long_rate": long_rate,
                        "short_rate": short_rate,
                        "spread": best_spread,
                        "soon_exchanges": list(symbol_exchanges.get(sym, {}).keys()),
                    })
    
    opportunities.sort(key=lambda x: x["spread"], reverse=True)
    
    if opportunities:
        for opp in opportunities[:30]:
            soon_mark = ",".join(opp["soon_exchanges"])
            print(
                f"  {opp['symbol']:25s} | L={opp['long']:8s} ({opp['long_rate']:+.6f}) "
                f"S={opp['short']:8s} ({opp['short_rate']:+.6f}) | "
                f"SPREAD={opp['spread']:+.4f}% | funding_soon_on=[{soon_mark}]"
            )
        
        above_05 = [o for o in opportunities if o["spread"] >= 0.5]
        print(f"\n>>> {len(above_05)} opportunities with spread >= 0.5%")
        print(f">>> {len(opportunities)} opportunities with spread >= 0.3%")
    else:
        print("  No opportunities with spread > 0.3% found.")


if __name__ == "__main__":
    asyncio.run(main())
