"""Quick check: NKN/USDT:USDT status on KuCoin Futures."""
import ccxt

k = ccxt.kucoinfutures({"options": {"defaultType": "swap"}})
k.load_markets()
m = k.markets.get("NKN/USDT:USDT", {})
info = m.get("info", {})
print("=== NKN/USDT:USDT on KuCoin Futures ===")
print(f"  multiplier : {info.get('multiplier')}")
print(f"  lotSize    : {info.get('lotSize')}")
print(f"  status     : {info.get('status')}")

ob = k.fetch_order_book("NKN/USDT:USDT", limit=20)
print(f"\nOrder Book:")
print(f"  Bids: {ob['bids'][:3] if ob['bids'] else 'EMPTY'}")
print(f"  Asks: {ob['asks'][:3] if ob['asks'] else 'EMPTY'}")

t = k.fetch_ticker("NKN/USDT:USDT")
print(f"\nTicker:")
print(f"  Last: {t['last']}")
print(f"  Bid:  {t['bid']}")
print(f"  Ask:  {t['ask']}")
print(f"  Vol24h (base): {t['baseVolume']}")
print(f"  Vol24h (quote): {t['quoteVolume']}")

# Also check Binance for comparison
print("\n=== NKN/USDT:USDT on Binance ===")
b = ccxt.binance({"options": {"defaultType": "swap"}})
b.load_markets()
bm = b.markets.get("NKN/USDT:USDT", {})
print(f"  active: {bm.get('active')}")
print(f"  status: {bm.get('info', {}).get('status')}")
try:
    bt = b.fetch_ticker("NKN/USDT:USDT")
    print(f"  Last: {bt['last']}, Vol: {bt['baseVolume']}")
except Exception as e:
    print(f"  Ticker error: {e}")
