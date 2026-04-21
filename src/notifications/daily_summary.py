"""Daily summary — async task that publishes a digest at a configured time.

Runs as a long-lived `asyncio.create_task(daily_summary_loop(...))`. At the
configured local hour (default 23:55 Asia/Jerusalem), it:

1. Reads closed trades from `trinity:trades:history` for the last 24 hours.
2. Computes total PnL, trade count, win rate, best/worst trade, top 3
   performing symbols.
3. Calls `publisher.publish_alert(..., alert_type="daily_summary")`, which
   triggers the Telegram fan-out and records the summary in the AlertBell.
4. Sleeps until tomorrow's configured time, then repeats.

The loop is defensive: any exception in the compute/publish step is logged
and the loop continues. A failed summary must never kill the bot.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import json
import logging
from typing import TYPE_CHECKING, Any, Dict, List

if TYPE_CHECKING:
    from src.api.publisher import APIPublisher
    from src.core.config import TelegramConfig
    from src.storage.redis_client import RedisClient

logger = logging.getLogger("trinity.daily_summary")


# ── Scheduling ────────────────────────────────────────────────────

def _seconds_until_next(hour: int, minute: int, tz_name: str) -> float:
    """Seconds from now until the next local (hour, minute) in `tz_name`."""
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(tz_name)
    except Exception:
        # Fallback to system local when tz unknown (e.g. stripped-down images).
        logger.warning("Unknown tz %s; using system local.", tz_name)
        tz = None
    now = _dt.datetime.now(tz)
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += _dt.timedelta(days=1)
    return (target - now).total_seconds()


# ── Data aggregation ──────────────────────────────────────────────

async def _collect_today(redis: "RedisClient", window_hours: int = 24) -> Dict[str, Any]:
    """Read trinity:trades:history trades from the last `window_hours` hours
    and compute summary stats. Returns a dict ready to be formatted."""
    cutoff = (_dt.datetime.now(_dt.timezone.utc)
              - _dt.timedelta(hours=window_hours)).timestamp()
    raw = await redis.zrangebyscore(
        "trinity:trades:history", cutoff, float("inf"), withscores=True,
    )

    trades: List[Dict[str, Any]] = []
    for item in raw or []:
        try:
            trade_json, _score = item
            trades.append(json.loads(trade_json))
        except Exception as exc:
            logger.debug("Skipping malformed trade entry: %s", exc)

    if not trades:
        return {
            "trade_count": 0,
            "total_pnl": 0.0,
            "win_rate": 0.0,
            "wins": 0,
            "losses": 0,
            "best_trade": None,
            "worst_trade": None,
            "top_symbols": [],
        }

    def _pnl(t: Dict[str, Any]) -> float:
        return float(t.get("total_pnl") or t.get("net_profit") or 0.0)

    pnls = [_pnl(t) for t in trades]
    wins = sum(1 for p in pnls if p > 0)
    losses = sum(1 for p in pnls if p < 0)
    total = sum(pnls)
    win_rate = (wins / len(pnls)) if pnls else 0.0

    # Best / worst by PnL
    best = max(trades, key=_pnl)
    worst = min(trades, key=_pnl)

    # Top 3 symbols by aggregate PnL
    by_symbol: Dict[str, float] = {}
    for t in trades:
        sym = t.get("symbol", "?")
        by_symbol[sym] = by_symbol.get(sym, 0.0) + _pnl(t)
    top_symbols = sorted(by_symbol.items(), key=lambda kv: kv[1], reverse=True)[:3]

    return {
        "trade_count": len(trades),
        "total_pnl": total,
        "win_rate": win_rate,
        "wins": wins,
        "losses": losses,
        "best_trade": {"symbol": best.get("symbol", "?"), "pnl": _pnl(best)},
        "worst_trade": {"symbol": worst.get("symbol", "?"), "pnl": _pnl(worst)},
        "top_symbols": [{"symbol": s, "pnl": p} for s, p in top_symbols],
    }


# ── Formatting ────────────────────────────────────────────────────

def _format_summary(stats: Dict[str, Any]) -> str:
    """Produce the message body. Emoji+prefix is added by format_alert()."""
    if stats["trade_count"] == 0:
        return "No closed trades in the last 24 hours."

    def _sign(v: float) -> str:
        return f"+${v:.2f}" if v >= 0 else f"-${abs(v):.2f}"

    lines = [
        f"Trades: {stats['trade_count']}  ({stats['wins']}W / {stats['losses']}L)",
        f"Total PnL: {_sign(stats['total_pnl'])}",
        f"Win rate: {stats['win_rate'] * 100:.1f}%",
    ]
    if stats["best_trade"]:
        b = stats["best_trade"]
        lines.append(f"Best: {b['symbol']} {_sign(b['pnl'])}")
    if stats["worst_trade"] and stats["worst_trade"]["pnl"] < 0:
        w = stats["worst_trade"]
        lines.append(f"Worst: {w['symbol']} {_sign(w['pnl'])}")
    if stats["top_symbols"]:
        top = "  ·  ".join(
            f"{s['symbol']} {_sign(s['pnl'])}" for s in stats["top_symbols"]
        )
        lines.append(f"Top symbols: {top}")
    return "\n".join(lines)


# ── Main loop ─────────────────────────────────────────────────────

async def daily_summary_loop(
    publisher: "APIPublisher",
    redis: "RedisClient",
    cfg: "TelegramConfig",
    shutdown_event: asyncio.Event,
) -> None:
    """Schedule + publish the daily summary in a loop.

    Respects `shutdown_event` so graceful shutdowns don't block on the
    long sleep. The `asyncio.wait(..., return_when=FIRST_COMPLETED)` trick
    lets us race the sleep against shutdown.
    """
    logger.info(
        "Daily summary scheduled: %02d:%02d %s",
        cfg.daily_summary_hour, cfg.daily_summary_minute, cfg.daily_summary_tz,
    )

    while not shutdown_event.is_set():
        wait_s = _seconds_until_next(
            cfg.daily_summary_hour, cfg.daily_summary_minute, cfg.daily_summary_tz,
        )
        logger.debug("Next summary in %.0f seconds", wait_s)

        # Race the sleep against shutdown so we don't block for hours.
        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=wait_s)
            return  # Shutdown fired
        except asyncio.TimeoutError:
            pass  # Normal — time to publish

        if shutdown_event.is_set():
            return

        # ── Compute + publish ──────────────────────────────────
        try:
            stats = await _collect_today(redis)
            message = _format_summary(stats)
            await publisher.publish_alert(
                message,
                severity="info",
                alert_type="daily_summary",
            )
            logger.info(
                "Daily summary published: %d trades, pnl=%.2f",
                stats["trade_count"], stats["total_pnl"],
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Daily summary publish failed: %s", exc)
            # Continue — don't exit the loop on one bad day.
