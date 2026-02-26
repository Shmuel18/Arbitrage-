#!/usr/bin/env python3
"""
Tier Performance Analyzer — parse trade_journal.jsonl and produce:
  • Monthly PnL breakdown by entry tier (TOP / MEDIUM / BAD)
  • Win-rate, average profit, average duration per tier
  • Recommendation: should BAD-tier trades be disabled?

Usage:
    python scripts/analyze_journal.py                # all-time
    python scripts/analyze_journal.py --month 2026-02
    python scripts/analyze_journal.py --last 30      # last N days

No external dependencies required.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional


# ── Helpers ──────────────────────────────────────────────────────

def _parse_ts(ts_str: str) -> Optional[datetime]:
    """Parse ISO timestamp from journal, return UTC datetime or None."""
    if not ts_str:
        return None
    try:
        return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except Exception:
        return None


def _month_key(dt: datetime) -> str:
    return dt.strftime("%Y-%m") if dt else "unknown"


def _pct(numerator: int, denominator: int) -> str:
    if denominator == 0:
        return "  n/a"
    return f"{100 * numerator / denominator:5.1f}%"


def _fmt(value, suffix="", fallback="   n/a") -> str:
    if value is None:
        return fallback
    try:
        return f"{float(value):+.4f}{suffix}"
    except Exception:
        return str(value)


# ── Load journal ─────────────────────────────────────────────────

def load_journal(path: str) -> List[dict]:
    if not os.path.exists(path):
        print(f"❌  No journal found at {path}")
        sys.exit(1)
    entries = []
    with open(path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError as e:
                print(f"⚠️  Skipping malformed line {lineno}: {e}")
    return entries


# ── Pair opens / closes ──────────────────────────────────────────

def pair_events(entries: List[dict]) -> Dict[str, dict]:
    """
    Return a dict  trade_id → {open: dict, close: dict | None}
    """
    trades: Dict[str, dict] = {}
    for e in entries:
        event = e.get("event", "")
        tid = e.get("trade_id")
        if not tid:
            continue
        if event == "trade_open":
            trades.setdefault(tid, {"open": None, "close": None})
            trades[tid]["open"] = e
        elif event == "trade_close":
            trades.setdefault(tid, {"open": None, "close": None})
            trades[tid]["close"] = e
    return trades


# ── Analysis ─────────────────────────────────────────────────────

class TierStats:
    def __init__(self):
        self.trades: int = 0
        self.wins: int = 0           # net_profit > 0
        self.losses: int = 0         # net_profit <= 0
        self.open_positions: int = 0  # no close event yet
        self.total_pnl: float = 0.0
        self.total_funding_income: float = 0.0
        self.total_price_pnl: float = 0.0
        self.total_duration_min: float = 0.0
        self.profit_list: List[float] = []

    def add(self, net_profit: Optional[float], funding_income: Optional[float],
            price_pnl: Optional[float], duration_min: Optional[float]):
        self.trades += 1
        if net_profit is None:
            self.open_positions += 1
            return
        p = float(net_profit)
        self.total_pnl += p
        self.profit_list.append(p)
        if p > 0:
            self.wins += 1
        else:
            self.losses += 1
        if funding_income is not None:
            self.total_funding_income += float(funding_income)
        if price_pnl is not None:
            self.total_price_pnl += float(price_pnl)
        if duration_min is not None:
            self.total_duration_min += float(duration_min)

    @property
    def closed(self) -> int:
        return self.trades - self.open_positions

    @property
    def avg_pnl(self) -> Optional[float]:
        return self.total_pnl / self.closed if self.closed else None

    @property
    def avg_duration_min(self) -> Optional[float]:
        return self.total_duration_min / self.closed if self.closed else None

    @property
    def win_rate_str(self) -> str:
        return _pct(self.wins, self.closed)

    @property
    def avg_pnl_str(self) -> str:
        return _fmt(self.avg_pnl, "%")


def analyze(trades: Dict[str, dict],
            since: Optional[datetime] = None,
            until: Optional[datetime] = None):
    """
    Aggregate trade stats by tier.
    Returns (all_time_stats, monthly_stats) where:
      all_time_stats = {tier: TierStats}
      monthly_stats  = {month_key: {tier: TierStats}}
    """
    all_time: Dict[str, TierStats] = defaultdict(TierStats)
    monthly: Dict[str, Dict[str, TierStats]] = defaultdict(lambda: defaultdict(TierStats))

    for tid, record in trades.items():
        op = record.get("open")
        cl = record.get("close")
        if not op:
            continue  # orphan close without open

        open_ts = _parse_ts(op.get("ts"))
        if open_ts is None:
            continue

        # Date filters
        if since and open_ts < since:
            continue
        if until and open_ts > until:
            continue

        open_data = op.get("data", {})
        tier = (open_data.get("entry_tier") or "unknown").lower()
        month = _month_key(open_ts)

        if cl:
            close_data = cl.get("data", {})
            net_profit = close_data.get("net_profit")
            funding_income = close_data.get("funding_income")
            price_pnl = close_data.get("price_pnl")
            duration_min = close_data.get("duration_min")
        else:
            net_profit = None
            funding_income = None
            price_pnl = None
            duration_min = None

        all_time[tier].add(net_profit, funding_income, price_pnl, duration_min)
        monthly[month][tier].add(net_profit, funding_income, price_pnl, duration_min)

    return all_time, monthly


# ── Printing ─────────────────────────────────────────────────────

TIER_ORDER = ["top", "medium", "bad", "unknown"]
TIER_LABELS = {"top": "🏆  TOP", "medium": "📊  MEDIUM", "bad": "⚠️   BAD", "unknown": "❓  UNKNOWN"}
BAD_TIER_LOSS_THRESHOLD = -0.1  # avg PnL below this → recommend disabling BAD tier


def _print_tier_table(stats: Dict[str, TierStats], title: str):
    print(f"\n{title}")
    print("─" * 88)
    print(f"  {'Tier':<14} {'Trades':>6} {'Open':>5} {'Wins':>5} {'Losses':>7} {'Win%':>6}  "
          f"{'Avg PnL':>9}  {'Total PnL':>10}  {'Avg Min':>8}")
    print("─" * 88)
    any_data = False
    for tier in TIER_ORDER:
        s = stats.get(tier)
        if not s or s.trades == 0:
            continue
        any_data = True
        label = TIER_LABELS.get(tier, tier)
        avg_dur = f"{s.avg_duration_min:.0f}" if s.avg_duration_min is not None else "  n/a"
        print(
            f"  {label:<14} {s.trades:>6} {s.open_positions:>5} {s.wins:>5} "
            f"{s.losses:>7} {s.win_rate_str:>6}  "
            f"{s.avg_pnl_str:>9}  "
            f"{_fmt(s.total_pnl, '%'):>10}  "
            f"{avg_dur:>8}"
        )
    if not any_data:
        print("  (no closed trades in this period)")
    print("─" * 88)


def _bad_tier_recommendation(all_time: Dict[str, TierStats]):
    bad = all_time.get("bad")
    if not bad or bad.closed == 0:
        print("\n💡 BAD TIER: No closed BAD-tier trades — recommendation not yet available.")
        return

    print("\n💡 BAD TIER RECOMMENDATION")
    print("─" * 60)
    print(f"   Closed trades : {bad.closed}")
    print(f"   Win rate       : {bad.win_rate_str}")
    print(f"   Avg PnL        : {bad.avg_pnl_str}")
    print(f"   Total PnL      : {_fmt(bad.total_pnl, '%')}")

    if bad.avg_pnl is not None and bad.avg_pnl < BAD_TIER_LOSS_THRESHOLD:
        print(f"\n   ❌ RECOMMENDATION: DISABLE BAD tier")
        print(f"      Average PnL ({bad.avg_pnl:.4f}%) is below threshold ({BAD_TIER_LOSS_THRESHOLD}%).")
        print(f"      Set  tier_bad_max_adverse_spread: 0  in config.yaml to stop BAD entries.")
    elif bad.avg_pnl is not None and bad.avg_pnl >= 0:
        print(f"\n   ✅ RECOMMENDATION: KEEP BAD tier — still profitable on average.")
    else:
        print(f"\n   ⚠️  RECOMMENDATION: MONITOR — marginally negative, not yet conclusive.")
    print("─" * 60)


# ── Main ─────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Analyze trade journal by entry tier")
    parser.add_argument("--log", default="logs/trade_journal.jsonl", help="Path to trade_journal.jsonl")
    parser.add_argument("--month", help="Filter to a specific month, e.g. 2026-02")
    parser.add_argument("--last", type=int, help="Filter to the last N days")
    args = parser.parse_args()

    entries = load_journal(args.log)

    since: Optional[datetime] = None
    until: Optional[datetime] = None

    if args.last:
        since = datetime.now(timezone.utc) - timedelta(days=args.last)
    elif args.month:
        try:
            year, month = map(int, args.month.split("-"))
            since = datetime(year, month, 1, tzinfo=timezone.utc)
            # first day of next month
            if month == 12:
                until = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
            else:
                until = datetime(year, month + 1, 1, tzinfo=timezone.utc)
        except ValueError:
            print("❌  --month must be in YYYY-MM format")
            sys.exit(1)

    trades = pair_events(entries)
    all_time, monthly = analyze(trades, since=since, until=until)

    print("=" * 88)
    print("  TRINITY BOT — TIER PERFORMANCE REPORT")
    print("=" * 88)
    print(f"  Journal : {args.log}")
    print(f"  Entries : {len(entries)}  |  Unique trades: {len(trades)}")
    if since or until:
        period = f"  From {since.date() if since else '—'}  to  {until.date() if until else 'now'}"
        print(period)

    if since or until:
        # Single-period view
        _print_tier_table(all_time, "📊 FILTERED PERIOD — Tier Breakdown")
    else:
        # All-time + monthly
        _print_tier_table(all_time, "📊 ALL-TIME — Tier Breakdown")

        if monthly:
            for month_key in sorted(monthly.keys()):
                _print_tier_table(monthly[month_key], f"📅 {month_key} — Tier Breakdown")

    _bad_tier_recommendation(all_time)

    # ── Recent trades (last 10 closed) ───────────────────────────
    closed_records = [
        (tid, r) for tid, r in trades.items()
        if r.get("open") and r.get("close")
    ]
    closed_records.sort(key=lambda x: x[1]["open"].get("ts", ""), reverse=True)

    if closed_records:
        print("\n📋 LAST 10 CLOSED TRADES")
        print("─" * 88)
        print(f"  {'Symbol':<14} {'Tier':<8} {'Mode':<12} {'Net PnL':>9}  "
              f"{'F.Income':>9}  {'Dur(min)':>9}  {'Exit Reason'}")
        print("─" * 88)
        for tid, r in closed_records[:10]:
            op = r["open"].get("data", {})
            cl = r["close"].get("data", {})
            symbol = op.get("symbol", "?")
            tier = (op.get("entry_tier") or "?").upper()
            mode = op.get("mode", "?")
            net_profit = cl.get("net_profit")
            funding_income = cl.get("funding_income")
            duration_min = cl.get("duration_min")
            exit_reason = cl.get("exit_reason", "")
            dur_str = f"{float(duration_min):.0f}" if duration_min is not None else "  n/a"
            print(
                f"  {symbol:<14} {tier:<8} {str(mode):<12} {_fmt(net_profit, '%'):>9}  "
                f"{_fmt(funding_income, '%'):>9}  {dur_str:>9}  {exit_reason}"
            )
        print("─" * 88)

    print()


if __name__ == "__main__":
    main()
