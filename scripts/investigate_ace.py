"""Investigate ACE trade timeline."""
import json
from datetime import datetime, timezone

for logfile in ['logs/execution.log', 'logs/risk.log', 'logs/exchanges.log']:
    print(f"\n{'='*70}")
    print(f"  {logfile}")
    print(f"{'='*70}")
    with open(logfile, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                ts = rec.get('ts', '')
                if not ts:
                    continue
                dt = datetime.fromisoformat(ts)
                # Only Feb 18, 18:00-20:00
                if dt.day != 18:
                    continue
                if dt.hour < 18 or dt.hour > 20:
                    continue
                # Check if ACE related
                full = json.dumps(rec)
                if 'ACE' not in full:
                    continue
                lvl = rec.get('level', '?')
                msg = rec.get('msg', '')
                sym = rec.get('symbol', '')
                exch = rec.get('exchange', '')
                act = rec.get('action', '')
                tid = rec.get('trade_id', '')
                data = rec.get('data', '')
                time_str = ts[11:19]
                print(f"  {time_str} [{lvl:7s}] {msg}")
                if sym:
                    print(f"           sym={sym} ex={exch} act={act} tid={tid}")
                if data:
                    print(f"           data={data}")
            except Exception:
                pass
