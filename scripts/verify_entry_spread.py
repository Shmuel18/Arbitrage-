#!/usr/bin/env python3
"""
Verify if the reported entry spread matches the actual entry prices.
Check for discrepancies between declared spread and calculated spread.
"""

import json
from datetime import datetime
from decimal import Decimal

TARGET_TRADES = {
    "3c584136-e6f": {
        "name": "Trade 1",
        "reported_basis": 0.9825,
    },
    "c1fc804c-3ce": {
        "name": "Trade 2",
        "reported_basis": 0.6529,
    }
}

def parse_timestamp(ts: str) -> datetime:
    """Parse ISO8601 timestamp."""
    return datetime.fromisoformat(ts.replace("+00:00", "+00:00"))

def calculate_basis(long_price: float, short_price: float) -> float:
    """Calculate basis percentage: (long - short) / short * 100"""
    if short_price == 0:
        return 0
    return ((long_price - short_price) / short_price) * 100

def main():
    journal_path = r"c:\Users\shh92\Documents\Arbitrage\logs\trade_journal.jsonl"
    
    print("\n" + "╔" + "═"*78 + "╗")
    print("║" + " ENTRY SPREAD VERIFICATION - DETECTION OF CALCULATION ERRORS ".center(78) + "║")
    print("╚" + "═"*78 + "╝\n")
    
    trades_data = {}
    
    # Read journal and extract all relevant events
    with open(journal_path, 'r') as f:
        for line in f:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            
            trade_id = record.get("trade_id")
            if trade_id not in TARGET_TRADES:
                continue
            
            if trade_id not in trades_data:
                trades_data[trade_id] = {
                    "trade_open": None,
                    "first_snapshot": None,
                }
            
            event = record.get("event")
            data = record.get("data", {})
            ts = record.get("ts")
            
            if event == "trade_open" and trades_data[trade_id]["trade_open"] is None:
                trades_data[trade_id]["trade_open"] = {
                    "ts": ts,
                    "entry_price_long": data.get("entry_price_long"),
                    "entry_price_short": data.get("entry_price_short"),
                    "reported_spread": data.get("spread_pct"),
                    "entry_reason": data.get("entry_reason"),
                }
            
            elif event == "position_snapshot" and trades_data[trade_id]["first_snapshot"] is None:
                trades_data[trade_id]["first_snapshot"] = {
                    "ts": ts,
                    "long_price": data.get("long_price"),
                    "short_price": data.get("short_price"),
                    "snapshot_basis": data.get("immediate_spread"),
                    "minutes_since_funding": data.get("minutes_since_funding"),
                }
    
    # Analyze each trade
    for trade_id, trade_info in TARGET_TRADES.items():
        print(f"\n{'='*80}")
        print(f"{trade_info['name']} ({trade_id})")
        print(f"{'='*80}\n")
        
        data = trades_data.get(trade_id)
        if not data or not data["trade_open"]:
            print("❌ Trade data not found")
            continue
        
        open_data = data["trade_open"]
        snap_data = data["first_snapshot"]
        
        # Get reported values
        entry_long = open_data["entry_price_long"]
        entry_short = open_data["entry_price_short"]
        reported_spread = open_data["reported_spread"]
        
        print("ENTRY DATA (AT ORDER PLACEMENT):")
        print(f"{'─'*80}")
        print(f"Entry Time: {open_data['ts']}")
        print(f"Long Price (entry_price_long): {entry_long}")
        print(f"Short Price (entry_price_short): {entry_short}")
        print(f"Reported Spread: {reported_spread:+.4f}%")
        
        # Calculate what the spread should have been
        if entry_long and entry_short:
            calc_spread = calculate_basis(entry_long, entry_short)
            print(f"\nCALCULATED FROM ENTRY PRICES:")
            print(f"Formula: (Long - Short) / Short * 100")
            print(f"  = ({entry_long} - {entry_short}) / {entry_short} * 100")
            print(f"  = {calc_spread:+.4f}%")
            
            # Check match
            difference = abs(reported_spread - calc_spread)
            match_status = "✅ MATCH" if difference < 0.01 else "❌ MISMATCH"
            
            print(f"\n{match_status}")
            print(f"Reported:  {reported_spread:+.4f}%")
            print(f"Calculated: {calc_spread:+.4f}%")
            print(f"Difference: {difference:+.4f}%")
        
        # Compare with first snapshot
        if snap_data:
            snap_long = snap_data["long_price"]
            snap_short = snap_data["short_price"]
            snapshot_basis = snap_data["snapshot_basis"]
            
            print(f"\n\nFIRST SNAPSHOT (IMMEDIATELY AFTER ENTRY):")
            print(f"{'─'*80}")
            print(f"Time: {snap_data['ts']} ({snap_data['minutes_since_funding']:.1f} min after funding)")
            print(f"Long Price: {snap_long}")
            print(f"Short Price: {snap_short}")
            print(f"Snapshot Basis: {snapshot_basis:+.4f}%")
            
            # Calculate what it should be
            calc_snap_basis = calculate_basis(snap_long, snap_short)
            snap_diff = abs(snapshot_basis - calc_snap_basis)
            snap_match = "✅ MATCH" if snap_diff < 0.01 else "❌ MISMATCH"
            
            print(f"\n{snap_match}")
            print(f"Reported:  {snapshot_basis:+.4f}%")
            print(f"Calculated: {calc_snap_basis:+.4f}%")
            print(f"Difference: {snap_diff:+.4f}%")
            
            # Price movement analysis
            print(f"\n\nPRICE MOVEMENT ANALYSIS:")
            print(f"{'─'*80}")
            print(f"Entry vs First Snapshot (time delta ~6 seconds):\n")
            
            long_delta = snap_long - entry_long
            short_delta = snap_short - entry_short
            long_pct_move = (long_delta / entry_long * 100) if entry_long else 0
            short_pct_move = (short_delta / entry_short * 100) if entry_short else 0
            
            print(f"Long Price:   {entry_long} → {snap_long}")
            print(f"  Move: {long_delta:+.8f} ({long_pct_move:+.4f}%)")
            print(f"\nShort Price:  {entry_short} → {snap_short}")
            print(f"  Move: {short_delta:+.8f} ({short_pct_move:+.4f}%)")
            
            # Spread deterioration
            entry_spread = reported_spread
            snap_spread = snapshot_basis
            spread_move = snap_spread - entry_spread
            
            print(f"\nSpread:       {entry_spread:+.4f}% → {snap_spread:+.4f}%")
            print(f"  Deterioration: {spread_move:+.4f}% (got worse by {abs(spread_move):.4f}%)")
            
            # Root cause
            print(f"\n\nROOT CAUSE ANALYSIS:")
            print(f"{'─'*80}")
            
            if abs(long_pct_move) > abs(short_pct_move):
                print(f"🔴 LONG ARM PRICE MOVED MORE than short")
                print(f"   Long moved: {long_pct_move:+.4f}%")
                print(f"   Short moved: {short_pct_move:+.4f}%")
                if long_pct_move > 0:
                    print(f"   → Prices rose, but short rose slower → Spread narrowed and reversed")
                else:
                    print(f"   → Prices fell, but short fell less → Spread widened")
            else:
                print(f"🔴 SHORT ARM PRICE MOVED MORE than long")
                print(f"   Long moved: {long_pct_move:+.4f}%")
                print(f"   Short moved: {short_pct_move:+.4f}%")
                if short_pct_move > 0:
                    print(f"   → Prices rose, but long rose slower → Spread narrowed and reversed")
                else:
                    print(f"   → Prices fell, but short fell more → Spread narrowed/reversed")
            
            # Assessment
            print(f"\n\nASSESSMENT:")
            print(f"{'─'*80}")
            
            if reported_spread != calc_spread:
                print(f"⚠️  ENTRY SPREAD WAS MISCALCULATED AT ORDER TIME")
                print(f"   Bot thought spread was {reported_spread:+.4f}%")
                print(f"   But it was actually {calc_spread:+.4f}%")
                print(f"   → Bot traded on incorrect information")
            
            if abs(spread_move) > 0.5:
                print(f"\n⚠️  MASSIVE SPREAD DETERIORATION IN <1 MINUTE")
                print(f"   This suggests:")
                print(f"   • Wide bid-ask spreads (especially on bybit/gateio)")
                print(f"   • Execution slippage between two slow APIs")
                print(f"   • Order actually filled at worse price than quoted")
            
        print()

if __name__ == "__main__":
    main()
