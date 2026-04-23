"""Compute summary metrics for a backtest run and render JSON / HTML.

HTML rendering uses plotly (standalone bundle, no network dependency) so the
output file opens fine offline. JSON output is plain dicts — intended to be
checked into commit history for regression comparisons across strategy tweaks.
"""
from __future__ import annotations

import json
import math
import statistics
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

from .engine import BacktestResult
from .portfolio import Trade


def _to_float(x: Decimal | float | int) -> float:
    return float(x) if x is not None else 0.0


@dataclass
class EquityPoint:
    timestamp_ms: int
    cum_pnl_usd: float


@dataclass
class BacktestMetrics:
    trade_count: int
    win_rate: float
    total_pnl_usd: float
    total_funding_usd: float
    total_basis_usd: float
    total_fees_usd: float
    avg_trade_pnl_usd: float
    avg_hold_hours: float
    best_trade_usd: float
    worst_trade_usd: float
    max_drawdown_usd: float
    sharpe_ratio_per_trade: float       # mean / stdev (unitless)
    sharpe_ratio_annualized: float      # scaled by sqrt(trades/year)
    equity_curve: list[EquityPoint]
    exit_reason_counts: dict[str, int]


def _equity_curve(trades: list[Trade]) -> list[EquityPoint]:
    closed = [t for t in trades if not t.is_open and t.exit_ts_ms is not None]
    closed.sort(key=lambda t: t.exit_ts_ms or 0)
    curve: list[EquityPoint] = []
    cum = 0.0
    for t in closed:
        cum += _to_float(t.net_pnl_usd)
        curve.append(EquityPoint(timestamp_ms=t.exit_ts_ms or 0, cum_pnl_usd=cum))
    return curve


def _max_drawdown(curve: list[EquityPoint]) -> float:
    if not curve:
        return 0.0
    peak = curve[0].cum_pnl_usd
    worst = 0.0
    for point in curve:
        peak = max(peak, point.cum_pnl_usd)
        worst = min(worst, point.cum_pnl_usd - peak)
    return worst  # negative-or-zero


def _sharpe(pnls: list[float], trades_per_year: float) -> tuple[float, float]:
    if len(pnls) < 2:
        return 0.0, 0.0
    mean = statistics.fmean(pnls)
    sd = statistics.stdev(pnls)
    if sd == 0:
        return 0.0, 0.0
    per_trade = mean / sd
    annualized = per_trade * math.sqrt(max(trades_per_year, 1.0))
    return per_trade, annualized


def compute_metrics(result: BacktestResult) -> BacktestMetrics:
    trades = result.trades
    closed = [t for t in trades if not t.is_open]
    pnls = [_to_float(t.net_pnl_usd) for t in closed]
    holds = [t.hold_hours for t in closed]

    # Estimate trades-per-year by scaling the sampled range.
    trades_per_year = 0.0
    if len(closed) >= 2:
        start_ms = min(t.entry_ts_ms for t in closed)
        end_ms = max((t.exit_ts_ms or 0) for t in closed)
        span_days = max((end_ms - start_ms) / 86_400_000, 1.0)
        trades_per_year = len(closed) * (365.0 / span_days)

    sharpe_tr, sharpe_ann = _sharpe(pnls, trades_per_year)
    curve = _equity_curve(trades)
    reason_counts: dict[str, int] = {}
    for t in closed:
        key = t.exit_reason or "unknown"
        reason_counts[key] = reason_counts.get(key, 0) + 1

    return BacktestMetrics(
        trade_count=len(trades),
        win_rate=result.win_rate,
        total_pnl_usd=_to_float(result.total_pnl_usd),
        total_funding_usd=_to_float(result.total_funding_usd),
        total_basis_usd=_to_float(result.total_basis_usd),
        total_fees_usd=_to_float(result.total_fees_usd),
        avg_trade_pnl_usd=statistics.fmean(pnls) if pnls else 0.0,
        avg_hold_hours=statistics.fmean(holds) if holds else 0.0,
        best_trade_usd=max(pnls) if pnls else 0.0,
        worst_trade_usd=min(pnls) if pnls else 0.0,
        max_drawdown_usd=_max_drawdown(curve),
        sharpe_ratio_per_trade=sharpe_tr,
        sharpe_ratio_annualized=sharpe_ann,
        equity_curve=curve,
        exit_reason_counts=reason_counts,
    )


def to_json(result: BacktestResult, metrics: BacktestMetrics) -> dict[str, Any]:
    def _trade_dict(t: Trade) -> dict[str, Any]:
        return {
            "symbol": t.symbol,
            "long_exchange": t.long_exchange,
            "short_exchange": t.short_exchange,
            "entry_ts_ms": t.entry_ts_ms,
            "exit_ts_ms": t.exit_ts_ms,
            "hold_hours": t.hold_hours,
            "notional_usd": _to_float(t.notional_usd),
            "funding_usd": _to_float(t.funding_collected_usd),
            "basis_usd": _to_float(t.basis_pnl_usd),
            "fees_usd": _to_float(t.fees_usd),
            "net_pnl_usd": _to_float(t.net_pnl_usd),
            "exit_reason": t.exit_reason,
        }

    return {
        "config": {
            "symbol": result.cfg.symbol,
            "exchange_a": result.cfg.exchange_a,
            "exchange_b": result.cfg.exchange_b,
            "notional_usd": _to_float(result.cfg.notional_usd),
            "min_funding_spread_pct": _to_float(result.cfg.min_funding_spread_pct),
            "max_hold_hours": result.cfg.max_hold_hours,
            "max_collections": result.cfg.max_collections,
            "slippage_bps": _to_float(result.cfg.slippage_bps),
            "funding_interval_hours": result.cfg.funding_interval_hours,
        },
        "metrics": asdict(metrics),
        "trades": [_trade_dict(t) for t in result.trades],
    }


