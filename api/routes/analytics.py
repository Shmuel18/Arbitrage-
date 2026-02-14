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
    """Get P&L over time"""
    try:
        if not redis_client:
            return {
                "data_points": [],
                "total_pnl": 0,
                "count": 0
            }
        
        pnl_key = "trinity:pnl:timeseries"
        cutoff_time = (datetime.utcnow() - timedelta(hours=hours)).timestamp()
        
        pnl_data = await redis_client._client.zrangebyscore(
            pnl_key,
            cutoff_time,
            float('inf'),
            withscores=True
        )
        
        if not pnl_data:
            return {
                "data_points": [],
                "total_pnl": 0,
                "count": 0
            }
        
        # Parse and calculate
        data_points = []
        total_pnl = 0
        
        for i in range(0, len(pnl_data), 2):
            if i + 1 < len(pnl_data):
                pnl = float(pnl_data[i])
                timestamp = pnl_data[i + 1]
                total_pnl += pnl
                data_points.append({
                    "pnl": pnl,
                    "cumulative_pnl": total_pnl,
                    "timestamp": timestamp
                })
        
        return {
            "data_points": data_points,
            "total_pnl": total_pnl,
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
