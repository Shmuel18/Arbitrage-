"""Summarize last night's trades from Redis"""
import sys, json, subprocess
from datetime import datetime, timezone

result = subprocess.run(
    ['docker', 'exec', 'trinity-redis', 'redis-cli', 'ZRANGE', 'trinity:trades:history', '0', '-1', 'WITHSCORES'],
    capture_output=True, text=True
)
lines = result.stdout.strip().split('\n')
trades = []
i = 0
while i < len(lines):
    try:
        t = json.loads(lines[i])
        ts = float(lines[i+1]) if i+1 < len(lines) else 0
        t['_ts'] = ts
        trades.append(t)
        i += 2
    except:
        i += 1

# Filter last ~12h
cutoff = datetime(2026, 3, 4, 20, 0, tzinfo=timezone.utc).timestamp()
recent = [t for t in trades if t['_ts'] >= cutoff]

if not recent:
    print("No trades found in the last night.")
    sys.exit(0)

total_pnl = sum(t.get('total_pnl', 0) for t in recent)
total_invested = sum(float(t.get('invested', 0)) for t in recent)
winners = [t for t in recent if t.get('total_pnl', 0) > 0]
losers = [t for t in recent if t.get('total_pnl', 0) <= 0]
total_fees = sum(float(t.get('fees_paid_total', 0)) for t in recent)
total_price_pnl = sum(float(t.get('price_pnl', 0)) for t in recent)
total_funding_net = sum(float(t.get('funding_net', 0)) for t in recent)
total_funding_collected = sum(float(t.get('funding_collected_usd', 0)) for t in recent)
avg_hold = sum(float(t.get('hold_minutes', 0)) for t in recent) / len(recent)

print("=" * 55)
print("   TRADES SUMMARY - Last Night")
print("=" * 55)
print(f"  Total trades:      {len(recent)}")
print(f"  Winners:           {len(winners)} | Losers: {len(losers)}")
print(f"  Win rate:          {len(winners)/len(recent)*100:.1f}%")
print(f"  Total PnL:         ${total_pnl:.4f}")
print(f"    Price PnL:       ${total_price_pnl:.4f}")
print(f"    Funding net:     ${total_funding_net:.4f}")
print(f"    Fees paid:       ${total_fees:.4f}")
print(f"    Funding collected: ${total_funding_collected:.4f}")
print(f"  Total invested:    ${total_invested:.2f}")
if total_invested > 0:
    print(f"  ROI:               {total_pnl/total_invested*100:.4f}%")
print(f"  Avg hold time:     {avg_hold:.1f} min")
print()

# By symbol
symbols = {}
for t in recent:
    s = t['symbol']
    if s not in symbols:
        symbols[s] = {'count': 0, 'pnl': 0, 'invested': 0}
    symbols[s]['count'] += 1
    symbols[s]['pnl'] += t.get('total_pnl', 0)
    symbols[s]['invested'] += float(t.get('invested', 0))
print("--- BY SYMBOL ---")
for s, d in sorted(symbols.items(), key=lambda x: x[1]['pnl'], reverse=True):
    roi = d['pnl']/d['invested']*100 if d['invested'] > 0 else 0
    print(f"  {s:<25} {d['count']:>2} trades  PnL=${d['pnl']:>+.4f}  ROI={roi:>+.3f}%")
print()

# By exchange pair
pairs = {}
for t in recent:
    p = f"{t['long_exchange']}<>{t['short_exchange']}"
    if p not in pairs:
        pairs[p] = {'count': 0, 'pnl': 0}
    pairs[p]['count'] += 1
    pairs[p]['pnl'] += t.get('total_pnl', 0)
print("--- BY EXCHANGE PAIR ---")
for p, d in sorted(pairs.items(), key=lambda x: x[1]['pnl'], reverse=True):
    print(f"  {p:<25} {d['count']:>2} trades  PnL=${d['pnl']:>+.4f}")
print()

# Exit reasons
reasons = {}
for t in recent:
    r = t.get('exit_reason', 'unknown')
    if r not in reasons:
        reasons[r] = {'count': 0, 'pnl': 0}
    reasons[r]['count'] += 1
    reasons[r]['pnl'] += t.get('total_pnl', 0)
print("--- BY EXIT REASON ---")
for r, d in sorted(reasons.items(), key=lambda x: x[1]['count'], reverse=True):
    print(f"  {r:<40} {d['count']:>2} trades  PnL=${d['pnl']:>+.4f}")
print()

# All trades detail
print("--- ALL TRADES (chronological) ---")
for t in recent:
    opened = t.get('opened_at', '?')[:19].replace('T', ' ')
    hold = float(t.get('hold_minutes', 0))
    pnl = t.get('total_pnl', 0)
    edge = t.get('entry_edge_pct', '?')
    marker = '+' if pnl > 0 else '-' if pnl < 0 else '='
    print(f"  [{marker}] {t['symbol']:<22} {t['long_exchange']:<8}<>{t['short_exchange']:<8} PnL=${pnl:>+.4f}  hold={hold:>5.0f}m  edge={edge}%  exit={t.get('exit_reason','?')}")
