"""Show filtered log history from the last N hours."""
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

hours = float(sys.argv[1]) if len(sys.argv) > 1 else 3
cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
print(f"=== Log history since {cutoff.strftime('%H:%M UTC')} ({hours}h ago) ===\n")

skip_actions = {"scan_complete", "opportunity", "top_opportunities"}
skip_phrases = ["Scan completed", "TOP 5", "opportunities from"]

log_files = [
    "logs/execution.log",
    "logs/risk.log",
    "logs/main.log",
    "logs/exchanges.log",
]

for logfile in log_files:
    lines = []
    try:
        with open(logfile, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    ts_str = rec.get("ts", "")
                    if not ts_str:
                        continue
                    ts = datetime.fromisoformat(ts_str)
                    if ts < cutoff:
                        continue
                    action = rec.get("action", "")
                    if action in skip_actions:
                        continue
                    msg = rec.get("msg", "")
                    if any(p in msg for p in skip_phrases):
                        continue
                    lines.append(rec)
                except Exception:
                    pass
    except FileNotFoundError:
        pass

    if lines:
        print(f"--- {logfile} ({len(lines)} events) ---")
        for rec in lines:
            ts = rec["ts"][11:19]
            lvl = rec.get("level", "?")
            msg = rec.get("msg", "")
            sym = rec.get("symbol", "")
            exch = rec.get("exchange", "")
            act = rec.get("action", "")
            tid = rec.get("trade_id", "")
            prefix = f"{ts} [{lvl:5s}]"
            detail = ""
            if sym:
                detail += f" sym={sym}"
            if exch:
                detail += f" ex={exch}"
            if act:
                detail += f" act={act}"
            if tid:
                detail += f" tid={tid[:12]}"
            print(f"  {prefix} {msg}{detail}")
        print()
    else:
        print(f"--- {logfile} (no events) ---\n")
