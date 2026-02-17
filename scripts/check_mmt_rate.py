"""Check Binance MMT/USDT funding rate: API vs actual."""
import asyncio
import ccxt.pro as ccxtpro


async def check():
    ex = ccxtpro.binance({"options": {"defaultType": "swap"}})
    await ex.load_markets()

    # Single fetch
    single = await ex.fetch_funding_rate("MMT/USDT:USDT")
    print("=== Single fetch_funding_rate ===")
    print(f"  fundingRate:      {single.get('fundingRate')}")
    print(f"  fundingTimestamp: {single.get('fundingTimestamp')}")
    print(f"  interval:         {single.get('interval')}")
    info = single.get("info", {})
    print(f"  info keys:        {list(info.keys())}")
    print(f"  info.lastFundingRate: {info.get('lastFundingRate')}")
    print(f"  info.markPrice:       {info.get('markPrice')}")
    print(f"  info.interestRate:    {info.get('interestRate')}")
    print(f"  info.nextFundingTime: {info.get('nextFundingTime')}")
    print()

    # Batch fetch
    all_rates = await ex.fetch_funding_rates()
    mmt = all_rates.get("MMT/USDT:USDT")
    if mmt:
        print("=== Batch fetch_funding_rates ===")
        print(f"  fundingRate:      {mmt.get('fundingRate')}")
        print(f"  fundingTimestamp: {mmt.get('fundingTimestamp')}")
        print(f"  interval:         {mmt.get('interval')}")
        info2 = mmt.get("info", {})
        print(f"  info.lastFundingRate: {info2.get('lastFundingRate')}")
    else:
        print("MMT not found in batch")

    # Check market info
    mkt = ex.markets.get("MMT/USDT:USDT", {})
    print()
    print("=== Market info ===")
    mi = mkt.get("info", {})
    print(f"  fundingInterval: {mi.get('fundingInterval')}")
    funding = mkt.get("funding", {})
    print(f"  market.funding: {funding}")

    await ex.close()


asyncio.run(check())
