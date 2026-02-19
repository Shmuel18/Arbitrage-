"""Scan ALL funding windows in the next 6 hours across all exchanges.
Find when the next viable entry will be, and which symbols have frequent funding."""

import asyncio
import time
from datetime import datetime, timezone
from collections import defaultdict

import ccxt.async_support as ccxt

EXCHANGES = ["binance", "bybit", "okx", "gateio", "kucoin"]
MIN_SPREAD = 0.3  # Show anything potentially interesting


async def main():
    now_utc = datetime.now(timezone.utc)
    now_ms = time.time() * 1000
    print(f"Current time: {now_utc.strftime('%H:%M:%S')} UTC")
    print("=" * 100)

    exs = {}
    for name in EXCHANGES:
        try:
            cls = getattr(ccxt, name)
            ex = cls({"enableRateLimit": True})
            await ex.load_markets()
            exs[name] = ex
            print(f"  Loaded {name} ({len(ex.markets)} markets)")
        except Exception as e:
            print(f"  SKIP {name}: {e}")

    # Fetch ALL funding rates from all exchanges
    print("\nFetching all funding rates...")
    all_rates = {}  # {exchange: {symbol: {rate, next_ts, interval_h, mins_until}}}

    for name, ex in exs.items():
        try:
            rates = await ex.fetch_funding_rates()
            all_rates[name] = {}
            for sym, fr in rates.items():
                if not sym.endswith(":USDT"):
                    continue
                rate = fr.get("fundingRate") or 0
                next_ts = fr.get("fundingTimestamp") or fr.get("nextFundingTimestamp")
                info = fr.get("info", {})
                if not next_ts:
                    for key in ["nextFundingTime", "next_funding_time"]:
                        val = info.get(key)
                        if val:
                            next_ts = int(val)
                            break
                if next_ts and next_ts < 1e12:
                    next_ts = int(next_ts * 1000)

                # Determine interval
                interval_h = 8
                for key in ["fundingIntervalHours", "fundingRateInterval", "interval"]:
                    val = info.get(key)
                    if val:
                        try:
                            val_str = str(val).replace("h", "")
                            interval_h = int(val_str)
                        except:
                            pass
                        break

                mins_until = (next_ts - now_ms) / 60_000 if (next_ts and next_ts > now_ms) else None
                all_rates[name][sym] = {
                    "rate": rate,
                    "next_ts": next_ts,
                    "interval_h": interval_h,
                    "mins_until": mins_until,
                }
            print(f"  {name}: {len(all_rates[name])} USDT perps")
        except Exception as e:
            print(f"  {name} ERROR: {e}")

    # ── Find ALL upcoming funding windows in next 6 hours ──
    print("\n" + "=" * 100)
    print("UPCOMING FUNDING WINDOWS (next 6 hours)")
    print("=" * 100)

    # Group by funding time bucket (rounded to 5 min)
    windows = defaultdict(list)  # {bucket_min: [(symbol, exchange, rate, interval, exact_mins)]}
    
    for name, symbols in all_rates.items():
        for sym, data in symbols.items():
            if data["mins_until"] is not None and 0 < data["mins_until"] <= 360:
                bucket = int(data["mins_until"] / 10) * 10  # round to 10-min buckets
                windows[bucket].append({
                    "symbol": sym,
                    "exchange": name,
                    "rate": data["rate"],
                    "interval_h": data["interval_h"],
                    "mins": data["mins_until"],
                })

    # Show timeline
    for bucket_min in sorted(windows.keys()):
        entries = windows[bucket_min]
        h = int(bucket_min / 60)
        m = int(bucket_min % 60)
        funding_time = datetime.fromtimestamp((now_ms + bucket_min * 60000) / 1000, tz=timezone.utc)
        print(f"\n--- ~{h}h{m:02d}m from now ({funding_time.strftime('%H:%M')} UTC) --- {len(entries)} symbols")
        
        # Don't list all, just count by exchange
        by_ex = defaultdict(int)
        for e in entries:
            by_ex[e["exchange"]] += 1
        print(f"    Exchanges: {dict(by_ex)}")

    # ── Now find ACTIONABLE spreads at each window ──
    print("\n" + "=" * 100)
    print("ACTIONABLE SPREADS (>= 0.3%) grouped by funding time")
    print("=" * 100)

    # For each symbol, find cross-exchange pairs with spread and group by when income fires
    results = []
    common_symbols = set()
    for name in all_rates:
        common_symbols.update(all_rates[name].keys())

    for sym in common_symbols:
        # Get all exchanges that have this symbol
        sym_data = {name: all_rates[name][sym] for name in all_rates if sym in all_rates[name]}
        names = list(sym_data.keys())
        
        for i, long_ex in enumerate(names):
            for short_ex in names[i + 1:]:
                for le, se in [(long_ex, short_ex), (short_ex, long_ex)]:
                    lr = sym_data[le]["rate"]
                    sr = sym_data[se]["rate"]
                    
                    long_is_income = lr < 0
                    short_is_income = sr > 0
                    
                    if not long_is_income and not short_is_income:
                        continue
                    
                    long_contrib = abs(lr) * 100 if long_is_income else -abs(lr) * 100
                    short_contrib = abs(sr) * 100 if short_is_income else -abs(sr) * 100
                    spread = long_contrib + short_contrib
                    
                    if spread < MIN_SPREAD:
                        continue
                    
                    # When does income fire?
                    income_times = []
                    if long_is_income and sym_data[le]["mins_until"]:
                        income_times.append(("L:" + le, sym_data[le]["mins_until"], abs(lr) * 100))
                    if short_is_income and sym_data[se]["mins_until"]:
                        income_times.append(("S:" + se, sym_data[se]["mins_until"], abs(sr) * 100))
                    
                    if not income_times:
                        continue
                    
                    # Earliest income
                    earliest = min(income_times, key=lambda x: x[1])
                    
                    results.append({
                        "sym": sym,
                        "le": le, "se": se,
                        "lr": lr, "sr": sr,
                        "spread": spread,
                        "earliest_income_tag": earliest[0],
                        "earliest_income_mins": earliest[1],
                        "earliest_income_pct": earliest[2],
                        "all_income": income_times,
                    })

    # Sort by earliest income time
    results.sort(key=lambda x: x["earliest_income_mins"])

    # Group by time bucket
    time_groups = defaultdict(list)
    for r in results:
        bucket = int(r["earliest_income_mins"] / 30) * 30  # 30-min buckets
        time_groups[bucket].append(r)

    for bucket in sorted(time_groups.keys()):
        group = time_groups[bucket]
        funding_time = datetime.fromtimestamp((now_ms + bucket * 60000) / 1000, tz=timezone.utc)
        h = int(bucket / 60)
        m = int(bucket % 60)
        print(f"\n{'='*80}")
        print(f"  ~{h}h{m:02d}m from now ({funding_time.strftime('%H:%M')} UTC) — {len(group)} pairs with spread >= {MIN_SPREAD}%")
        print(f"{'='*80}")
        
        # Sort by spread descending, show top 10
        group.sort(key=lambda x: -x["spread"])
        for r in group[:10]:
            income_detail = ", ".join([f"{t[0]}={t[2]:.3f}% in {t[1]:.0f}m" for t in r["all_income"]])
            marker = ">>>" if r["spread"] >= 0.5 else "   "
            print(f"  {marker} {r['sym']:25s} L={r['le']:8s}({r['lr']:+.6f}) S={r['se']:8s}({r['sr']:+.6f}) | spread={r['spread']:.3f}% | income: [{income_detail}]")
        if len(group) > 10:
            above_half = sum(1 for r in group if r["spread"] >= 0.5)
            print(f"  ... and {len(group) - 10} more ({above_half} with spread >= 0.5%)")

    # Summary: when is the first 0.5%+ opportunity?
    first_half = [r for r in results if r["spread"] >= 0.5]
    print("\n" + "=" * 100)
    if first_half:
        f = first_half[0]
        h = int(f["earliest_income_mins"] / 60)
        m = int(f["earliest_income_mins"] % 60)
        funding_time = datetime.fromtimestamp((now_ms + f["earliest_income_mins"] * 60000) / 1000, tz=timezone.utc)
        print(f"FIRST 0.5%+ OPPORTUNITY: {f['sym']} L={f['le']} S={f['se']} spread={f['spread']:.3f}%")
        print(f"  Income fires in ~{h}h{m:02d}m ({funding_time.strftime('%H:%M')} UTC)")
        print(f"  Entry window opens: ~{h}h{m-15:02d}m" if m >= 15 else f"  Entry window opens: ~{h-1}h{m+45:02d}m")
    else:
        print("NO 0.5%+ OPPORTUNITIES in the next 6 hours!")

    # Show distribution of intervals
    print("\n" + "=" * 100)
    print("FUNDING INTERVAL DISTRIBUTION (symbols with imminent income < 4h)")
    intervals = defaultdict(int)
    for r in results:
        if r["earliest_income_mins"] <= 240:
            for tag, mins, pct in r["all_income"]:
                ex = tag.split(":")[1]
                sym_interval = all_rates[ex][r["sym"]]["interval_h"]
                intervals[f"{sym_interval}h"] += 1
    for k, v in sorted(intervals.items()):
        print(f"  {k}: {v} pairs")

    for ex in exs.values():
        await ex.close()

if __name__ == "__main__":
    asyncio.run(main())
