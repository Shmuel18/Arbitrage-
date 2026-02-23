"""
Diagnose POWER/USDT opportunity — why isn't bot entering?
Checks: live prices, funding rates, basis, and net calculation.
"""
import asyncio
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decimal import Decimal
from dotenv import load_dotenv
load_dotenv()

from src.core.config import init_config
from src.exchanges.adapter import ExchangeManager

SYMBOL = "POWER/USDT:USDT"
PAIRS = [
    ("bybit",   "gateio"),
    ("binance", "gateio"),
    ("kucoin",  "gateio"),
    ("bitget",  "gateio"),
]

async def main():
    cfg = init_config()
    mgr = ExchangeManager()
    for eid in cfg.enabled_exchanges:
        exc = cfg.exchanges.get(eid)
        if exc:
            d = exc.model_dump()
            d["max_sane_funding_rate"] = float(cfg.trading_params.max_sane_funding_rate)
            mgr.register(eid, d)

    await mgr.connect_all()
    verified = await mgr.verify_all()
    print(f"\n✅ Connected: {verified}\n")

    adapters = mgr._adapters

    print(f"{'='*70}")
    print(f"POWER/USDT DIAGNOSTIC — live prices + funding rates")
    print(f"{'='*70}\n")

    # Get prices from all relevant exchanges
    prices = {}
    funding = {}
    exchanges_to_check = set()
    for l, s in PAIRS:
        exchanges_to_check.add(l)
        exchanges_to_check.add(s)

    for eid in exchanges_to_check:
        if eid not in adapters:
            print(f"  ⚠️  {eid} not connected")
            continue
        adp = adapters[eid]
        try:
            ticker = await adp.get_ticker(SYMBOL)
            price = ticker.get("last") or ticker.get("close") or 0
            prices[eid] = float(price)
        except Exception as e:
            prices[eid] = None
            print(f"  ❌ {eid} price error: {e}")

        try:
            fr = await adp.get_funding_rate(SYMBOL)
            funding[eid] = fr
        except Exception as e:
            funding[eid] = {}
            print(f"  ❌ {eid} funding error: {e}")

    # Print prices
    print("📊 LIVE PRICES:")
    for eid, price in prices.items():
        print(f"  {eid:12s}: ${price:.6f}" if price else f"  {eid:12s}: N/A")

    # Print funding rates
    print("\n💰 FUNDING RATES:")
    for eid, fr in funding.items():
        rate = fr.get("fundingRate", fr.get("funding_rate", "N/A"))
        next_ts = fr.get("nextFundingTime", fr.get("next_timestamp"))
        if next_ts:
            import datetime
            next_dt = datetime.datetime.fromtimestamp(next_ts/1000, tz=datetime.timezone.utc)
            now = datetime.datetime.now(datetime.timezone.utc)
            mins = (next_dt - now).total_seconds() / 60
            next_str = f"{next_dt.strftime('%H:%M UTC')} (in {mins:.0f}min)"
        else:
            next_str = "unknown"
        print(f"  {eid:12s}: rate={float(rate)*100:.6f}%  next={next_str}")

    # Analyze each pair
    print(f"\n{'='*70}")
    print("📈 PAIR ANALYSIS:")
    print(f"{'='*70}")

    entry_cost = float(cfg.trading_params.slippage_buffer_pct + cfg.trading_params.safety_buffer_pct)
    # Estimate fees (0.06% × 2 legs × 2 sides = ~0.18% for most exchanges)
    fees_est = 0.18

    for long_eid, short_eid in PAIRS:
        if long_eid not in prices or short_eid not in prices:
            continue
        if not prices.get(long_eid) or not prices.get(short_eid):
            continue

        long_price = prices[long_eid]
        short_price = prices[short_eid]
        long_rate = float(funding.get(long_eid, {}).get("fundingRate", funding.get(long_eid, {}).get("funding_rate", 0)))
        short_rate = float(funding.get(short_eid, {}).get("fundingRate", funding.get(short_eid, {}).get("funding_rate", 0)))

        spread = (-long_rate + short_rate) * 100
        price_basis = (long_price - short_price) / short_price * 100
        adverse_basis = max(price_basis, 0)
        total_cost = fees_est + entry_cost + adverse_basis
        net = spread - total_cost

        status = "✅ ENTER" if net >= 0.5 else ("⚠️  CLOSE" if net > 0 else "❌ NO")

        print(f"\n  {long_eid}↔{short_eid}:")
        print(f"    Prices:       {long_eid}=${long_price:.4f}  {short_eid}=${short_price:.4f}")
        print(f"    Price basis:  {price_basis:+.4f}%  {'← ADVERSE (costs money)' if price_basis > 0 else '← favorable'}")
        print(f"    Funding:      L={long_rate*100:.6f}%  S={short_rate*100:.6f}%")
        print(f"    Spread:       {spread:.4f}%")
        print(f"    Fees est:     {fees_est:.4f}%")
        print(f"    Buffers:      {entry_cost:.4f}%")
        print(f"    Adverse basis:{adverse_basis:.4f}%")
        print(f"    ─────────────────────────")
        print(f"    NET:          {net:.4f}%   {status}")
        print(f"    Need spread:  >{total_cost:.4f}% to break even")

    print(f"\n{'='*70}")
    print(f"CONFIG THRESHOLDS:")
    print(f"  min_immediate_spread: {cfg.trading_params.min_immediate_spread}%")
    print(f"  min_funding_spread:   {cfg.trading_params.min_funding_spread}%")
    print(f"  min_net_pct:          {cfg.trading_params.min_net_pct}%")
    print(f"{'='*70}\n")

    await mgr.close_all()

if __name__ == "__main__":
    asyncio.run(main())
