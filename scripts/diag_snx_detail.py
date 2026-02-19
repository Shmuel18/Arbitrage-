"""Check SNX funding details including interval from Binance fundingInfo."""
import asyncio
import time
import ccxt.pro as ccxtpro


async def check():
    # Binance: check fundingInfo for SNX
    ex = ccxtpro.binance()
    await ex.load_markets()
    
    # Check market info
    market = ex.market("SNX/USDT:USDT")
    print(f"Binance market info for SNX:")
    print(f"  symbol: {market['symbol']}")
    print(f"  id: {market['id']}")
    
    # Fetch funding rate
    data = await ex.fetch_funding_rate("SNX/USDT:USDT")
    print(f"\nBinance fetch_funding_rate:")
    print(f"  fundingRate: {data.get('fundingRate')}")
    print(f"  fundingTimestamp: {data.get('fundingTimestamp')}")
    print(f"  timestamp: {data.get('timestamp')}")
    
    now_ms = time.time() * 1000
    ts = data.get("fundingTimestamp")
    if ts:
        hours = (ts - now_ms) / 3600000
        print(f"  hours_until_funding: {hours:.2f}")
    
    # Check raw info
    info = data.get("info", {})
    print(f"\nRaw info keys: {list(info.keys())}")
    for k, v in info.items():
        print(f"  {k}: {v}")
    
    await ex.close()
    
    # Bybit: check
    ex2 = ccxtpro.bybit()
    await ex2.load_markets()
    data2 = await ex2.fetch_funding_rate("SNX/USDT:USDT")
    info2 = data2.get("info", {})
    print(f"\nBybit fetch_funding_rate:")
    print(f"  fundingRate: {data2.get('fundingRate')}")
    print(f"  fundingTimestamp: {data2.get('fundingTimestamp')}")
    ts2 = data2.get("fundingTimestamp")
    if ts2:
        hours2 = (ts2 - now_ms) / 3600000
        print(f"  hours_until_funding: {hours2:.2f}")
    print(f"\nBybit raw info:")
    for k, v in info2.items():
        print(f"  {k}: {v}")

    await ex2.close()


if __name__ == "__main__":
    asyncio.run(check())
