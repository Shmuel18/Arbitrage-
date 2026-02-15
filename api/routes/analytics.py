"""
Analytics API Routes
"""

from fastapi import APIRouter, HTTPException, Query
from datetime import datetime, timedelta
import json
from typing import Optional

redis_client = None

def set_redis_client(client):
    global redis_client
    redis_client = client

router = APIRouter()


@router.get("/performance")
async def get_performance(hours: int = Query(24, ge=1, le=168)):
    """Get performance metrics"""
    try:
        if not redis_client:
            return {"data_points": [], "count": 0}
        
        # Get performance data from Redis time series
        perf_key = "trinity:performance:timeseries"
        cutoff_time = (datetime.utcnow() - timedelta(hours=hours)).timestamp()
        
        perf_data = await redis_client._client.zrangebyscore(
            perf_key,
            cutoff_time,
            float('inf'),
            withscores=True
        )
        
        if not perf_data:
            return {"data_points": [], "count": 0}
        
        # Parse data points
        data_points = []
        for i in range(0, len(perf_data), 2):
            if i + 1 < len(perf_data):
                value = json.loads(perf_data[i])
                timestamp = perf_data[i + 1]
                data_points.append({
                    **value,
                    "timestamp": timestamp
                })
        
        return {
            "data_points": data_points,
            "count": len(data_points),
            "period_hours": hours
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/pnl")
async def get_pnl(hours: int = Query(24, ge=1, le=168)):
    """Get P&L over time - reads from closed trades history (persistent across bot restarts)"""
    try:
        if not redis_client:
            return {
                "data_points": [],
                "total_pnl": 0,
                "count": 0
            }
        
        cutoff_time = (datetime.utcnow() - timedelta(hours=hours)).timestamp()
        
        # ── Read closed trades from history ──────────────────────────
        trades_key = "trinity:trades:history"
        trades_data = await redis_client._client.zrangebyscore(
            trades_key,
            cutoff_time,
            float('inf'),
            withscores=True
        )
        
        data_points = []
        total_pnl = 0.0
        cumulative = 0.0
        
        if trades_data:
            # trades_data = [trade_json, timestamp, trade_json, timestamp, ...]
            for i in range(0, len(trades_data), 2):
                if i + 1 < len(trades_data):
                    try:
                        trade_json = trades_data[i]
                        timestamp = float(trades_data[i + 1])
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
                    except Exception as parse_err:
                        pass
        
        # ── Add unrealized PnL from running snapshots (if bot is active) ──
        latest = await redis_client._client.get("trinity:pnl:latest")
        unrealized_pnl = 0.0
        if latest:
            try:
                pnl_payload = json.loads(latest)
                unrealized_pnl = float(pnl_payload.get('unrealized_pnl', 0))
                total_pnl += unrealized_pnl
            except Exception:
                pass
        
        return {
            "data_points": data_points,
            "total_pnl": total_pnl,
            "realized_pnl": total_pnl - unrealized_pnl,
            "unrealized_pnl": unrealized_pnl,
            "count": len(data_points),
            "period_hours": hours
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/summary")
async def get_summary():
    """Get overall summary statistics"""
    try:
        if not redis_client:
            return {
                "total_pnl": 0,
                "total_trades": 0,
                "win_rate": 0,
                "active_positions": 0,
                "uptime_hours": 0
            }
        
        summary_key = "trinity:summary"
        summary_data = await redis_client._client.get(summary_key)
        
        if not summary_data:
            return {
                "total_pnl": 0,
                "total_trades": 0,
                "win_rate": 0,
                "active_positions": 0,
                "uptime_hours": 0
            }
        
        return json.loads(summary_data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
