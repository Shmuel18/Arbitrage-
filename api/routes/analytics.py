"""
Analytics API Routes
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from datetime import datetime, timedelta, timezone
import json
import logging

if TYPE_CHECKING:
    from src.storage.redis_client import RedisClient

from ..deps import require_redis_client

logger = logging.getLogger("trinity.api.analytics")

router = APIRouter(redirect_slashes=False)


@router.get("/performance")
async def get_performance(
    redis_client: RedisClient = Depends(require_redis_client),
    hours: int = Query(24, ge=1, le=4320),
):
    """Get performance metrics"""
    try:
        # Get performance data from Redis time series
        perf_key = "trinity:performance:timeseries"
        cutoff_time = (datetime.now(timezone.utc) - timedelta(hours=hours)).timestamp()
        
        perf_data = await redis_client.zrangebyscore(
            perf_key,
            cutoff_time,
            float('inf'),
            withscores=True
        )
        
        if not perf_data:
            return {"data_points": [], "count": 0}
        
        # Parse data points
        data_points = []
        for item in perf_data:
            try:
                value_json, timestamp = item
                value = json.loads(value_json)
                data_points.append({
                    **value,
                    "timestamp": float(timestamp)
                })
            except Exception as exc:
                logger.debug("Skipping malformed performance data point: %s", exc)
        
        return {
            "data_points": data_points,
            "count": len(data_points),
            "period_hours": hours
        }
    except Exception as e:
        logger.exception("Unexpected error in get_performance")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/pnl")
async def get_pnl(
    redis_client: RedisClient = Depends(require_redis_client),
    hours: int = Query(24, ge=1, le=4320),
):
    """Get P&L over time - reads from closed trades history (persistent across bot restarts)"""
    try:
        cutoff_time = (datetime.now(timezone.utc) - timedelta(hours=hours)).timestamp()
        
        # ── Read closed trades from history ──────────────────────────
        trades_key = "trinity:trades:history"
        trades_data = await redis_client.zrangebyscore(
            trades_key,
            cutoff_time,
            float('inf'),
            withscores=True
        )
        
        data_points = []
        total_pnl = 0.0
        cumulative = 0.0
        
        if trades_data:
            # trades_data = [(trade_json, timestamp), ...] — list of tuples from withscores=True
            for item in trades_data:
                try:
                    trade_json, timestamp = item
                    timestamp = float(timestamp)
                    trade = json.loads(trade_json)
                    
                    # Extract PnL from trade record
                    trade_pnl = 0.0
                    if 'total_pnl' in trade:
                        trade_pnl = float(trade.get('total_pnl', 0))
                    elif 'net_profit' in trade:
                        trade_pnl = float(trade.get('net_profit', 0))
                    
                    cumulative += trade_pnl
                    total_pnl += trade_pnl
                    
                    data_points.append({
                        "pnl": trade_pnl,
                        "cumulative_pnl": cumulative,
                        "timestamp": timestamp,
                        "symbol": trade.get('symbol', '?'),
                    })
                except Exception as exc:
                    logger.debug("Skipping malformed PnL trade entry: %s", exc)
        
        # ── Merge running PnL snapshots for chart continuity ────────
        # status_publisher writes periodic {running, unrealized, realized}
        # snapshots to trinity:pnl:running.  These fill gaps between
        # closed trades so the chart always has data to render.
        try:
            running_data = await redis_client.zrangebyscore(
                "trinity:pnl:running", cutoff_time, float('inf'),
                withscores=True,
            )
            if running_data:
                for member, score in running_data:
                    try:
                        point = json.loads(member)
                        snap_pnl = float(point.get("running", 0))
                        data_points.append({
                            "pnl": 0.0,
                            "cumulative_pnl": snap_pnl,
                            "unrealized": float(point.get("unrealized", 0)),
                            "realized": float(point.get("realized", 0)),
                            "timestamp": float(score),
                        })
                    except Exception as exc:
                        logger.debug("Skipping malformed running PnL point: %s", exc)
        except Exception as exc:
            logger.debug("Failed to read running PnL snapshots: %s", exc)

        # Sort all data points chronologically (trades + running snapshots)
        data_points.sort(key=lambda p: p["timestamp"])

        # ── Add unrealized PnL from running snapshots (if bot is active) ──
        latest = await redis_client.get("trinity:pnl:latest")
        unrealized_pnl = 0.0
        if latest:
            try:
                pnl_payload = json.loads(latest)
                unrealized_pnl = float(pnl_payload.get('unrealized_pnl', 0))
                total_pnl += unrealized_pnl
            except Exception as exc:
                logger.debug("Failed to parse unrealized PnL snapshot: %s", exc)
        
        return {
            "data_points": data_points,
            "total_pnl": total_pnl,
            "realized_pnl": total_pnl - unrealized_pnl,
            "unrealized_pnl": unrealized_pnl,
            "count": len(data_points),
            "period_hours": hours
        }
    except Exception as e:
        logger.exception("Unexpected error in get_pnl")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/summary")
async def get_summary(
    redis_client: RedisClient = Depends(require_redis_client),
):
    """Get overall summary statistics"""
    try:
        summary_key = "trinity:summary"
        summary_data = await redis_client.get(summary_key)
        base = json.loads(summary_data) if summary_data else {
            "total_pnl": 0, "total_trades": 0, "win_rate": 0,
            "active_positions": 0, "uptime_hours": 0,
        }

        # Compute accurate all-time PnL, win-rate and avg from closed trades history
        all_time_pnl = 0.0
        trade_count = 0
        winning = 0
        try:
            trades_raw = await redis_client.zrange("trinity:trades:history", 0, -1)
            for t in trades_raw:
                td = json.loads(t)
                pnl = float(td.get('total_pnl', 0))
                all_time_pnl += pnl
                trade_count += 1
                if pnl > 0:
                    winning += 1
        except Exception as exc:
            logger.debug("Skipping malformed trade in history scan: %s", exc)

        base['all_time_pnl'] = round(all_time_pnl, 4)
        base['avg_pnl'] = round(all_time_pnl / trade_count, 4) if trade_count > 0 else 0.0
        base['total_trades'] = trade_count
        base['win_rate'] = round(winning / trade_count, 3) if trade_count > 0 else 0.0
        return base
    except Exception as e:
        logger.exception("Unexpected error in get_summary")
        raise HTTPException(status_code=500, detail="Internal server error")
