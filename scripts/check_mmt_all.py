"""Check what's in the bot's live funding cache for MMT."""
import asyncio
import ccxt.pro as ccxtpro
import sys
sys.path.insert(0, ".")
from src.exchanges.adapter import ExchangeAdapter


async def check():
    # Create adapter like the bot does
    cfg = {
        "exchange_id": "binance",
        "api_key": "",
        "api_secret": "",
        "position_mode": "oneway",
    }

    ex = ccxtpro.binance({
        "options": {"defaultType": "swap"},
        "enableRateLimit": True,
    })
    await ex.load_markets()

    # Check what _get_funding_interval returns for MMT via adapter
    adapter = ExchangeAdapter.__new__(ExchangeAdapter)
    adapter._exchange = ex
    adapter.exchange_id = "binance"
    adapter._funding_rate_cache = {}
    adapter._MAX_SANE_RATE = __import__("decimal").Decimal("0.03")

    # Fetch funding rate
    data = await ex.fetch_funding_rate("MMT/USDT:USDT")
    interval = adapter._get_funding_interval("MMT/USDT:USDT", data)
    print(f"MMT/USDT:USDT on binance:")
    print(f"  fundingRate:      {data.get('fundingRate')}")
    print(f"  interval detected: {interval}h")
    print(f"  interval field:    {data.get('interval')}")
    print(f"  fundingTimestamp:  {data.get('fundingTimestamp')}")

    # Also check Bybit
    bybit = ccxtpro.bybit({
        "options": {"defaultType": "swap"},
        "enableRateLimit": True,
    })
    await bybit.load_markets()

    data_bybit = await bybit.fetch_funding_rate("MMT/USDT:USDT")
    adapter2 = ExchangeAdapter.__new__(ExchangeAdapter)
    adapter2._exchange = bybit
    adapter2.exchange_id = "bybit"
    interval2 = adapter2._get_funding_interval("MMT/USDT:USDT", data_bybit)
    print(f"\nMMT/USDT:USDT on bybit:")
    print(f"  fundingRate:      {data_bybit.get('fundingRate')}")
    print(f"  interval detected: {interval2}h")
    print(f"  interval field:    {data_bybit.get('interval')}")
    print(f"  fundingTimestamp:  {data_bybit.get('fundingTimestamp')}")
    mkt_bybit = bybit.markets.get("MMT/USDT:USDT", {})
    fi = mkt_bybit.get("info", {}).get("fundingInterval")
    print(f"  info.fundingInterval: {fi}")

    # Check all exchanges
    for name, klass in [("gateio", ccxtpro.gateio), ("okx", ccxtpro.okx), ("kucoin", ccxtpro.kucoinfutures)]:
        try:
            inst = klass({"options": {"defaultType": "swap"}, "enableRateLimit": True})
            await inst.load_markets()
            if "MMT/USDT:USDT" in inst.symbols:
                d = await inst.fetch_funding_rate("MMT/USDT:USDT")
                print(f"\nMMT/USDT:USDT on {name}:")
                print(f"  fundingRate: {d.get('fundingRate')}")
                print(f"  interval:    {d.get('interval')}")
            else:
                print(f"\n{name}: MMT/USDT:USDT not available")
            await inst.close()
        except Exception as e:
            print(f"\n{name}: error - {e}")

    await ex.close()
    await bybit.close()


asyncio.run(check())
