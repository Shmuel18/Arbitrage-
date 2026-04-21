#!/usr/bin/env python3
"""
Analyze what happened to OGN basis after the timeout trades exited.
Check market conditions in the following hours to see recovery.
"""

import json
from datetime import datetime, timedelta
from collections import defaultdict

TARGET_TRADES = {
    "3c584136-e6f": {
        "symbol": "OGN/USDT:USDT",
        "exit_time": "2026-03-12T00:31:25.653968+00:00",
        "exit_basis": -1.0398,  # was -1.039837%
    },
    "c1fc804c-3ce": {
        "symbol": "OGN/USDT:USDT",
        "exit_time": "2026-03-12T08:31:17.346878+00:00",
        "exit_basis": -1.4575,  # was -1.457554%
    }
}

def parse_timestamp(ts: str) -> datetime:
    """Parse ISO8601 timestamp."""
    return datetime.fromisoformat(ts.replace("+00:00", "+00:00"))

def main():
    journal_path = r"c:\Users\shh92\Documents\Arbitrage\logs\trade_journal.jsonl"
    
    print("Looking for OGN market data after trades closed...")
    print("=" * 80)
    
    # Collect all OGN-related events after each trade closed
    events_after_close = defaultdict(list)
    
    with open(journal_path, 'r') as f:
        for line in f:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            
            symbol = record.get("data", {}).get("symbol")
            if symbol != "OGN/USDT:USDT":
                continue
            
            ts = record.get("ts")
            if not ts:
                continue
            
            record_time = parse_timestamp(ts)
            
            # Check if this is after first trade close
            trade1_exit = parse_timestamp(TARGET_TRADES["3c584136-e6f"]["exit_time"])
            trade2_exit = parse_timestamp(TARGET_TRADES["c1fc804c-3ce"]["exit_time"])
            
            # Collect events after first trade, before second trade
            if trade1_exit < record_time < trade2_exit:
                hours_after = (record_time - trade1_exit).total_seconds() / 3600
                if hours_after <= 8:  # Within 8 hours
                    event = record.get("event")
                    if event in ["position_snapshot", "funding_collected", "trade_open", "trade_close"]:
                        events_after_close[1].append({
                            "time": record_time,
                            "hours_after": hours_after,
                            "event": event,
                            "data": record.get("data", {})
                        })
            
            # Collect events after second trade
            elif record_time > trade2_exit:
                hours_after = (record_time - trade2_exit).total_seconds() / 3600
                if hours_after <= 2:  # Within 2 hours
                    event = record.get("event")
                    if event in ["position_snapshot", "funding_collected", "trade_open"]:
                        events_after_close[2].append({
                            "time": record_time,
                            "hours_after": hours_after,
                            "event": event,
                            "data": record.get("data", {})
                        })
    
    # Analyze Trade 1
    print("\n" + "="*80)
    print("TRADE 1: OGN (3c584136-e6f)")
    print("="*80)
    print(f"Exit Time: 2026-03-12 00:31:25 UTC")
    print(f"Exit Basis: -1.0398%\n")
    
    if events_after_close[1]:
        print(f"Found {len(events_after_close[1])} market snapshots after trade 1 closed:")
        print(f"{'─'*80}\n")
        
        for evt in sorted(events_after_close[1], key=lambda e: e["time"]):
            hours = evt["hours_after"]
            mins = int((hours % 1) * 60)
            data = evt["data"]
            
            if evt["event"] == "position_snapshot":
                long_price = data.get("long_price")
                short_price = data.get("short_price")
                basis = data.get("immediate_spread")
                
                symbol = data.get("symbol")
                print(f"⏱️  {hours:.1f}h after exit ({evt['time'].strftime('%H:%M:%S %Z')})")
                print(f"   Long (bybit): {long_price}, Short (gateio): {short_price}")
                print(f"   Basis: {basis:+.4f}%")
                
                if basis > 0:
                    print(f"   ✅ NORMALIZED (basis now positive)")
                print()
            
            elif evt["event"] == "trade_open":
                new_symbol = data.get("symbol")
                spread = data.get("spread_pct")
                print(f"📊 {hours:.1f}h after exit: New trade opened")
                print(f"   Symbol: {new_symbol}, Spread: {spread}%\n")
    else:
        print("No market snapshots found after trade 1")
    
    # Analyze Trade 2
    print("\n" + "="*80)
    print("TRADE 2: OGN (c1fc804c-3ce)")
    print("="*80)
    print(f"Exit Time: 2026-03-12 08:31:17 UTC")
    print(f"Exit Basis: -1.4575%\n")
    
    if events_after_close[2]:
        print(f"Found {len(events_after_close[2])} market snapshots after trade 2 closed:")
        print(f"{'─'*80}\n")
        
        for evt in sorted(events_after_close[2], key=lambda e: e["time"]):
            hours = evt["hours_after"]
            data = evt["data"]
            
            if evt["event"] == "position_snapshot":
                long_price = data.get("long_price")
                short_price = data.get("short_price")
                basis = data.get("immediate_spread")
                
                print(f"⏱️  {hours:.2f}h after exit ({evt['time'].strftime('%H:%M:%S %Z')})")
                print(f"   Long (bybit): {long_price}, Short (gateio): {short_price}")
                print(f"   Basis: {basis:+.4f}%")
                
                if basis > 0:
                    print(f"   ✅ NORMALIZED (basis now positive)")
                print()
            
            elif evt["event"] == "trade_open":
                new_symbol = data.get("symbol")
                spread = data.get("spread_pct")
                print(f"📊 {hours:.2f}h after exit: New trade opened")
                print(f"   Symbol: {new_symbol}, Spread: {spread}%\n")
    else:
        print("No market snapshots found after trade 2")

if __name__ == "__main__":
    main()
