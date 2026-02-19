"""
Trinity Bot — 48-Hour Report Generator

Reads logs/trade_journal.jsonl and produces a comprehensive summary
of everything that happened. Run anytime:

    python scripts/report.py              # last 48h
    python scripts/report.py --hours 24   # last 24h
    python scripts/report.py --all        # everything

Output: human-readable summary to console + optional JSON export.
"""

import json
import argparse
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path


def load_journal(path: str, since: datetime = None):
    """Load journal entries, optionally filtering by time."""
    entries = []
    p = Path(path)
    if not p.exists():
        return entries
    with open(p, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                if since:
                    ts = datetime.fromisoformat(entry["ts"])
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    if ts < since:
                        continue
                entries.append(entry)
            except (json.JSONDecodeError, KeyError):
                continue
    return entries


def format_usd(val):
    if val is None:
        return "N/A"
    return f"${val:,.4f}" if abs(val) < 100 else f"${val:,.2f}"


def generate_report(entries):
    """Generate report from journal entries."""
    trades_opened = []
    trades_closed = []
    funding_events = []
    hold_decisions = []
    exit_decisions = []
    balance_snapshots = []
    basis_rejections = []
    errors = []

    for e in entries:
        event = e.get("event", "")
        if event == "trade_open":
            trades_opened.append(e)
        elif event == "trade_close":
            trades_closed.append(e)
        elif event == "funding_paid":
            funding_events.append(e)
        elif event == "hold_decision":
            hold_decisions.append(e)
        elif event == "exit_decision":
            exit_decisions.append(e)
        elif event == "balance_snapshot":
            balance_snapshots.append(e)
        elif event == "basis_reject":
            basis_rejections.append(e)
        elif event == "error":
            errors.append(e)

    # ── Summary stats ──
    total_pnl = sum(
        (t.get("data", {}).get("net_profit") or 0) for t in trades_closed
    )
    total_price_pnl = sum(
        (t.get("data", {}).get("price_pnl") or 0) for t in trades_closed
    )
    total_funding_net = sum(
        (t.get("data", {}).get("funding_net") or 0) for t in trades_closed
    )
    total_fees = sum(
        (t.get("data", {}).get("fees") or 0) for t in trades_closed
    )
    winners = [t for t in trades_closed if (t.get("data", {}).get("net_profit") or 0) > 0]
    losers = [t for t in trades_closed if (t.get("data", {}).get("net_profit") or 0) < 0]
    win_rate = (len(winners) / len(trades_closed) * 100) if trades_closed else 0

    print()
    print("=" * 70)
    print("  🏛️  TRINITY BOT — PERFORMANCE REPORT")
    print("=" * 70)

    if entries:
        first_ts = entries[0].get("ts", "?")
        last_ts = entries[-1].get("ts", "?")
        print(f"  Period: {first_ts[:19]} → {last_ts[:19]} UTC")
    print()

    # ── Overall P&L ──
    print("  📊 OVERALL P&L")
    print("  " + "─" * 50)
    print(f"  Trades opened:    {len(trades_opened)}")
    print(f"  Trades closed:    {len(trades_closed)}")
    print(f"  Still open:       {len(trades_opened) - len(trades_closed)}")
    print(f"  Win rate:         {win_rate:.1f}% ({len(winners)}W / {len(losers)}L)")
    print(f"  Price PnL:        {format_usd(total_price_pnl)}")
    print(f"  Funding net:      {format_usd(total_funding_net)}")
    print(f"  Fees paid:        {format_usd(total_fees)}")
    print(f"  ─────────────────────────────")
    print(f"  NET PROFIT:       {format_usd(total_pnl)}")
    print()

    # ── Per-trade details ──
    print("  📋 TRADE LOG")
    print("  " + "─" * 50)
    
    # Create a combined timeline
    all_trade_events = {}
    for t in trades_opened:
        tid = t.get("trade_id", "?")
        d = t.get("data", {})
        all_trade_events.setdefault(tid, {"open": t, "close": None, "funding": [], "holds": [], "exit_decision": None})
        all_trade_events[tid]["open"] = t
    for t in trades_closed:
        tid = t.get("trade_id", "?")
        all_trade_events.setdefault(tid, {"open": None, "close": None, "funding": [], "holds": [], "exit_decision": None})
        all_trade_events[tid]["close"] = t
    for f in funding_events:
        tid = f.get("trade_id", "?")
        if tid in all_trade_events:
            all_trade_events[tid]["funding"].append(f)
    for h in hold_decisions:
        tid = h.get("trade_id", "?")
        if tid in all_trade_events:
            all_trade_events[tid]["holds"].append(h)
    for x in exit_decisions:
        tid = x.get("trade_id", "?")
        if tid in all_trade_events:
            all_trade_events[tid]["exit_decision"] = x

    for i, (tid, info) in enumerate(all_trade_events.items(), 1):
        open_data = (info["open"] or {}).get("data", {})
        close_data = (info["close"] or {}).get("data", {}) if info["close"] else {}
        
        symbol = open_data.get("symbol", "?")
        mode = open_data.get("mode", "?")
        long_ex = open_data.get("long_exchange", "?")
        short_ex = open_data.get("short_exchange", "?")
        spread = open_data.get("spread_pct", "?")
        net = open_data.get("net_pct", "?") 
        opened_at = (info["open"] or {}).get("ts", "?")[:19]
        
        pnl = close_data.get("net_profit")
        duration = close_data.get("duration_min")
        exit_reason = close_data.get("exit_reason", "")
        funding_count = len(info["funding"])
        hold_count = len(info["holds"])
        is_open = info["close"] is None
        
        status = "🟢 OPEN" if is_open else ("🟩 WIN" if (pnl or 0) >= 0 else "🟥 LOSS")
        
        print(f"\n  Trade #{i}: {tid}")
        print(f"    {status} | {symbol} | {mode}")
        print(f"    {long_ex} ↔ {short_ex}")
        print(f"    Opened: {opened_at} UTC")
        if open_data.get("entry_reason"):
            print(f"    Why entered: {open_data['entry_reason']}")
        if not is_open:
            closed_at = info["close"].get("ts", "?")[:19]
            print(f"    Closed: {closed_at} UTC ({duration:.0f} min)")
            print(f"    Why exited: {exit_reason}")
            # Entry/exit prices
            print(f"    ── Prices ──")
            print(f"      Entry: L=${open_data.get('entry_price_long', '?')}  S=${open_data.get('entry_price_short', '?')}")
            print(f"      Exit:  L=${close_data.get('exit_price_long', '?')}  S=${close_data.get('exit_price_short', '?')}")
            # Funding rates at entry vs exit
            _efl = open_data.get("long_funding_rate")
            _efr = open_data.get("short_funding_rate")
            _xfl = close_data.get("exit_funding_long")
            _xfr = close_data.get("exit_funding_short")
            if _efl is not None and _efr is not None:
                print(f"    ── Funding Rates ──")
                print(f"      Entry: {long_ex}={float(_efl)*100:+.4f}%  {short_ex}={float(_efr)*100:+.4f}%")
                if _xfl is not None and _xfr is not None:
                    print(f"      Exit:  {long_ex}={float(_xfl)*100:+.4f}%  {short_ex}={float(_xfr)*100:+.4f}%")
            # Per-leg breakdown
            long_pnl = close_data.get("long_pnl")
            short_pnl = close_data.get("short_pnl")
            print(f"    ── Per-Leg PnL ──")
            print(f"      LONG  {long_ex}: {format_usd(long_pnl)}")
            print(f"      SHORT {short_ex}: {format_usd(short_pnl)}")
            # Totals
            fi = close_data.get("funding_income")
            fc = close_data.get("funding_cost")
            fn = close_data.get("funding_net")
            fees = close_data.get("fees")
            pp = close_data.get("profit_pct")
            inv = close_data.get("invested")
            print(f"    ── Totals ──")
            print(f"      Price PnL:  {format_usd(close_data.get('price_pnl'))}")
            if fi is not None:
                print(f"      Funding:    +{format_usd(fi)} income  -{format_usd(fc)} cost  = {format_usd(fn)} net")
            else:
                print(f"      Funding net: {format_usd(fn)}")
            print(f"      Fees:       -{format_usd(fees)}")
            print(f"      Invested:   {format_usd(inv)}")
            pp_str = f"  ({float(pp):.3f}%)" if pp is not None else ""
            print(f"      NET PROFIT: {format_usd(pnl)}{pp_str}")
        else:
            entry_l = open_data.get("entry_price_long", "?")
            entry_s = open_data.get("entry_price_short", "?")
            print(f"    Entry:  L=${entry_l} S=${entry_s}")
            lq = open_data.get("long_qty", "?")
            not_val = open_data.get("notional", "?")
            print(f"    Qty:    {lq} | Notional: {format_usd(not_val) if not_val else '?'}")
        
        if funding_count:
            print(f"    Funding payments: {funding_count}×")
            for f in info["funding"]:
                fd = f.get("data", {})
                fx = fd.get("exchange", "?")
                fr = fd.get("rate", "?")
                fp = fd.get("estimated_payment", "?")
                print(f"      {f.get('ts', '?')[:19]} | {fx} | rate={fr} | est=${fp}")
        if hold_count:
            print(f"    Hold decisions: {hold_count}×")

    # ── Basis rejections ──
    if basis_rejections:
        print(f"\n\n  🚫 BASIS INVERSION REJECTIONS: {len(basis_rejections)}")
        print("  " + "─" * 50)
        # Group by symbol
        by_symbol = defaultdict(int)
        for b in basis_rejections:
            bd = b.get("data", {})
            sym = bd.get("symbol", "?")
            by_symbol[sym] += 1
        for sym, count in sorted(by_symbol.items(), key=lambda x: -x[1]):
            print(f"    {sym}: {count}× rejected")

    # ── Balance history ──
    if balance_snapshots:
        print(f"\n\n  💰 BALANCE HISTORY ({len(balance_snapshots)} snapshots)")
        print("  " + "─" * 50)
        for snap in balance_snapshots:
            ts = snap.get("ts", "?")[:19]
            bd = snap.get("data", {})
            total = bd.get("total", 0)
            bals = bd.get("balances", {})
            parts = " | ".join(f"{k}=${v:.2f}" for k, v in bals.items() if v is not None)
            print(f"    {ts} | Total: ${total:.2f} | {parts}")

    # ── Errors ──
    if errors:
        print(f"\n\n  ❌ ERRORS: {len(errors)}")
        print("  " + "─" * 50)
        for err in errors[:20]:  # cap at 20
            ts = err.get("ts", "?")[:19]
            msg = err.get("data", {}).get("message", "?")
            print(f"    {ts}: {msg[:100]}")

    print()
    print("=" * 70)
    print()

    return {
        "total_pnl": total_pnl,
        "trades_opened": len(trades_opened),
        "trades_closed": len(trades_closed),
        "win_rate": win_rate,
        "total_price_pnl": total_price_pnl,
        "total_funding_net": total_funding_net,
        "total_fees": total_fees,
        "basis_rejections": len(basis_rejections),
        "errors": len(errors),
    }


def main():
    parser = argparse.ArgumentParser(description="Trinity Bot Report Generator")
    parser.add_argument("--hours", type=int, default=48, help="Hours to look back (default: 48)")
    parser.add_argument("--all", action="store_true", help="Show all history")
    parser.add_argument("--json", action="store_true", help="Also output JSON summary")
    parser.add_argument("--journal", default="logs/trade_journal.jsonl", help="Path to journal file")
    args = parser.parse_args()

    since = None
    if not args.all:
        since = datetime.now(timezone.utc) - timedelta(hours=args.hours)

    entries = load_journal(args.journal, since=since)
    if not entries:
        print(f"\nNo journal entries found in {args.journal}")
        print("The journal starts recording after the bot is restarted with the new code.")
        print("Run: python scripts/report.py --all")
        return

    summary = generate_report(entries)

    if args.json:
        out_path = Path("logs/report_summary.json")
        with open(out_path, "w") as f:
            json.dump(summary, f, indent=2)
        print(f"JSON summary saved to {out_path}")


if __name__ == "__main__":
    main()
