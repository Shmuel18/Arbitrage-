"""Diagnose WHY the scanner is not qualifying opportunities with the new logic.
Connects to exchanges, fetches funding rates + next timestamps, and simulates
the entry-window logic for top pairs."""

import asyncio
import time
from datetime import datetime, timezone

import ccxt.async_support as ccxt

SYMBOLS_TO_CHECK = [
    "INJ/USDT:USDT",
    "SNX/USDT:USDT",
    "ENSO/USDT:USDT",
    "AXS/USDT:USDT",
]

EXCHANGES = ["binance", "bybit", "okx", "gateio", "kraken"]
MAX_WINDOW = 15  # minutes
MIN_SPREAD = 0.5  # percent


async def main():
    now_utc = datetime.now(timezone.utc)
    now_ms = time.time() * 1000
    print(f"Current time: {now_utc.strftime('%H:%M:%S')} UTC")
    print(f"Entry window: {MAX_WINDOW} min | Min spread: {MIN_SPREAD}%")
    print("=" * 90)

    # Load exchanges
    exs = {}
    for name in EXCHANGES:
        try:
            cls = getattr(ccxt, name)
            ex = cls({"enableRateLimit": True})
            await ex.load_markets()
            exs[name] = ex
            print(f"  Loaded {name}")
        except Exception as e:
            print(f"  SKIP {name}: {e}")

    print()

    for symbol in SYMBOLS_TO_CHECK:
        print(f"\n{'='*90}")
        print(f"  {symbol}")
        print(f"{'='*90}")

        # Get funding rate + next timestamp from each exchange
        data = {}
        for name, ex in exs.items():
            if symbol not in ex.markets:
                print(f"  {name}: NOT LISTED")
                continue
            try:
                fr = await ex.fetch_funding_rate(symbol)
                rate = fr.get("fundingRate", 0) or 0
                next_ts = fr.get("fundingTimestamp") or fr.get("nextFundingTimestamp")
                interval = fr.get("fundingRateInterval") or fr.get("interval")

                # Try to get nextFundingTimestamp from info
                info = fr.get("info", {})
                if not next_ts:
                    for key in ["nextFundingTime", "next_funding_time", "nextFundingTimestamp"]:
                        val = info.get(key)
                        if val:
                            next_ts = int(val)
                            break

                if next_ts and next_ts < 1e12:
                    next_ts = int(next_ts * 1000)

                mins_until = (next_ts - now_ms) / 60_000 if (next_ts and next_ts > now_ms) else None

                data[name] = {
                    "rate": rate,
                    "next_ts": next_ts,
                    "mins_until": mins_until,
                    "interval": interval,
                }
                ts_str = datetime.fromtimestamp(next_ts / 1000, tz=timezone.utc).strftime("%H:%M") if next_ts else "N/A"
                mins_str = f"{mins_until:.1f}min" if mins_until else "N/A"
                print(f"  {name:8s}: rate={rate:+.8f} ({rate*100:+.6f}%) | next={ts_str} ({mins_str}) | interval={interval}")
            except Exception as e:
                print(f"  {name:8s}: ERROR {e}")

        # Now simulate entry-window logic for each pair
        print(f"\n  --- PAIR ANALYSIS ---")
        names = list(data.keys())
        for i, long_eid in enumerate(names):
            for short_eid in names[i + 1:]:
                ld = data[long_eid]
                sd = data[short_eid]

                # Try both directions
                for le, se, ldata, sdata in [
                    (long_eid, short_eid, ld, sd),
                    (short_eid, long_eid, sd, ld),
                ]:
                    lr = ldata["rate"]
                    sr = sdata["rate"]

                    # Basic spread = abs(lr) - abs(sr) if lr < 0 (income on long)
                    # Actually spread = (how much we benefit from both sides combined)
                    # Long income contribution: -lr (if lr < 0)
                    # Short income contribution: sr (if sr > 0)
                    long_is_income = lr < 0
                    short_is_income = sr > 0
                    
                    # Calculate immediate spread (for display)
                    long_contrib = abs(lr) * 100 if long_is_income else -abs(lr) * 100
                    short_contrib = abs(sr) * 100 if short_is_income else -abs(sr) * 100
                    spread = long_contrib + short_contrib

                    if spread < 0.3:
                        continue  # Skip low spread directions

                    long_mins = ldata["mins_until"]
                    short_mins = sdata["mins_until"]

                    long_imminent = long_is_income and long_mins is not None and long_mins <= MAX_WINDOW
                    short_imminent = short_is_income and short_mins is not None and short_mins <= MAX_WINDOW

                    # Imminent spread
                    imminent_income = 0
                    imminent_cost = 0
                    if long_imminent:
                        imminent_income += abs(lr) * 100
                    if short_imminent:
                        imminent_income += abs(sr) * 100
                    if not long_is_income and long_mins is not None and long_mins <= MAX_WINDOW:
                        imminent_cost += abs(lr) * 100
                    if not short_is_income and short_mins is not None and short_mins <= MAX_WINDOW:
                        imminent_cost += abs(sr) * 100
                    imminent_spread = imminent_income - imminent_cost

                    # Determine rejection reason
                    if not long_is_income and not short_is_income:
                        reason = "BOTH_COST"
                    elif not (long_imminent or short_imminent):
                        l_reason = ""
                        s_reason = ""
                        if long_is_income:
                            if long_mins is None:
                                l_reason = "L:no_timestamp"
                            elif long_mins > MAX_WINDOW:
                                l_reason = f"L:too_far({long_mins:.0f}min)"
                            else:
                                l_reason = f"L:imminent({long_mins:.0f}min)"
                        else:
                            l_reason = "L:is_cost"
                        if short_is_income:
                            if short_mins is None:
                                s_reason = "S:no_timestamp"
                            elif short_mins > MAX_WINDOW:
                                s_reason = f"S:too_far({short_mins:.0f}min)"
                            else:
                                s_reason = f"S:imminent({short_mins:.0f}min)"
                        else:
                            s_reason = "S:is_cost"
                        reason = f"NO_IMMINENT_INCOME [{l_reason}, {s_reason}]"
                    elif imminent_spread < MIN_SPREAD:
                        reason = f"IMMINENT_SPREAD_LOW ({imminent_spread:.4f}% < {MIN_SPREAD}%)"
                    else:
                        reason = f"QUALIFIED! imminent={imminent_spread:.4f}%"

                    marker = ">>>" if "QUALIFIED" in reason else "   "
                    print(
                        f"  {marker} L={le:8s}({lr:+.6f}) S={se:8s}({sr:+.6f}) | "
                        f"spread={spread:.4f}% | imminent={imminent_spread:.4f}% | "
                        f"{reason}"
                    )

    # Cleanup
    for ex in exs.values():
        await ex.close()


if __name__ == "__main__":
    asyncio.run(main())
