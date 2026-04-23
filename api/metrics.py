"""
Prometheus `/metrics` endpoint.

Polls the latest bot state from Redis on each scrape and publishes it as
Prometheus gauges. We read from Redis (not from bot memory) because the
API and the scanner/controller update the same keys — so this keeps the
metrics module decoupled from the execution path and free of new wiring
through the controller mixins.

Scrape cadence is set in monitoring/prometheus/prometheus.yml (15 s).
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
from typing import TYPE_CHECKING

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Gauge,
    generate_latest,
)

if TYPE_CHECKING:
    from src.storage.redis_client import RedisClient


logger = logging.getLogger("trinity.api.metrics")

# Dedicated registry so we control exactly what gets emitted (no Python
# process metrics we don't need, no cross-module collisions).
REGISTRY = CollectorRegistry()

_BOT_UP = Gauge(
    "ratebridge_bot_up",
    "1 if the bot has published a balance snapshot in the last 5 minutes",
    registry=REGISTRY,
)
_ACTIVE_POSITIONS = Gauge(
    "ratebridge_active_positions",
    "Number of currently open trading positions",
    registry=REGISTRY,
)
_CONNECTED_EXCHANGES = Gauge(
    "ratebridge_connected_exchanges",
    "Number of exchanges reporting a balance",
    registry=REGISTRY,
)
_OPPORTUNITIES_TOTAL = Gauge(
    "ratebridge_opportunities_total",
    "Opportunities found in the most recent scan",
    registry=REGISTRY,
)
_BEST_NET_PCT = Gauge(
    "ratebridge_best_net_pct",
    "Net % of the single best opportunity in the most recent scan",
    registry=REGISTRY,
)
_TRADES_TOTAL = Gauge(
    "ratebridge_trades_completed_total",
    "Cumulative number of completed trades (lifetime)",
    registry=REGISTRY,
)
_TRADES_WINNING = Gauge(
    "ratebridge_trades_winning_total",
    "Cumulative number of winning trades (lifetime)",
    registry=REGISTRY,
)
_TOTAL_PNL = Gauge(
    "ratebridge_total_pnl_usd",
    "Cumulative realized PnL in USD (lifetime)",
    registry=REGISTRY,
)
_PNL_24H = Gauge(
    "ratebridge_pnl_24h_usd",
    "Realized PnL in USD over the last 24 hours",
    registry=REGISTRY,
)
_EXCHANGE_BALANCE = Gauge(
    "ratebridge_exchange_balance_usd",
    "Per-exchange equity balance in USD",
    ["exchange"],
    registry=REGISTRY,
)
_TOTAL_BALANCE = Gauge(
    "ratebridge_total_balance_usd",
    "Sum of all exchange balances in USD",
    registry=REGISTRY,
)


async def _read_json(redis: "RedisClient", key: str) -> object | None:
    raw = await redis.get(key)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


async def _update_gauges(redis: "RedisClient") -> None:
    # ── Balances + bot-up flag + per-exchange labels ──
    balances_doc = await _read_json(redis, "trinity:balances")
    bot_up = 0.0
    if isinstance(balances_doc, dict):
        balances = balances_doc.get("balances") or {}
        _CONNECTED_EXCHANGES.set(len(balances))
        _TOTAL_BALANCE.set(float(balances_doc.get("total") or 0.0))
        for exchange, bal in balances.items():
            try:
                _EXCHANGE_BALANCE.labels(exchange=str(exchange)).set(float(bal or 0))
            except (TypeError, ValueError):
                continue

        # Bot is "up" if we got a snapshot in the last 5 min.
        updated_raw = balances_doc.get("updated_at")
        if updated_raw:
            try:
                updated = _dt.datetime.fromisoformat(str(updated_raw))
                if updated.tzinfo is None:
                    updated = updated.replace(tzinfo=_dt.timezone.utc)
                age_s = (_dt.datetime.now(_dt.timezone.utc) - updated).total_seconds()
                if age_s < 300:
                    bot_up = 1.0
            except ValueError:
                pass
    _BOT_UP.set(bot_up)

    # ── Positions ──
    positions_doc = await _read_json(redis, "trinity:positions")
    if isinstance(positions_doc, list):
        _ACTIVE_POSITIONS.set(len(positions_doc))
    elif isinstance(positions_doc, dict):
        # Some deployments wrap with {"positions": [...]}.
        inner = positions_doc.get("positions")
        _ACTIVE_POSITIONS.set(len(inner) if isinstance(inner, list) else 0)

    # ── Opportunities ──
    opps_doc = await _read_json(redis, "trinity:opportunities")
    if isinstance(opps_doc, dict):
        opps = opps_doc.get("opportunities") or []
        _OPPORTUNITIES_TOTAL.set(len(opps))
        if opps:
            try:
                _BEST_NET_PCT.set(float(opps[0].get("net_pct") or 0.0))
            except (TypeError, ValueError):
                _BEST_NET_PCT.set(0.0)

    # ── Lifetime counters (stored as string scalars) ──
    for key, gauge in (
        ("trinity:stats:trade_count", _TRADES_TOTAL),
        ("trinity:stats:win_count", _TRADES_WINNING),
        ("trinity:stats:total_pnl", _TOTAL_PNL),
    ):
        raw = await redis.get(key)
        if raw is not None:
            try:
                gauge.set(float(raw))
            except (TypeError, ValueError):
                continue

    # ── 24h PnL — sum the same zset the AI tool reads ──
    cutoff = (
        _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(hours=24)
    ).timestamp()
    try:
        entries = await redis.zrangebyscore(
            "trinity:pnl:timeseries", cutoff, float("inf"),
        )
    except Exception:  # noqa: BLE001 — redis wrapper may raise varied errors
        entries = []
    total_24h = 0.0
    for raw in entries:
        try:
            payload = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
            total_24h += float(payload.get("pnl") or 0.0)
        except (json.JSONDecodeError, AttributeError, TypeError, ValueError):
            continue
    _PNL_24H.set(total_24h)


async def render_metrics(redis: "RedisClient") -> tuple[bytes, str]:
    """Refresh all gauges from Redis, then serialize the registry.

    Returns (body, content_type). Safe to call on every scrape — any
    individual Redis failure logs and falls through so the scraper still
    gets a coherent response.
    """
    try:
        await _update_gauges(redis)
    except Exception as exc:  # noqa: BLE001 — never break the scrape
        logger.warning("metrics refresh failed, serving last values: %s", exc)
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST
