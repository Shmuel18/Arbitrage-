"""Fetch historical funding-rate + daily-OHLCV data for backtesting.

Usage:
    python scripts/fetch_historical_data.py \\
        --exchange binance --symbol 'BTC/USDT:USDT' --days 30

Writes Parquet files under ``data/history/<exchange>/<slug>.<kind>.parquet``
where ``<slug>`` is the symbol with ``/`` → ``_`` and ``:`` → ``-``. Runs
incrementally: if the file exists we only pull new records since the latest
``timestamp_ms`` in the file.

This script is stand-alone — it does NOT import the bot's ExchangeManager
(which would pull in Redis + config). It only needs ccxt's async client and
pandas. Funding-rate history is a public endpoint on every exchange we use,
so no API keys are required either.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import ccxt.async_support as ccxt
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data" / "history"

logger = logging.getLogger("backtest.fetch")


def _slug(symbol: str) -> str:
    return symbol.replace("/", "_").replace(":", "-")


def _parquet_path(exchange: str, symbol: str, kind: str) -> Path:
    return DATA_DIR / exchange / f"{_slug(symbol)}.{kind}.parquet"


def _last_timestamp_ms(path: Path) -> int | None:
    if not path.exists():
        return None
    df = pd.read_parquet(path, columns=["timestamp_ms"])
    return int(df["timestamp_ms"].max()) if len(df) else None


def _merge_and_save(path: Path, new_df: pd.DataFrame, key: str = "timestamp_ms") -> int:
    if path.exists():
        combined = pd.concat([pd.read_parquet(path), new_df])
    else:
        combined = new_df
    combined = combined.drop_duplicates(key).sort_values(key).reset_index(drop=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    combined.to_parquet(path, index=False)
    return len(combined)


async def fetch_funding_rates(
    client, symbol: str, since_ms: int, until_ms: int, page_limit: int = 1000,
) -> list[dict]:
    if not client.has.get("fetchFundingRateHistory"):
        raise RuntimeError(f"{client.id} does not support fetchFundingRateHistory")

    records: list[dict] = []
    cursor = since_ms
    while cursor < until_ms:
        batch = await client.fetch_funding_rate_history(
            symbol, since=cursor, limit=page_limit,
        )
        if not batch:
            break
        for r in batch:
            ts = int(r["timestamp"])
            if ts >= until_ms:
                break
            records.append(
                {
                    "timestamp_ms": ts,
                    "symbol": symbol,
                    "funding_rate": float(r["fundingRate"]),
                }
            )
        next_cursor = int(batch[-1]["timestamp"]) + 1
        if next_cursor <= cursor:
            break  # defensive: exchange returned same-or-earlier timestamp
        cursor = next_cursor
    return records


async def fetch_ohlcv_daily(
    client, symbol: str, since_ms: int, until_ms: int, page_limit: int = 1000,
) -> list[dict]:
    records: list[dict] = []
    cursor = since_ms
    while cursor < until_ms:
        batch = await client.fetch_ohlcv(
            symbol, timeframe="1d", since=cursor, limit=page_limit,
        )
        if not batch:
            break
        for row in batch:
            ts = int(row[0])
            if ts >= until_ms:
                break
            records.append(
                {
                    "timestamp_ms": ts,
                    "symbol": symbol,
                    "open": float(row[1]),
                    "high": float(row[2]),
                    "low": float(row[3]),
                    "close": float(row[4]),
                    "volume": float(row[5]),
                }
            )
        next_cursor = int(batch[-1][0]) + 1
        if next_cursor <= cursor:
            break
        cursor = next_cursor
    return records


async def run(args: argparse.Namespace) -> int:
    exchange_id = args.exchange.lower()
    symbol = args.symbol
    until_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    since_ms = until_ms - int(args.days * 86_400_000)

    klass = getattr(ccxt, exchange_id, None)
    if klass is None:
        logger.error("unknown exchange: %s", exchange_id)
        return 1

    client = klass({"options": {"defaultType": "swap"}, "enableRateLimit": True})
    try:
        await client.load_markets()
        if symbol not in client.markets:
            logger.error("symbol %s not on %s", symbol, exchange_id)
            return 1

        kinds = (
            ["funding", "ohlcv-1d"]
            if args.kind == "both"
            else [args.kind]
        )

        for kind in kinds:
            path = _parquet_path(exchange_id, symbol, kind)
            last = _last_timestamp_ms(path)
            start = max(since_ms, (last or 0) + 1)
            if start >= until_ms:
                print(f"[{exchange_id}] {kind}: already up to date")
                continue

            fetcher = fetch_funding_rates if kind == "funding" else fetch_ohlcv_daily
            print(
                f"[{exchange_id}] {kind}: fetching {symbol} "
                f"from {datetime.fromtimestamp(start / 1000, timezone.utc):%Y-%m-%d %H:%M} "
                f"to {datetime.fromtimestamp(until_ms / 1000, timezone.utc):%Y-%m-%d %H:%M}"
            )
            records = await fetcher(client, symbol, start, until_ms)
            if records:
                total = _merge_and_save(path, pd.DataFrame(records))
                print(f"  → +{len(records)} new rows, total={total}  ({path.relative_to(REPO_ROOT)})")
            else:
                print(f"  → no new rows returned by {exchange_id}")

        return 0
    finally:
        await client.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--exchange", required=True, help="ccxt id, e.g. binance / bybit / gateio")
    ap.add_argument("--symbol", required=True, help="ccxt symbol, e.g. 'BTC/USDT:USDT'")
    ap.add_argument("--days", type=int, default=30, help="how many days back (from now) to fetch")
    ap.add_argument(
        "--kind",
        default="both",
        choices=["funding", "ohlcv-1d", "both"],
        help="which dataset(s) to fetch",
    )
    sys.exit(asyncio.run(run(ap.parse_args())))


if __name__ == "__main__":
    main()
