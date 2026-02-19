"""Investigate RIVER rapid cycling."""
import json
from datetime import datetime, timezone, timedelta

cutoff = datetime(2026, 2, 18, 23, 44, tzinfo=timezone.utc)
end = datetime(2026, 2, 19, 0, 2, tzinfo=timezone.utc)

for logfile in ['logs/execution.log']:
    print(f"\n{'='*70}")
    print(f"  {logfile} — RIVER events 23:44–00:02 UTC")
    print(f"{'='*70}")
    with open(logfile, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                ts_str = rec.get('ts', '')
                if not ts_str:
                    continue
                dt = datetime.fromisoformat(ts_str)
                if dt < cutoff or dt > end:
                    continue
                full = json.dumps(rec)
                if 'RIVER' not in full:
                    continue
                lvl = rec.get('level', '?')
                msg = rec.get('msg', '')
                act = rec.get('action', '')
                tid = rec.get('trade_id', '')
                time_str = ts_str[11:19]
                print(f"  {time_str} [{lvl:7s}] {msg}")
                if act:
                    print(f"           act={act} tid={tid[:12] if tid else ''}")
            except Exception:
                pass
