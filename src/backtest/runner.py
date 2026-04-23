"""CLI for the backtest engine.

Example:
    python -m src.backtest.runner \\
        --symbol 'BTC/USDT:USDT' --pair binance,bybit \\
        --notional 100 --min-spread 0.003

Prints a summary and a per-trade breakdown to stdout. Phase 3 will add
equity curves, Sharpe, max drawdown, and HTML output.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal

from .engine import BacktestConfig, run_backtest


def _fmt_ts(ms: int | None) -> str:
    if ms is None:
        return "(open)"
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbol", required=True, help="e.g. 'BTC/USDT:USDT'")
    ap.add_argument("--pair", required=True, help="two exchange ids, comma-separated, e.g. binance,bybit")
    ap.add_argument("--notional", type=float, default=100.0, help="USD notional per trade (default 100)")
    ap.add_argument(
        "--min-spread", type=float, default=0.003,
        help="min gross funding-rate spread per interval to enter (default 0.003 = 0.3%%)",
    )
    ap.add_argument("--max-hold-hours", type=int, default=72)
    ap.add_argument("--max-collections", type=int, default=6)
    ap.add_argument("--slippage-bps", type=float, default=5.0)
    ap.add_argument("--funding-interval", type=int, default=8, help="funding interval hours (default 8)")
    args = ap.parse_args()

    exchanges = [e.strip() for e in args.pair.split(",")]
    if len(exchanges) != 2:
        ap.error("--pair must be exactly two exchange ids, comma-separated")

    cfg = BacktestConfig(
        symbol=args.symbol,
        exchange_a=exchanges[0],
        exchange_b=exchanges[1],
        funding_interval_hours=args.funding_interval,
        notional_usd=Decimal(str(args.notional)),
        min_funding_spread_pct=Decimal(str(args.min_spread)),
        max_hold_hours=args.max_hold_hours,
        max_collections=args.max_collections,
        slippage_bps=Decimal(str(args.slippage_bps)),
    )
    result = run_backtest(cfg)

    print(f"\n=== Backtest: {cfg.symbol}   {cfg.exchange_a} ↔ {cfg.exchange_b} ===")
    print(f"notional per trade : ${cfg.notional_usd}")
    print(f"min gross spread   : {cfg.min_funding_spread_pct * 100:.3f}%")
    print(f"round-trip cost    : {cfg.round_trip_cost_pct() * 100:.3f}% (fees + slippage)")
    print()
    print(f"trades             : {result.trade_count}")
    print(f"win rate           : {result.win_rate * 100:.1f}%")
    print(f"total net P&L      : ${result.total_pnl_usd:+.4f}")
    print(f"  funding          : ${result.total_funding_usd:+.4f}")
    print(f"  basis            : ${result.total_basis_usd:+.4f}")
    print(f"  fees + slippage  : ${result.total_fees_usd:+.4f}")

    if result.trades:
        print("\n── per-trade ─────────────────────────────────────────────────")
        for i, t in enumerate(result.trades, 1):
            print(
                f" {i:2d}. {_fmt_ts(t.entry_ts_ms)} → {_fmt_ts(t.exit_ts_ms):<16}  "
                f"{t.long_exchange[:7]:<7} long / {t.short_exchange[:7]:<7} short  "
                f"| held {t.hold_hours:5.1f} h  "
                f"| net ${t.net_pnl_usd:+7.4f}  "
                f"| {t.exit_reason}"
            )


if __name__ == "__main__":
    main()
