"""Quick check: Is OM/USDT in the scanner results?"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import redis

r = redis.Redis()

# Check trinity:opportunities (published by scanner)
data = r.get("trinity:opportunities")
if data:
    opps = json.loads(data)
    if isinstance(opps, list):
        all_opps = opps
    else:
        all_opps = opps.get("opportunities", []) if isinstance(opps, dict) else []
    print(f"Total opportunities: {len(all_opps)}")
    for o in all_opps[:20]:
        print(f"  {o.get('symbol')} L={o.get('long_exchange')} S={o.get('short_exchange')} "
              f"spread={o.get('immediate_spread_pct', '?')} "
              f"qualified={o.get('qualified', '?')}")

    om = [o for o in all_opps if "OM/" in str(o.get("symbol", ""))]
    print(f"OM opportunities: {len(om)}")
    for o in om:
        print(f"  {o['symbol']} L={o.get('long_exchange')} S={o.get('short_exchange')} "
              f"spread={o.get('immediate_spread_pct', '?')} "
              f"qualified={o.get('qualified', '?')}")
else:
    print("No opportunities data in Redis")

# Also check active trades
keys = r.keys("trinity:trade:*")
print(f"\nActive trades: {len(keys)}")
for k in keys:
    td = r.get(k)
    if td:
        t = json.loads(td)
        print(f"  {t.get('symbol')} state={t.get('state')} id={t.get('trade_id','?')[:15]}")
