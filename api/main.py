"""
Trinity Bot API - Main FastAPI Application
"""

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from contextlib import asynccontextmanager
import os
import asyncio
import json
from datetime import datetime
from typing import List

from api.routes import positions, trades, controls, analytics
from api.websocket_manager import ConnectionManager
from src.storage.redis_client import RedisClient


# Global state
manager = ConnectionManager()
redis_client = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events"""
    global redis_client
    
    # Startup
    print("🚀 Starting Trinity Bot API...")
    redis_client = RedisClient()
    await redis_client.connect()
    print("✅ Connected to Redis")
    
    # Set redis client for all routes
    from api.routes import positions, trades, controls, analytics
    positions.set_redis_client(redis_client)
    trades.set_redis_client(redis_client)
    controls.set_redis_client(redis_client)
    analytics.set_redis_client(redis_client)
    
    # Start background task for broadcasting updates
    asyncio.create_task(broadcast_updates())
    
    yield
    
    # Shutdown
    print("🛑 Shutting down Trinity Bot API...")
    if redis_client:
        await redis_client.disconnect()


app = FastAPI(
    title="Trinity Bot API",
    description="Real-time arbitrage bot monitoring and control",
    version="1.0.0",
    lifespan=lifespan
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify exact origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Include routers
app.include_router(positions.router, prefix="/api/positions", tags=["positions"])
app.include_router(trades.router, prefix="/api/trades", tags=["trades"])
app.include_router(controls.router, prefix="/api/controls", tags=["controls"])
app.include_router(analytics.router, prefix="/api/analytics", tags=["analytics"])


@app.get("/api/opportunities")
async def get_opportunities():
    """Get latest opportunities from scanner"""
    try:
        if not redis_client:
            return {"opportunities": [], "count": 0}
        data = await redis_client._client.get("trinity:opportunities")
        if data:
            return json.loads(data)
        return {"opportunities": [], "count": 0}
    except Exception as e:
        return {"opportunities": [], "count": 0, "error": str(e)}


@app.get("/api/balances")
async def get_balances():
    """Get exchange balances"""
    try:
        if not redis_client:
            return {"balances": {}, "total": 0}
        data = await redis_client._client.get("trinity:balances")
        if data:
            return json.loads(data)
        return {"balances": {}, "total": 0}
    except Exception as e:
        return {"balances": {}, "total": 0, "error": str(e)}


@app.get("/api/logs")
async def get_logs(limit: int = 50):
    """Get recent system logs"""
    try:
        if not redis_client:
            return {"logs": []}
        raw_logs = await redis_client._client.lrange("trinity:logs", 0, limit - 1)
        logs = [json.loads(log) for log in raw_logs]
        return {"logs": logs}
    except Exception as e:
        return {"logs": [], "error": str(e)}


@app.get("/")
async def root():
    """Serve React app or API health check"""
    index = os.path.join("frontend", "build", "index.html")
    if os.path.exists(index):
        return FileResponse(index)
    return {
        "status": "online",
        "service": "Trinity Bot API",
        "version": "1.0.0",
        "timestamp": datetime.utcnow().isoformat()
    }


@app.get("/api/status")
async def get_status():
    """Get bot status"""
    try:
        if not redis_client:
            return {
                "error": "Redis not connected",
                "bot_running": False
            }
        
        # Get status from Redis
        status_key = "trinity:status"
        status = await redis_client._client.get(status_key)
        
        if status:
            return json.loads(status)
        
        return {
            "bot_running": False,
            "connected_exchanges": [],
            "active_positions": 0,
            "uptime": 0
        }
    except Exception as e:
        return {"error": str(e)}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time updates"""
    await manager.connect(websocket)
    try:
        while True:
            # Keep connection alive
            data = await websocket.receive_text()
            # Echo back for ping/pong
            await websocket.send_text(f"pong: {data}")
    except WebSocketDisconnect:
        manager.disconnect(websocket)


async def _compute_summary(client) -> dict:
    """Compute accurate summary from trade history (same logic as HTTP endpoint)."""
    base = {"total_pnl": 0, "total_trades": 0, "win_rate": 0,
            "active_positions": 0, "uptime_hours": 0,
            "all_time_pnl": 0, "avg_pnl": 0}
    try:
        summary_data = await client.get("trinity:summary")
        if summary_data:
            base.update(json.loads(summary_data))
    except Exception:
        pass
    # Compute accurate stats from closed trade history
    all_time_pnl = 0.0
    trade_count = 0
    winning = 0
    try:
        trades_raw = await client.zrange("trinity:trades:history", 0, -1)
        for t in trades_raw:
            td = json.loads(t)
            pnl = float(td.get('total_pnl', 0))
            all_time_pnl += pnl
            trade_count += 1
            if pnl > 0:
                winning += 1
    except Exception:
        pass
    base['all_time_pnl'] = round(all_time_pnl, 4)
    base['avg_pnl'] = round(all_time_pnl / trade_count, 4) if trade_count > 0 else 0.0
    base['total_trades'] = trade_count
    base['win_rate'] = round(winning / trade_count, 3) if trade_count > 0 else 0.0
    return base


