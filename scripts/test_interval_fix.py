"""Test Binance funding interval detection with the fix."""
import asyncio
import sys
sys.path.insert(0, ".")
from src.exchanges.adapter import ExchangeAdapter


async def test():
    cfg = {
        "exchange_id": "binance",
        "api_key": "",
        "api_secret": "",
        "position_mode": "oneway",
    }
    adapter = ExchangeAdapter("binance", cfg)
    await adapter.connect()

    # Check MMT interval
    data = await adapter._exchange.fetch_funding_rate("MMT/USDT:USDT")
    interval = adapter._get_funding_interval("MMT/USDT:USDT", data)
    print(f"MMT/USDT:USDT interval: {interval}h (expected 4h)")
    print(f"  ccxt interval field: {data.get('interval')}")
    print(f"  _funding_intervals cache: {adapter._funding_intervals.get('MMT/USDT:USDT')}")

    # Check BTC interval (should still be 8h)
    data2 = await adapter._exchange.fetch_funding_rate("BTC/USDT:USDT")
    interval2 = adapter._get_funding_interval("BTC/USDT:USDT", data2)
    print(f"\nBTC/USDT:USDT interval: {interval2}h (expected 8h)")

    # Count non-8h symbols
    non_default = {s: h for s, h in adapter._funding_intervals.items() if h != 8}
    print(f"\nNon-8h symbols ({len(non_default)}):")
    for s, h in sorted(non_default.items()):
        print(f"  {s}: {h}h")

    await adapter._exchange.close()


asyncio.run(test())
