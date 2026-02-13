"""Quick independent check — fetch funding rates from Binance & Bybit and compare."""
import asyncio
from decimal import Decimal
import ccxt.pro as ccxtpro


async def main():
    binance = ccxtpro.binanceusdm({"options": {"defaultType": "future"}})
    bybit = ccxtpro.bybit({"options": {"defaultType": "swap"}})

    await binance.load_markets()
    await bybit.load_markets()

    # Filter to USDT linear perps
    b_symbols = {s for s, m in binance.markets.items() if m.get("swap") and m.get("linear") and m.get("settle") == "USDT"}
    y_symbols = {s for s, m in bybit.markets.items() if m.get("swap") and m.get("linear") and m.get("settle") == "USDT"}
    common = sorted(b_symbols & y_symbols)

    print(f"Common USDT perps: {len(common)}")
    print(f"{'Symbol':<25} {'Binance':>10} {'Bybit':>10} {'Diff':>10} {'B-interval':>11} {'Y-interval':>11}")
    print("-" * 80)

    results = []
    for sym in common:
        try:
            b_fund = await binance.fetch_funding_rate(sym)
            y_fund = await bybit.fetch_funding_rate(sym)

            b_rate = Decimal(str(b_fund.get("fundingRate", 0) or 0))
            y_rate = Decimal(str(y_fund.get("fundingRate", 0) or 0))
            diff = abs(b_rate - y_rate)

            b_interval = b_fund.get("fundingRateInterval", "?")
            y_interval = y_fund.get("fundingRateInterval", "?")

            results.append((sym, b_rate, y_rate, diff, b_interval, y_interval))
        except Exception as e:
            pass  # skip symbols that fail

    # Sort by diff descending
    results.sort(key=lambda x: x[3], reverse=True)

    # Print top 20
    for sym, b_rate, y_rate, diff, b_int, y_int in results[:20]:
        print(f"{sym:<25} {float(b_rate)*100:>9.4f}% {float(y_rate)*100:>9.4f}% {float(diff)*100:>9.4f}% {str(b_int):>11} {str(y_int):>11}")

    await binance.close()
    await bybit.close()


if __name__ == "__main__":
    asyncio.run(main())
