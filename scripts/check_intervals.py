"""Check CCXT 'interval' field for both exchanges."""
import asyncio
import ccxt.pro as ccxtpro


async def check():
    binance = ccxtpro.binanceusdm({"enableRateLimit": True, "options": {"defaultType": "future"}})
    bybit = ccxtpro.bybit({"enableRateLimit": True, "options": {"defaultType": "swap"}})

    await binance.load_markets()
    await bybit.load_markets()

    symbols = ["RIVER/USDT:USDT", "AXS/USDT:USDT", "RESOLV/USDT:USDT", "BTC/USDT:USDT", "ETH/USDT:USDT"]

    for sym in symbols:
        print(f"=== {sym} ===")
        try:
            bf = await binance.fetch_funding_rate(sym)
            interval = bf.get("interval")
            # Also check market info
            mkt = binance.market(sym)
            info_interval = mkt.get("info", {}).get("fundingInterval")
            print(f"  Binance: interval={interval}, info.fundingInterval={info_interval}")
        except Exception as e:
            print(f"  Binance: {e}")

        try:
            byf = await bybit.fetch_funding_rate(sym)
            interval = byf.get("interval")
            mkt = bybit.market(sym)
            info_interval = mkt.get("info", {}).get("fundingInterval")
            info_interval_hour = mkt.get("info", {}).get("fundingIntervalHour")
            # Also from response info
            resp_interval = byf.get("info", {}).get("fundingIntervalHour")
            print(f"  Bybit:   interval={interval}, market.fundingInterval={info_interval}min, fundingIntervalHour={info_interval_hour or resp_interval}")
        except Exception as e:
            print(f"  Bybit:   {e}")
        print()

    await binance.close()
    await bybit.close()


asyncio.run(check())
