"""
Trinity Bot API - Main FastAPI Application
"""

# Load .env before anything else so ADMIN_TOKEN / READ_TOKEN are available
# when uvicorn is started as a standalone process (not via main.py).
from dotenv import load_dotenv as _load_dotenv
_load_dotenv()

from fastapi import Depends, FastAPI, Request, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response
from contextlib import asynccontextmanager
import logging
import os
import asyncio
import json
import uuid
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

from .auth import require_read_token
from .broadcast_service import BroadcastService
from .metrics import render_metrics
from .routes import positions, trades, controls, analytics, alerts, ai, backtest
from .websocket_manager import ConnectionManager
from src.storage.redis_client import RedisClient

logger = logging.getLogger("trinity.api")


# Global state
manager = ConnectionManager()
_start_time = datetime.now(timezone.utc)
_last_known_status: dict | None = None  # Cache last good status to survive transient Redis errors
_metrics_lock = Lock()
_request_count = 0
_request_total_ms = 0.0
_request_by_status: dict[str, int] = defaultdict(int)
_request_by_route: dict[str, int] = defaultdict(int)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events"""
    # Startup
    logger.info("Starting Trinity Bot API...")
    redis_client = RedisClient()
    await redis_client.connect()
    logger.info("Connected to Redis")

    # Store in application state so route dependencies can access it via
    # Depends(get_redis_client) without module-level globals.
    app.state.redis_client = redis_client

    # Start background broadcast task (extracted to BroadcastService)
    svc = BroadcastService(manager, redis_client)
    _broadcast_task = asyncio.create_task(
        svc.run_forever(), name="broadcast_updates"
    )
    _broadcast_task.add_done_callback(_task_done_handler)
    
    yield

    # ── Shutdown ────────────────────────────────────────────────────────────
    logger.info("Shutting down Trinity Bot API...")

    # Cancel the broadcast background task and wait for it to finish.
    # This prevents resource leaks and ensures any in-flight Redis calls
    # complete (or are properly cancelled) before we close the connection.
    _broadcast_task.cancel()
    try:
        await asyncio.wait_for(_broadcast_task, timeout=5.0)
    except (asyncio.CancelledError, asyncio.TimeoutError):
        pass  # Expected — task was cancelled or took too long to stop
    except Exception as exc:
        logger.warning("Broadcast task raised during shutdown: %s", exc)

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


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    """Attach request id and capture lightweight request metrics."""
    global _request_count, _request_total_ms
    req_id = request.headers.get("x-request-id") or uuid.uuid4().hex
    request.state.request_id = req_id
    route_key = f"{request.method} {request.url.path}"
    start = time.perf_counter()

    response = None
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    finally:
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        with _metrics_lock:
            _request_count += 1
            _request_total_ms += elapsed_ms
            _request_by_status[str(status_code)] += 1
            _request_by_route[route_key] += 1

        if response is not None:
            response.headers["X-Request-ID"] = req_id
            response.headers["X-Process-Time-Ms"] = f"{elapsed_ms:.2f}"

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
app.include_router(alerts.router, prefix="/api/alerts", tags=["alerts"])
app.include_router(ai.router, prefix="/api/ai", tags=["ai"])
app.include_router(backtest.router, prefix="/api/backtest", tags=["backtest"])


@app.get("/api/opportunities")
async def get_opportunities(
    request: Request,
    _auth: None = Depends(require_read_token),
):
    """Get latest opportunities from scanner"""
    redis_client = getattr(request.app.state, "redis_client", None)
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
async def get_balances(
    request: Request,
    _auth: None = Depends(require_read_token),
):
    """Get exchange balances"""
    redis_client = getattr(request.app.state, "redis_client", None)
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
async def get_logs(
    request: Request,
    limit: int = 50,
    _auth: None = Depends(require_read_token),
):
    """Get recent system logs"""
    redis_client = getattr(request.app.state, "redis_client", None)
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
async def get_status(
    request: Request,
    _auth: None = Depends(require_read_token),
):
    """Get bot status"""
    global _last_known_status
    redis_client = getattr(request.app.state, "redis_client", None)
    try:
        if not redis_client:
            return _last_known_status or {
                "error": "Redis not connected",
                "bot_running": False
            }

        # Get status from Redis
        status_key = "trinity:status"
        status = await redis_client.get(status_key)

        if status:
            _last_known_status = json.loads(status)
            return _last_known_status

        return _last_known_status or {
            "bot_running": False,
            "connected_exchanges": [],
            "active_positions": 0,
            "uptime": 0
        }
    except Exception as e:
        logger.warning("Error fetching status: %s — returning last known status", e)
        # Return last known good status instead of bot_running=False to avoid
        # false "STOPPED" display on transient Redis timeouts.
        return _last_known_status or {"bot_running": False, "connected_exchanges": [], "active_positions": 0, "uptime": 0}


@app.get("/api/health")
async def health_check(
    request: Request,
    _auth: None = Depends(require_read_token),
):
    """Health / readiness probe for container orchestration."""
    redis_client = getattr(request.app.state, "redis_client", None)
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


@app.get("/api/metrics")
async def get_metrics(
    _auth: None = Depends(require_read_token),
):
    """Lightweight in-process metrics for observability dashboards."""
    with _metrics_lock:
        req_count = _request_count
        total_ms = _request_total_ms
        status_counts = dict(_request_by_status)
        route_counts = dict(_request_by_route)

    avg_ms = (total_ms / req_count) if req_count > 0 else 0.0
    return {
        "request_count": req_count,
        "avg_latency_ms": round(avg_ms, 2),
        "status_counts": status_counts,
        "route_counts": route_counts,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/metrics")
async def prometheus_metrics(request: Request):
    """Prometheus scrape endpoint (plaintext format).

    Unauthenticated by design — the bot port is bound to 127.0.0.1, so
    only processes inside the Docker network (i.e. Prometheus) can reach
    it. Exposing this through nginx would require adding auth.
    """
    redis_client = getattr(request.app.state, "redis_client", None)
    if redis_client is None:
        return Response(
            content=b"# HELP redis not ready\n",
            media_type="text/plain; version=0.0.4",
        )
    body, content_type = await render_metrics(redis_client)
    return Response(content=body, media_type=content_type)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time updates.

    Caller must present ``trinity_ws_token`` cookie that matches ADMIN_TOKEN.
    If the token is missing/invalid, or ADMIN_TOKEN is not configured, the
    connection is rejected with close-code 1008 (Policy Violation).
    """
    expected = os.environ.get("ADMIN_TOKEN")
    if not expected:
        await websocket.close(code=1008, reason="Unauthorized")
        logger.warning("WebSocket connection rejected: ADMIN_TOKEN is not configured")
        return

    cookies = getattr(websocket, "cookies", {})
    token_candidate = cookies.get("trinity_ws_token")
    if token_candidate != expected:
        await websocket.close(code=1008, reason="Unauthorized")
        logger.warning("WebSocket connection rejected: invalid token")
        return

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
# Resolved once at startup so every request can do a fast prefix check.
_build_dir_resolved = Path(_build_dir).resolve()

