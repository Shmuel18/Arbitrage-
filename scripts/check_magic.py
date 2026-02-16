"""Quick check: MAGIC funding timing on both exchanges."""
import ccxt, time

now = time.time() * 1000

for name, cls, opts in [
    ("Binance", ccxt.binance, {"options": {"defaultType": "swap"}}),
    ("Bybit", ccxt.bybit, {"options": {"defaultType": "swap"}}),
]:
    ex = cls(opts)
    ex.load_markets()
    f = ex.fetch_funding_rate("MAGIC/USDT:USDT")
    ts = f.get("fundingTimestamp")
    rate = f.get("fundingRate")
    if ts:
        mins = (ts - now) / 60000
        print(f"{name}: rate={rate}, next in {mins:.1f} min")
    else:
        print(f"{name}: rate={rate}, next_ts=None")
