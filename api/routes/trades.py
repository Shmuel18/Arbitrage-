"""
Trades History API Routes
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


@router.get("/")
async def get_trades(
    limit: int = Query(100, ge=1, le=1000),
    hours: Optional[int] = Query(None, ge=1, le=168)
):
    """Get trades history"""
    try:
        if not redis_client:
            return {"trades": [], "count": 0}
        
        # Get trades from Redis sorted set
        trades_key = "trinity:trades:history"
        
        # Calculate time range
        if hours:
            cutoff_time = (datetime.utcnow() - timedelta(hours=hours)).timestamp()
            trades_data = await redis_client._client.zrangebyscore(
                trades_key, 
                cutoff_time, 
                float('inf'), 
                start=0, 
                num=limit,
                withscores=False
            )
        else:
            trades_data = await redis_client._client.zrange(trades_key, -limit, -1, withscores=False)
        
        if not trades_data:
            return {"trades": [], "count": 0}
        
        # Parse trades
        trades = [json.loads(trade) for trade in trades_data if trade]
        trades.reverse()  # Most recent first
        
        # ── Normalize field names for frontend ──────────────────
        def normalize(t: dict) -> dict:
            invested = float(t.get('invested') or 0)
            total_pnl = float(t.get('total_pnl') or 0)
            pnl_pct = (total_pnl / invested) if invested > 0 else 0.0
            entry_edge = t.get('entry_edge_pct')
            return {
                **t,
                # aliases expected by TradesHistory.tsx
                'pnl':           total_pnl,
                'pnl_percentage': pnl_pct,
                'open_time':     t.get('opened_at'),
                'close_time':    t.get('closed_at'),
                'exchanges':     {'long': t.get('long_exchange'), 'short': t.get('short_exchange')},
                'size':          f"${invested:,.0f}",
                'entry_spread':  float(entry_edge) / 100 if entry_edge else None,
                'exit_spread':   None,  # not tracked at exit
            }
        
        trades = [normalize(t) for t in trades]
        
        return {
            "trades": trades,
            "count": len(trades),
            "timestamp": datetime.utcnow().isoformat()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/stats")
async def get_trade_stats():
    """Get trading statistics"""
    try:
        if not redis_client:
            return {
                "total_trades": 0,
                "winning_trades": 0,
                "losing_trades": 0,
                "win_rate": 0,
                "total_pnl": 0,
                "avg_pnl": 0
            }
        
        # Get stats from Redis
        stats_key = "trinity:stats"
        stats_data = await redis_client._client.get(stats_key)
        
        if not stats_data:
            return {
                "total_trades": 0,
                "winning_trades": 0,
                "losing_trades": 0,
                "win_rate": 0,
                "total_pnl": 0,
                "avg_pnl": 0
            }
        
        return json.loads(stats_data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
