"""
Trades History API Routes
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

logger = logging.getLogger("trinity.api.trades")

router = APIRouter(redirect_slashes=False)


@router.get("/")
@router.get("")
async def get_trades(
    redis_client: RedisClient = Depends(require_redis_client),
    limit: int = Query(100, ge=1, le=1000),
    hours: Optional[int] = Query(None, ge=1, le=168)
):
    """Get trades history"""
    try:
        # Get trades from Redis sorted set
        trades_key = "trinity:trades:history"
        
        # Calculate time range
        if hours:
            cutoff_time = (datetime.now(timezone.utc) - timedelta(hours=hours)).timestamp()
            trades_data = await redis_client.zrangebyscore(
                trades_key, 
                cutoff_time, 
                float('inf'),
            )
            # Respect limit: return only the most recent `limit` entries
            if len(trades_data) > limit:
                trades_data = trades_data[-limit:]
        else:
            trades_data = await redis_client.zrange(trades_key, -limit, -1)
        
        if not trades_data:
            return {"trades": [], "count": 0}
        
        # Parse trades
        trades = [json.loads(trade) for trade in trades_data if trade]
        trades.reverse()  # Most recent first
        
        # ── Normalize field names for frontend ──────────────────
        def normalize(t: dict) -> dict:
            invested = float(t.get('invested') or 0)
            total_pnl = float(t.get('total_pnl') or 0)
            price_pnl = float(t.get('price_pnl') or 0)
            funding_net = float(t.get('funding_net') or 0)
            pnl_pct = (total_pnl / invested) if invested > 0 else 0.0
            entry_edge = t.get('entry_edge_pct')
            return {
                **t,
                # aliases expected by TradesHistory.tsx
                'pnl':                    total_pnl,
                'pnl_percentage':         pnl_pct,
                'open_time':              t.get('opened_at'),
                'close_time':             t.get('closed_at'),
                'exchanges':              {'long': t.get('long_exchange'), 'short': t.get('short_exchange')},
                'size':                   f"${invested:,.0f}",
                'entry_spread':           float(entry_edge) / 100 if entry_edge else None,
                'entry_basis_pct':        float(t['entry_basis_pct']) / 100 if t.get('entry_basis_pct') is not None else None,
                'price_spread_pct':       float(t['price_spread_pct']) / 100 if t.get('price_spread_pct') is not None else None,
                'exit_spread':            None,  # not tracked at exit
                # rich detail fields
                'price_pnl':              price_pnl,
                'funding_net':            funding_net,
                'invested':               invested,
                'mode':                   t.get('mode', 'hold'),
                'exit_reason':            t.get('exit_reason'),
                'entry_tier':             t.get('entry_tier'),
                'funding_collections':    int(t.get('funding_collections') or 0),
                'funding_collected_usd':  float(t.get('funding_collected_usd') or 0),
                'long_24h_volume_usd':    float(t['long_24h_volume_usd']) if t.get('long_24h_volume_usd') is not None else None,
                'short_24h_volume_usd':   float(t['short_24h_volume_usd']) if t.get('short_24h_volume_usd') is not None else None,
            }
        
        trades = [normalize(t) for t in trades]
        
        return {
            "trades": trades,
            "count": len(trades),
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    except Exception as e:
        logger.exception("Unexpected error in get_trades")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/stats")
async def get_trade_stats(
    redis_client: RedisClient = Depends(require_redis_client),
):
    """Get trading statistics"""
    try:
        # Get stats from Redis
        stats_key = "trinity:stats"
        stats_data = await redis_client.get(stats_key)
        
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
        logger.exception("Unexpected error in get_trade_stats")
        raise HTTPException(status_code=500, detail="Internal server error")
