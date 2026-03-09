"""
Trinity Bot API - Main FastAPI Application
"""

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from contextlib import asynccontextmanager
import logging
import os
import asyncio
import json
from datetime import datetime, timezone

from api.broadcast_service import BroadcastService
from api.routes import positions, trades, controls, analytics
from api.websocket_manager import ConnectionManager
from src.storage.redis_client import RedisClient

logger = logging.getLogger("trinity.api")


# Global state
manager = ConnectionManager()
redis_client: RedisClient | None = None
_start_time = datetime.now(timezone.utc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events"""
    global redis_client
    
    # Startup
    logger.info("Starting Trinity Bot API...")
    redis_client = RedisClient()
    await redis_client.connect()
    logger.info("Connected to Redis")
    
    # Set redis client for all routes
    from api.routes import positions, trades, controls, analytics
    positions.set_redis_client(redis_client)
    trades.set_redis_client(redis_client)
    controls.set_redis_client(redis_client)
    analytics.set_redis_client(redis_client)
    
    # Start background broadcast task (extracted to BroadcastService)
    svc = BroadcastService(manager, redis_client)
    _broadcast_task = asyncio.create_task(
        svc.run_forever(), name="broadcast_updates"
    )
    _broadcast_task.add_done_callback(_task_done_handler)
    
    yield
    
    # Shutdown
    logger.info("Shutting down Trinity Bot API...")
    if redis_client:
        await redis_client.disconnect()


def _task_done_handler(t: asyncio.Task) -> None:
    if t.cancelled():
        return
    exc = t.exception()
    if exc:
        logger.error("Task %s failed: %s", t.get_name(), exc)


app = FastAPI(
    title="Trinity Bot API",
    description="Real-time arbitrage bot monitoring and control",
    version="1.0.0",
    lifespan=lifespan,
    redirect_slashes=False,
)

# CORS middleware — restrict to known origins; falls back to ["*"] if env not set
_cors_origins = os.environ.get("CORS_ORIGINS", "http://localhost:3000,http://localhost:8000").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _cors_origins],
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
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
        data = await redis_client.get("trinity:opportunities")
        if data:
            return json.loads(data)
        return {"opportunities": [], "count": 0}
    except Exception as e:
        logger.warning("Error fetching opportunities: %s", e)
        return {"opportunities": [], "count": 0}


@app.get("/api/balances")
async def get_balances():
    """Get exchange balances"""
    try:
        if not redis_client:
            return {"balances": {}, "total": 0}
        data = await redis_client.get("trinity:balances")
        if data:
            return json.loads(data)
        return {"balances": {}, "total": 0}
    except Exception as e:
        logger.warning("Error fetching balances: %s", e)
        return {"balances": {}, "total": 0}


@app.get("/api/logs")
async def get_logs(limit: int = 50):
    """Get recent system logs"""
    try:
        if not redis_client:
            return {"logs": []}
        raw_logs = await redis_client.lrange("trinity:logs", 0, limit - 1)
        logs = [json.loads(log) for log in raw_logs]
        return {"logs": logs}
    except Exception as e:
        logger.warning("Error fetching logs: %s", e)
        return {"logs": []}


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
        "timestamp": datetime.now(timezone.utc).isoformat()
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
        status = await redis_client.get(status_key)
        
        if status:
            return json.loads(status)
        
        return {
            "bot_running": False,
            "connected_exchanges": [],
            "active_positions": 0,
            "uptime": 0
        }
    except Exception as e:
        logger.warning("Error fetching status: %s", e)
        return {"bot_running": False, "connected_exchanges": [], "active_positions": 0, "uptime": 0}


@app.get("/api/health")
async def health_check():
    """Health / readiness probe for container orchestration."""
    redis_ok = False
    if redis_client:
        try:
            redis_ok = await redis_client.health_check()
        except Exception:
            redis_ok = False

    uptime_s = (datetime.now(timezone.utc) - _start_time).total_seconds()
    ws_clients = len(manager.active_connections)

    return {
        "status": "healthy" if redis_ok else "degraded",
        "redis": "ok" if redis_ok else "down",
        "uptime_seconds": round(uptime_s, 1),
        "ws_clients": ws_clients,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time updates"""
    await manager.connect(websocket)
    try:
        while True:
            # Keep connection alive even if client doesn't send any frames.
            # This prevents "ghost connected" sessions that remain open but idle.
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=25)
                if data.lower() == "ping":
                    await websocket.send_text(json.dumps({
                        "type": "heartbeat",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }))
            except asyncio.TimeoutError:
                await websocket.send_text(json.dumps({
                    "type": "heartbeat",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }))
    except WebSocketDisconnect:
        await manager.disconnect(websocket)


# ── Serve React build (must be LAST — after all API routes) ──────
_build_dir = os.path.join("frontend", "build")
if os.path.exists(_build_dir):
    # Serve /static/... assets
    app.mount("/static", StaticFiles(directory=os.path.join(_build_dir, "static")), name="static")

    @app.get("/{full_path:path}")
    async def serve_react(full_path: str):
        """Catch-all: serve React app for client-side routing"""
        # Never intercept API or WebSocket routes
        if full_path == "api" or full_path.startswith("api/") or full_path.startswith("ws"):
            raise HTTPException(status_code=404, detail="Not found")
        file_path = os.path.join(_build_dir, full_path)
        if os.path.exists(file_path) and os.path.isfile(file_path):
            return FileResponse(file_path)
        return FileResponse(os.path.join(_build_dir, "index.html"))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