if os.path.exists(_build_dir):
    # Serve /assets/... (Vite build output) or /static/... (CRA build output)
    _assets_dir = os.path.join(_build_dir, "assets")
    _static_dir = os.path.join(_build_dir, "static")
    if os.path.exists(_assets_dir):
        app.mount("/assets", StaticFiles(directory=_assets_dir), name="static")
    elif os.path.exists(_static_dir):
        app.mount("/static", StaticFiles(directory=_static_dir), name="static")

    @app.get("/{full_path:path}")
    async def serve_react(full_path: str):
        """Catch-all: serve React app for client-side routing.

        Security: normalise the path and verify it stays inside the build
        directory before serving.  This prevents path-traversal attacks such
        as ``GET /../../etc/passwd``.
        """
        # Never intercept API, WebSocket, or Prometheus scrape routes.
        if (
            full_path == "api"
            or full_path.startswith("api/")
            or full_path.startswith("ws")
            or full_path == "metrics"
        ):
            raise HTTPException(status_code=404, detail="Not found")

        # Normalise and jail to build dir.
        # Path.resolve() collapses all "../" components.
        candidate = (_build_dir_resolved / full_path).resolve()
        try:
            candidate.relative_to(_build_dir_resolved)
        except ValueError:
            # Path escapes the build directory — reject immediately (403, not 404,
            # so we don't inadvertently confirm the existence of files outside).
            raise HTTPException(status_code=403, detail="Forbidden")

        if candidate.is_file():
            return FileResponse(str(candidate))
        # SPA fallback — all unknown paths render index.html.
        return FileResponse(str(_build_dir_resolved / "index.html"))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
