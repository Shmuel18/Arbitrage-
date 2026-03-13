#!/usr/bin/env python3
"""
Analyze basis recovery time for trades that exited due to timeout.
Pulls data from the trades and shows when the basis normalized.
"""

import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional
from collections import defaultdict

# Trade IDs that exited due to basis_hard_stop_30min
TARGET_TRADES = [
    "3c584136-e6f",  # OGN trade 1
    "c1fc804c-3ce",  # OGN trade 2
]

def parse_timestamp(ts: str) -> datetime:
    """Parse ISO8601 timestamp."""
    return datetime.fromisoformat(ts.replace("+00:00", "+00:00"))

def read_trade_journal(filepath: str):
    """Read and parse trade journal."""
    trades_data = defaultdict(lambda: {
        "open": None,
        "close": None,
        "positions": [],
        "funding": [],
    })
    
    try:
        with open(filepath, 'r') as f:
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
                
                event = record.get("event")
                data = record.get("data", {})
                ts = record.get("ts")
                
                if event == "trade_open":
                    trades_data[trade_id]["open"] = {
                        "ts": ts,
                        "symbol": data.get("symbol"),
                        "long_exchange": data.get("long_exchange"),
                        "short_exchange": data.get("short_exchange"),
                        "entry_price_long": data.get("entry_price_long"),
                        "entry_price_short": data.get("entry_price_short"),
                        "spread_pct": data.get("spread_pct"),
                    }
                
                elif event == "position_snapshot":
                    trades_data[trade_id]["positions"].append({
                        "ts": ts,
                        "minutes_since_funding": data.get("minutes_since_funding"),
                        "long_price": data.get("long_price"),
                        "short_price": data.get("short_price"),
                        "immediate_spread": data.get("immediate_spread"),
                    })
                
                elif event == "funding_collected":
                    trades_data[trade_id]["funding"].append({
                        "ts": ts,
                        "collection_num": data.get("collection_num"),
                        "net_payment_usd": data.get("net_payment_usd"),
                        "immediate_spread": data.get("immediate_spread"),
                    })
                
                elif event == "trade_close":
                    trades_data[trade_id]["close"] = {
                        "ts": ts,
                        "exit_reason": data.get("exit_reason"),
                        "duration_min": data.get("duration_min"),
                        "exit_price_long": data.get("exit_price_long"),
                        "exit_price_short": data.get("exit_price_short"),
                        "price_pnl": data.get("price_pnl"),
                        "funding_net": data.get("funding_net"),
                        "net_profit": data.get("net_profit"),
                    }
    
    except FileNotFoundError:
        print(f"Error: File not found: {filepath}")
        return trades_data
    
    return trades_data

