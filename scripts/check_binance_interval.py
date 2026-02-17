"""Check Binance market info for different funding intervals."""
import asyncio
import ccxt.pro as ccxtpro


async def check():
    ex = ccxtpro.binance({"options": {"defaultType": "swap"}, "enableRateLimit": True})
    await ex.load_markets()

    # Check MMT (4h funding) vs BTC (8h funding)
    for sym in ["MMT/USDT:USDT", "BTC/USDT:USDT"]:
        mkt = ex.markets.get(sym, {})
        info = mkt.get("info", {})
        print(f"\n=== {sym} ===")
        print(f"  Market keys: {[k for k in mkt.keys() if 'fund' in k.lower()]}")
        print(f"  info keys: {list(info.keys())}")
        # Print all info fields
        for k, v in sorted(info.items()):
            print(f"  info.{k}: {v}")

    # Also try the raw API: /fapi/v1/fundingInfo
    try:
        resp = await ex._exchange.publicGetFapiV1FundingInfo() if hasattr(ex, 'publicGetFapiV1FundingInfo') else None
        if resp:
            for item in resp:
                if item.get("symbol") == "MMTUSDT":
                    print(f"\n=== fundingInfo for MMT ===")
                    print(item)
    except Exception as e:
        print(f"\nfundingInfo API error: {e}")

    # Try direct HTTP request
    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.get("https://fapi.binance.com/fapi/v1/fundingInfo") as resp:
                data = await resp.json()
                for item in data:
                    if item.get("symbol") in ("MMTUSDT", "BTCUSDT"):
                        print(f"\n=== fundingInfo for {item['symbol']} ===")
                        for k, v in item.items():
                            print(f"  {k}: {v}")
    except Exception as e:
        print(f"\nDirect API error: {e}")

    await ex.close()


asyncio.run(check())