async def broadcast_updates():
    """Background task to broadcast updates to all connected clients"""
    while True:
        try:
            if manager.active_connections and redis_client:
                # Gather all data
                status_data = await redis_client._client.get("trinity:status")
                positions_data = await redis_client._client.get("trinity:positions")
                balances_data = await redis_client._client.get("trinity:balances")
                opportunities_data = await redis_client._client.get("trinity:opportunities")
                summary = await _compute_summary(redis_client._client)
                logs_data = await redis_client._client.lrange("trinity:logs", 0, 19)
                pnl_latest = await redis_client._client.get("trinity:pnl:latest")
                # Build proper pnl structure from closed trades (last 24h)
                pnl_struct = None
                try:
                    import time as _time
                    cutoff = _time.time() - 86400
                    trades_raw = await redis_client._client.zrangebyscore(
                        "trinity:trades:history", cutoff, float('inf'), withscores=True
                    )
                    dp = []
                    cumulative = 0.0
                    for item in trades_raw:
                        tj, ts = item
                        t = json.loads(tj)
                        pnl_val = float(t.get('total_pnl') or t.get('net_profit') or 0)
                        cumulative += pnl_val
                        dp.append({"pnl": pnl_val, "cumulative_pnl": cumulative, "timestamp": float(ts), "symbol": t.get('symbol', '?')})
                    unrealized = float(json.loads(pnl_latest).get('unrealized_pnl', 0)) if pnl_latest else 0.0
                    pnl_struct = {"data_points": dp, "total_pnl": cumulative + unrealized, "realized_pnl": cumulative, "unrealized_pnl": unrealized}
                except Exception:
                    pass

                # Build normalized trades list for frontend
                trades_list = []
                try:
                    recent_raw = await redis_client._client.zrange("trinity:trades:history", -20, -1, withscores=False)
                    for item in reversed(recent_raw):
                        t = json.loads(item)
                        invested = float(t.get('invested') or 0)
                        total_pnl_t = float(t.get('total_pnl') or 0)
                        pnl_pct = (total_pnl_t / invested) if invested > 0 else 0.0
                        entry_edge = t.get('entry_edge_pct')
                        trades_list.append({
                            **t,
                            'pnl': total_pnl_t,
                            'pnl_percentage': pnl_pct,
                            'open_time': t.get('opened_at'),
                            'close_time': t.get('closed_at'),
                            'exchanges': {'long': t.get('long_exchange'), 'short': t.get('short_exchange')},
                            'size': f"${invested:,.0f}",
                            'entry_spread': float(entry_edge) / 100 if entry_edge else None,
                            'exit_spread': None,
                            'price_pnl': float(t.get('price_pnl') or 0),
                            'funding_net': float(t.get('funding_net') or 0),
                            'invested': invested,
                            'mode': t.get('mode', 'hold'),
                            'exit_reason': t.get('exit_reason'),
                            'funding_collections': int(t.get('funding_collections') or 0),
                            'funding_collected_usd': float(t.get('funding_collected_usd') or 0),
                        })
                except Exception:
                    pass

                update = {
                    "type": "full_update",
                    "data": {
                        "status": json.loads(status_data) if status_data else None,
                        "positions": json.loads(positions_data) if positions_data else [],
                        "balances": json.loads(balances_data) if balances_data else None,
                        "opportunities": json.loads(opportunities_data) if opportunities_data else None,
                        "summary": summary,
                        "pnl": pnl_struct,
                        "logs": [json.loads(l) for l in logs_data] if logs_data else [],
                        "trades": trades_list,
                    },
                    "timestamp": datetime.utcnow().isoformat()
                }
                await manager.broadcast(json.dumps(update))
            
            await asyncio.sleep(2)
        except Exception as e:
            print(f"Error in broadcast_updates: {e}")
            await asyncio.sleep(5)


# ── Serve React build (must be LAST — after all API routes) ──────
_build_dir = os.path.join("frontend", "build")
if os.path.exists(_build_dir):
    # Serve /static/... assets
    app.mount("/static", StaticFiles(directory=os.path.join(_build_dir, "static")), name="static")

    @app.get("/{full_path:path}")
    async def serve_react(full_path: str):
        """Catch-all: serve React app for client-side routing"""
        file_path = os.path.join(_build_dir, full_path)
        if os.path.exists(file_path) and os.path.isfile(file_path):
            return FileResponse(file_path)
        return FileResponse(os.path.join(_build_dir, "index.html"))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
