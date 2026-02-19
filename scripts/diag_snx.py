"""Quick diagnostic: check SNX funding timestamps on all exchanges."""
import asyncio
import time
import ccxt.pro as ccxtpro


async def check():
    for eid in ["gateio", "binance", "bybit", "kraken"]:
        try:
            ex = getattr(ccxtpro, eid)()
            await ex.load_markets()
            sym = "SNX/USDT:USDT"
            if sym not in ex.symbols:
                print(f"{eid}: SNX/USDT:USDT not listed")
                await ex.close()
                continue
            data = await ex.fetch_funding_rate(sym)
            ts = data.get("fundingTimestamp")
            rate = data.get("fundingRate")
            now_ms = time.time() * 1000
            mins = (ts - now_ms) / 60000 if ts else None
            if mins is not None:
                print(f"{eid}: rate={rate}, fundingTimestamp={ts}, mins_until={mins:.1f}min")
            else:
                print(f"{eid}: rate={rate}, fundingTimestamp=None")
            await ex.close()
        except Exception as e:
            print(f"{eid}: ERROR - {e}")


if __name__ == "__main__":
    asyncio.run(check())
