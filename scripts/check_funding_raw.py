"""Check what raw data CCXT returns for funding, including interval info."""
import asyncio
import json
import ccxt.pro as ccxtpro


async def check():
    binance = ccxtpro.binanceusdm({"enableRateLimit": True, "options": {"defaultType": "future"}})
    bybit = ccxtpro.bybit({"enableRateLimit": True, "options": {"defaultType": "swap"}})

    await binance.load_markets()
    await bybit.load_markets()

    sym = "RIVER/USDT:USDT"

    # Check market info for funding interval
    print("=== MARKET INFO ===")
    bm = binance.market(sym)
    print(f"Binance market keys: {[k for k in bm.keys() if 'fund' in k.lower()]}")
    print(f"Binance info keys: {list(bm.get('info', {}).keys())}")
    
    bym = bybit.market(sym)
    print(f"Bybit market keys: {[k for k in bym.keys() if 'fund' in k.lower()]}")
    print(f"Bybit info keys: {list(bym.get('info', {}).keys())}")
    if "fundingInterval" in bym.get("info", {}):
        print(f"Bybit fundingInterval: {bym['info']['fundingInterval']}")

    # Check fetchFundingRate raw response
    print("\n=== FUNDING RATE RAW ===")
    bf = await binance.fetch_funding_rate(sym)
    print(f"Binance keys: {list(bf.keys())}")
    # Print info dict which has exchange-specific data
    print(f"Binance info: {json.dumps(bf.get('info', {}), indent=2)}")
    
    byf = await bybit.fetch_funding_rate(sym)
    print(f"\nBybit keys: {list(byf.keys())}")
    print(f"Bybit info: {json.dumps(byf.get('info', {}), indent=2)}")

    # Try fetchFundingRateHistory
    print("\n=== FUNDING RATE HISTORY (last 3) ===")
    try:
        bh = await binance.fetch_funding_rate_history(sym, limit=3)
        for h in bh:
            print(f"Binance: ts={h.get('datetime')}, rate={h.get('fundingRate')}")
    except Exception as e:
        print(f"Binance history error: {e}")

    try:
        byh = await bybit.fetch_funding_rate_history(sym, limit=3)
        for h in byh:
            print(f"Bybit:   ts={h.get('datetime')}, rate={h.get('fundingRate')}")
    except Exception as e:
        print(f"Bybit history error: {e}")

    await binance.close()
    await bybit.close()


asyncio.run(check())
