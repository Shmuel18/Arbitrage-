"""Quick check: fetch funding rates from Binance & Bybit for key symbols.

Shows:
 - Raw rate from each exchange
 - Funding interval (hours)
 - Raw diff (actual rate difference, what min_rate_diff_pct checks)
 - Normalized 8h edge in BPS (what the bot's calculator produces)
"""
import asyncio
from decimal import Decimal
import ccxt.pro as ccxtpro


def get_interval_hours(exchange, symbol: str) -> int:
    """Extract funding interval in hours from market info."""
    market = exchange.market(symbol)
    info = market.get("info", {})
    # Bybit: fundingInterval in minutes
    fi_min = info.get("fundingInterval")
    if fi_min is not None:
        return int(fi_min) // 60
    # Binance: typically 8h, but some are 4h
    # Check adjustedFundingRateCap or just default
    fi_ms = info.get("fundingIntervalHours")
    if fi_ms is not None:
        return int(fi_ms)
    return 8  # default assumption


async def main():
    binance = ccxtpro.binanceusdm({"options": {"defaultType": "future"}})
    bybit = ccxtpro.bybit({"options": {"defaultType": "swap"}})
    await binance.load_markets()
    await bybit.load_markets()

    test_symbols = [
        "ME/USDT:USDT",
        "FLOW/USDT:USDT",
        "SOMI/USDT:USDT",
        "BTC/USDT:USDT",
        "ETH/USDT:USDT",
        "RIVER/USDT:USDT",
        "ACE/USDT:USDT",
        "MYX/USDT:USDT",
    ]

    print()
    header = (
        f"{'Symbol':<20} │ {'Bin rate':>9} {'int':>4} │ "
        f"{'Byb rate':>9} {'int':>4} │ "
        f"{'RawDiff':>9} │ {'Norm8h BPS':>10} │ {'Note'}"
    )
    sep = "─" * len(header)
    print(header)
    print(sep)

    for sym in test_symbols:
        try:
            b_data = await binance.fetch_funding_rate(sym)
            y_data = await bybit.fetch_funding_rate(sym)

            b_rate = Decimal(str(b_data.get("fundingRate", 0) or 0))
            y_rate = Decimal(str(y_data.get("fundingRate", 0) or 0))

            b_int = get_interval_hours(binance, sym)
            y_int = get_interval_hours(bybit, sym)

            raw_diff = abs(b_rate - y_rate)

            # Reproduce the bot's normalization (calculator.py)
            # The scanner tries both directions and picks the best.
            # Direction 1: long=binance, short=bybit
            norm_b = b_rate * Decimal(8) / Decimal(b_int)
            norm_y = y_rate * Decimal(8) / Decimal(y_int)
            edge1 = (norm_y - norm_b) * Decimal("10000")
            # Direction 2: long=bybit, short=binance
            edge2 = (norm_b - norm_y) * Decimal("10000")
            best_edge = max(edge1, edge2)

            # Note column
            note = ""
            if raw_diff < Decimal("0.005"):
                note = "BLOCKED (raw<0.5%)"
            elif best_edge < Decimal("50"):
                note = "below 50bps"
            else:
                note = "TRADEABLE"

            if b_int != y_int:
                note += f" | diff intervals!"

            print(
                f"{sym:<20} │ {float(b_rate)*100:>8.4f}% {b_int:>3}h │ "
                f"{float(y_rate)*100:>8.4f}% {y_int:>3}h │ "
                f"{float(raw_diff)*100:>8.4f}% │ {float(best_edge):>9.1f}   │ {note}"
            )
        except Exception as e:
            print(f"{sym:<20} │ ERROR: {e}")

    await binance.close()
    await bybit.close()


if __name__ == "__main__":
    asyncio.run(main())
