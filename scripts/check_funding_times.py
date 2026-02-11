"""Check actual funding times on both exchanges."""
import asyncio
from datetime import datetime, timezone
import ccxt.pro as ccxtpro


async def check():
    binance = ccxtpro.binanceusdm({
        "enableRateLimit": True,
        "options": {"defaultType": "future"},
    })
    bybit = ccxtpro.bybit({
        "enableRateLimit": True,
        "options": {"defaultType": "swap"},
    })

    await binance.load_markets()
    await bybit.load_markets()

    symbols = ["RIVER/USDT:USDT", "AXS/USDT:USDT", "RESOLV/USDT:USDT"]

    now = datetime.now(timezone.utc)
    print(f"Current time: {now.strftime('%H:%M:%S UTC')}\n")

    for sym in symbols:
        print(f"=== {sym} ===")
        try:
            bf = await binance.fetch_funding_rate(sym)
            rate = bf.get("fundingRate", 0)
            next_ts = bf.get("fundingTimestamp")
            if next_ts:
                next_dt = datetime.fromtimestamp(next_ts / 1000, tz=timezone.utc)
                diff = (next_dt - now).total_seconds() / 60
                print(f"  Binance: rate={rate:.6f}, next={next_dt.strftime('%H:%M:%S UTC')} (in {diff:.0f} min)")
            else:
                print(f"  Binance: rate={rate:.6f}, next=UNKNOWN")
        except Exception as e:
            print(f"  Binance: ERROR {e}")

        try:
            byf = await bybit.fetch_funding_rate(sym)
            rate = byf.get("fundingRate", 0)
            next_ts = byf.get("fundingTimestamp")
            if next_ts:
                next_dt = datetime.fromtimestamp(next_ts / 1000, tz=timezone.utc)
                diff = (next_dt - now).total_seconds() / 60
                print(f"  Bybit:   rate={rate:.6f}, next={next_dt.strftime('%H:%M:%S UTC')} (in {diff:.0f} min)")
            else:
                print(f"  Bybit:   rate={rate:.6f}, next=UNKNOWN")
        except Exception as e:
            print(f"  Bybit:   ERROR {e}")
        print()

    await binance.close()
    await bybit.close()


asyncio.run(check())