def write_json(path: Path, result: BacktestResult, metrics: BacktestMetrics) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(to_json(result, metrics), fh, indent=2, default=str)


def _fmt_ts(ms: Optional[int]) -> str:
    if ms is None:
        return "(open)"
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M")


def write_html(
    path: Path,
    result: BacktestResult,
    metrics: BacktestMetrics,
) -> None:
    """Write an interactive standalone HTML report (requires plotly)."""
    import plotly.graph_objects as go  # noqa: WPS433 — optional dep

    cfg = result.cfg
    curve = metrics.equity_curve

    fig = go.Figure()
    if curve:
        fig.add_trace(
            go.Scatter(
                x=[datetime.fromtimestamp(p.timestamp_ms / 1000, timezone.utc) for p in curve],
                y=[p.cum_pnl_usd for p in curve],
                mode="lines+markers",
                name="Cumulative P&L (USD)",
                line={"shape": "hv"},
            )
        )
    fig.update_layout(
        title=f"{cfg.symbol} · {cfg.exchange_a} ↔ {cfg.exchange_b}",
        xaxis_title="Exit timestamp (UTC)",
        yaxis_title="Cumulative P&L (USD)",
        template="plotly_white",
        height=420,
    )

    # Trade table
    trade_rows = "".join(
        f"<tr>"
        f"<td>{i}</td>"
        f"<td>{_fmt_ts(t.entry_ts_ms)}</td>"
        f"<td>{_fmt_ts(t.exit_ts_ms)}</td>"
        f"<td>{t.long_exchange} / {t.short_exchange}</td>"
        f"<td>{t.hold_hours:.1f}</td>"
        f"<td>{_to_float(t.funding_collected_usd):+.4f}</td>"
        f"<td>{_to_float(t.basis_pnl_usd):+.4f}</td>"
        f"<td>{_to_float(t.fees_usd):+.4f}</td>"
        f"<td>{_to_float(t.net_pnl_usd):+.4f}</td>"
        f"<td>{t.exit_reason or ''}</td>"
        f"</tr>"
        for i, t in enumerate(result.trades, 1)
    )

    summary_pairs = [
        ("Trades", f"{metrics.trade_count}"),
        ("Win rate", f"{metrics.win_rate * 100:.1f}%"),
        ("Total net P&L (USD)", f"{metrics.total_pnl_usd:+.4f}"),
        ("  funding", f"{metrics.total_funding_usd:+.4f}"),
        ("  basis", f"{metrics.total_basis_usd:+.4f}"),
        ("  fees + slippage", f"{metrics.total_fees_usd:+.4f}"),
        ("Avg trade P&L (USD)", f"{metrics.avg_trade_pnl_usd:+.4f}"),
        ("Avg hold (h)", f"{metrics.avg_hold_hours:.1f}"),
        ("Best / worst trade (USD)", f"{metrics.best_trade_usd:+.4f}  /  {metrics.worst_trade_usd:+.4f}"),
        ("Max drawdown (USD)", f"{metrics.max_drawdown_usd:+.4f}"),
        ("Sharpe (per-trade)", f"{metrics.sharpe_ratio_per_trade:.3f}"),
        ("Sharpe (annualized)", f"{metrics.sharpe_ratio_annualized:.3f}"),
    ]
    summary_rows = "".join(
        f"<tr><th>{label}</th><td>{value}</td></tr>" for label, value in summary_pairs
    )
    reason_rows = "".join(
        f"<tr><td>{reason}</td><td>{count}</td></tr>"
        for reason, count in sorted(metrics.exit_reason_counts.items(), key=lambda kv: -kv[1])
    )

    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Backtest · {cfg.symbol} · {cfg.exchange_a} ↔ {cfg.exchange_b}</title>
<style>
  body {{ font-family: ui-sans-serif, system-ui, sans-serif; margin: 2rem auto; max-width: 1100px; color: #222; }}
  h1 {{ margin-bottom: 0; }}
  .subtitle {{ color: #777; margin-top: 0.2rem; }}
  .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 2rem; margin-top: 1.5rem; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 0.9rem; }}
  th, td {{ padding: 0.35rem 0.6rem; text-align: left; border-bottom: 1px solid #eee; }}
  th {{ color: #555; font-weight: 500; }}
  .trades {{ margin-top: 2rem; }}
  .trades td, .trades th {{ font-variant-numeric: tabular-nums; }}
</style>
</head><body>
<h1>Backtest report</h1>
<p class="subtitle">{cfg.symbol} · {cfg.exchange_a} ↔ {cfg.exchange_b}
· notional ${_to_float(cfg.notional_usd):.0f} · min spread {_to_float(cfg.min_funding_spread_pct) * 100:.3f}%</p>

{fig.to_html(full_html=False, include_plotlyjs='cdn')}

<div class="grid">
  <div>
    <h2>Summary</h2>
    <table>{summary_rows}</table>
  </div>
  <div>
    <h2>Exit reasons</h2>
    <table><tr><th>reason</th><th>count</th></tr>{reason_rows}</table>
  </div>
</div>

<h2 class="trades">Per-trade breakdown</h2>
<table class="trades">
<tr><th>#</th><th>entry</th><th>exit</th><th>L / S</th><th>held h</th>
    <th>funding</th><th>basis</th><th>fees</th><th>net</th><th>reason</th></tr>
{trade_rows}
</table>

</body></html>
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
