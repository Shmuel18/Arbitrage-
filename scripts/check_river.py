"""Check contractSize and precision for RIVER on each exchange."""
import asyncio
import ccxt.pro as ccxtpro

async def check():
    for exc_id in ['bybit', 'okx']:
        ex = getattr(ccxtpro, exc_id)({'enableRateLimit': True})
        await ex.load_markets()
        sym = 'RIVER/USDT:USDT'
        if sym in ex.markets:
            m = ex.markets[sym]
            print(f"--- {exc_id} ---")
            print(f"  contractSize: {m.get('contractSize')}")
            print(f"  precision.amount: {m['precision']['amount']}")
            print(f"  precision.price: {m['precision']['price']}")
            print(f"  limits.amount.min: {m['limits']['amount']['min']}")
            print(f"  limits.amount.max: {m['limits']['amount']['max']}")
            print(f"  limits.cost.min: {m['limits']['cost']['min']}")
            info = m.get('info', {})
            print(f"  info.lotSz: {info.get('lotSz', 'N/A')}")
            print(f"  info.ctVal: {info.get('ctVal', 'N/A')}")
            print(f"  info.minSz: {info.get('minSz', 'N/A')}")
        else:
            print(f"{exc_id}: RIVER/USDT:USDT NOT FOUND")
        await ex.close()

asyncio.run(check())