def analyze_basis_recovery(trades_data: dict):
    """Analyze basis recovery for each trade."""
    
    for trade_id in TARGET_TRADES:
        trade = trades_data.get(trade_id)
        if not trade:
            print(f"\n❌ Trade {trade_id} not found")
            continue
        
        if not trade["open"] or not trade["close"]:
            print(f"\n⚠️  Trade {trade_id} missing open or close data")
            continue
        
        print(f"\n{'='*80}")
        print(f"Trade ID: {trade_id}")
        print(f"{'='*80}")
        
        symbol = trade["open"]["symbol"]
        entry_time = parse_timestamp(trade["open"]["ts"])
        exit_time = parse_timestamp(trade["close"]["ts"])
        entry_basis = trade["open"]["spread_pct"]
        exit_basis = trade["close"].get("exit_price_long") and trade["close"].get("exit_price_short")
        duration_min = trade["close"]["duration_min"]
        
        print(f"\nSymbol: {symbol}")
        print(f"Long Exchange: {trade['open']['long_exchange']}")
        print(f"Short Exchange: {trade['open']['short_exchange']}")
        print(f"\nEntry Time: {entry_time.strftime('%Y-%m-%d %H:%M:%S')} UTC")
        print(f"Exit Time: {exit_time.strftime('%Y-%m-%d %H:%M:%S')} UTC")
        print(f"Duration: {duration_min:.2f} minutes ({int(duration_min)} min {int((duration_min % 1) * 60)} sec)")
        
        print(f"\nEntry Basis: {entry_basis:+.4f}%")
        print(f"Exit Reason: {trade['close']['exit_reason']}")
        
        # Calculate basis from entry prices
        entry_long = trade["open"]["entry_price_long"]
        entry_short = trade["open"]["entry_price_short"]
        if entry_long and entry_short:
            calc_entry_basis = ((entry_long - entry_short) / entry_short * 100)
            print(f"  (Long: {entry_long}, Short: {entry_short}, Calc: {calc_entry_basis:+.4f}%)")
        
        # Analyze position snapshots to find basis normalization
        print(f"\n{'─'*80}")
        print("Basis Evolution During Hold:")
        print(f"{'─'*80}")
        print(f"{'Time Since Funding':<20} {'Time':<20} {'Long Price':<15} {'Short Price':<15} {'Basis %':<12}")
        print(f"{'-'*82}")
        
        positions = sorted(trade["positions"], key=lambda p: parse_timestamp(p["ts"]))
        
        basis_zero_crossed = False
        time_to_normalize = None
        
        for i, pos in enumerate(positions):
            pos_time = parse_timestamp(pos["ts"])
            mins_since_open = (pos_time - entry_time).total_seconds() / 60
            mins_since_funding = pos.get("minutes_since_funding", 0)
            basis = pos.get("immediate_spread", 0)
            long_price = pos.get("long_price")
            short_price = pos.get("short_price")
            
            # Check if basis crossed to negative
            if not basis_zero_crossed and basis < 0 and entry_basis > 0:
                basis_zero_crossed = True
                print(f"\n⚠️  BASIS CROSSED TO NEGATIVE at {mins_since_open:.1f} min")
                time_to_negative = mins_since_open
            
            # Check if basis recovered to positive (normalized)
            if basis_zero_crossed and basis >= 0 and time_to_normalize is None:
                time_to_normalize = mins_since_open
                print(f"\n✅ BASIS RECOVERED TO POSITIVE at {mins_since_open:.1f} min")
                if time_to_negative is not None:
                    recovery_time = time_to_normalize - time_to_negative
                    print(f"   Recovery time: {recovery_time:.2f} minutes ({int(recovery_time)} min {int((recovery_time % 1) * 60)} sec)")
            
            print(f"{mins_since_funding:<20.1f} {pos_time.strftime('%H:%M:%S'):<20} {long_price!s:<15} {short_price!s:<15} {basis:+.4f}%")
        
        # Check if basis ever recovered
        final_basis = positions[-1].get("immediate_spread", 0) if positions else None
        print(f"\n{'-'*82}")
        print(f"Final Basis at Exit: {final_basis:+.4f}%")
        
        if basis_zero_crossed and time_to_normalize is None:
            print(f"⚠️  BASIS NEVER RECOVERED WITHIN TRADE HOLD PERIOD")
            print(f"   Trade exited with basis: {final_basis:+.4f}%")
        
        # Show funding collections
        if trade["funding"]:
            print(f"\n{'─'*80}")
            print("Funding Collected:")
            print(f"{'─'*80}")
            for funding in trade["funding"]:
                funding_time = parse_timestamp(funding["ts"])
                mins_since_open = (funding_time - entry_time).total_seconds() / 60
                print(f"  Collection {funding['collection_num']} at {mins_since_open:.1f} min: "
                      f"${funding['net_payment_usd']+0:.4f} (basis: {funding.get('immediate_spread', 0):+.4f}%)")
        
        # Final summary
        print(f"\n{'─'*80}")
        print("Trade Result:")
        print(f"{'─'*80}")
        print(f"Price P&L: ${trade['close']['price_pnl']:.4f}")
        print(f"Funding Net: ${trade['close']['funding_net']:.4f}")
        print(f"Net Profit: ${trade['close']['net_profit']:.4f}")

def main():
    journal_path = r"c:\Users\shh92\Documents\Arbitrage\logs\trade_journal.jsonl"
    
    print("Analyzing Basis Recovery for Timeout Trades")
    print("=" * 80)
    
    trades_data = read_trade_journal(journal_path)
    analyze_basis_recovery(trades_data)
    
    # Summary
    print(f"\n{'='*80}")
    print("SUMMARY: Basis Recovery Statistics")
    print(f"{'='*80}")
    
    recovery_times = []
    
    for trade_id in TARGET_TRADES:
        trade = trades_data.get(trade_id)
        if not trade or not trade["positions"]:
            continue
        
        entry_basis = trade["open"]["spread_pct"]
        entry_time = parse_timestamp(trade["open"]["ts"])
        
        basis_negative_time = None
        recovery_time = None
        
        for pos in sorted(trade["positions"], key=lambda p: parse_timestamp(p["ts"])):
            pos_time = parse_timestamp(pos["ts"])
            basis = pos.get("immediate_spread", 0)
            mins_since_open = (pos_time - entry_time).total_seconds() / 60
            
            if basis_negative_time is None and basis < 0 < entry_basis:
                basis_negative_time = mins_since_open
            
            if basis_negative_time is not None and basis >= 0 and recovery_time is None:
                recovery_time = mins_since_open - basis_negative_time
                recovery_times.append(recovery_time)
        
        if recovery_time:
            print(f"\n{trade_id}: Recovery time = {recovery_time:.2f} minutes "
                  f"({int(recovery_time)} min {int((recovery_time % 1) * 60)} sec)")
        else:
            print(f"\n{trade_id}: Basis did NOT fully recover within hold period")
    
    if recovery_times:
        avg_recovery = sum(recovery_times) / len(recovery_times)
        print(f"\n🔍 Average basis recovery time: {avg_recovery:.2f} minutes "
              f"({int(avg_recovery)} min {int((avg_recovery % 1) * 60)} sec)")
    else:
        print(f"\n⚠️  No basis recovery data found")

if __name__ == "__main__":
    main()
