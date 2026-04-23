"""Load historical Parquet data and produce an aligned funding-event stream.

The ingestion script writes one funding file and one OHLCV file per
``(exchange, symbol)``. At backtest time we merge funding events from two or
more exchanges into a single timeline, grouped by *funding window* — a
``funding_interval_hours``-wide bucket — because different exchanges timestamp
the same funding moment a few hundred ms apart.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data" / "history"


def _slug(symbol: str) -> str:
    return symbol.replace("/", "_").replace(":", "-")


def _path(exchange: str, symbol: str, kind: str) -> Path:
    return DATA_DIR / exchange / f"{_slug(symbol)}.{kind}.parquet"


def load_funding(exchange: str, symbol: str) -> pd.DataFrame:
    path = _path(exchange, symbol, "funding")
    if not path.exists():
        raise FileNotFoundError(
            f"no funding data for {exchange}/{symbol} — run "
            f"scripts/fetch_historical_data.py --exchange {exchange} "
            f"--symbol '{symbol}' --kind funding"
        )
    return pd.read_parquet(path).sort_values("timestamp_ms").reset_index(drop=True)


def load_ohlcv(exchange: str, symbol: str) -> pd.DataFrame:
    path = _path(exchange, symbol, "ohlcv-1d")
    if not path.exists():
        raise FileNotFoundError(
            f"no OHLCV data for {exchange}/{symbol} — run "
            f"scripts/fetch_historical_data.py --exchange {exchange} "
            f"--symbol '{symbol}' --kind ohlcv-1d"
        )
    return pd.read_parquet(path).sort_values("timestamp_ms").reset_index(drop=True)


@dataclass
class FundingEvent:
    timestamp_ms: int                 # window start (floored)
    rates: dict[str, float]           # exchange_id → funding rate
    prices: dict[str, float]          # exchange_id → approx mark price (daily close)


def _daily_close_price(ohlcv_df: pd.DataFrame, event_ts_ms: int) -> float | None:
    """Look up the daily close for the day containing ``event_ts_ms``.

    Falls back to the most recent prior day's close when the exact day is
    absent (e.g. events at the very start of the data range).
    """
    day_ms = (event_ts_ms // 86_400_000) * 86_400_000
    exact = ohlcv_df[ohlcv_df.timestamp_ms == day_ms]
    if len(exact):
        return float(exact.iloc[0]["close"])
    prior = ohlcv_df[ohlcv_df.timestamp_ms < event_ts_ms]
    if len(prior):
        return float(prior.iloc[-1]["close"])
    return None


def build_events(
    symbol: str,
    exchanges: Sequence[str],
    funding_interval_hours: int = 8,
) -> list[FundingEvent]:
    """Merge per-exchange funding events into one sorted stream by funding window."""
    if len(exchanges) < 2:
        raise ValueError("backtest needs at least two exchanges")

    interval_ms = funding_interval_hours * 3_600_000
    funding = {ex: load_funding(ex, symbol) for ex in exchanges}
    ohlcv = {ex: load_ohlcv(ex, symbol) for ex in exchanges}

    # window_start → {exchange: rate}
    buckets: dict[int, dict[str, float]] = {}
    for ex, df in funding.items():
        for ts, rate in zip(df.timestamp_ms, df.funding_rate):
            win = (int(ts) // interval_ms) * interval_ms
            buckets.setdefault(win, {})[ex] = float(rate)

    events: list[FundingEvent] = []
    for win in sorted(buckets):
        prices: dict[str, float] = {}
        for ex, df in ohlcv.items():
            p = _daily_close_price(df, win)
            if p is not None:
                prices[ex] = p
        events.append(
            FundingEvent(timestamp_ms=win, rates=buckets[win], prices=prices)
        )
    return events
