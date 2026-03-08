"""
Trinity Bot API - Main FastAPI Application
"""

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from contextlib import asynccontextmanager
import os
import asyncio
import json
from datetime import datetime, timezone
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
    _broadcast_task = asyncio.create_task(
        broadcast_updates(), name="broadcast_updates"
    )
    _broadcast_task.add_done_callback(_task_done_handler)
    
    yield
    
    # Shutdown
    print("🛑 Shutting down Trinity Bot API...")
    if redis_client:
        await redis_client.disconnect()


def _task_done_handler(t: asyncio.Task) -> None:
    if t.cancelled():
        return
    exc = t.exception()
    if exc:
        print(f"Task {t.get_name()} failed: {exc}")


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
        print(f"Error fetching opportunities: {e}")
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
        print(f"Error fetching balances: {e}")
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
        print(f"Error fetching logs: {e}")
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
        print(f"Error fetching status: {e}")
        return {"bot_running": False, "connected_exchanges": [], "active_positions": 0, "uptime": 0}


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


async def broadcast_updates():
    """Background task to broadcast updates to all connected clients"""
    import time as _time  # import once at top of function, not inside loop

    _heartbeat_json = lambda: json.dumps({  # noqa: E731
        "type": "heartbeat",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })

    while True:
        # ── Always sleep 2s per cycle regardless of success/failure ──
        try:
            if not manager.active_connections:
                await asyncio.sleep(2)
                continue

            if not redis_client:
                # No Redis yet — heartbeat only so WS AGE stays green.
                await manager.broadcast(_heartbeat_json())
                await asyncio.sleep(2)
                continue

            rc = redis_client  # local alias

            # ── Parallel Redis reads — return_exceptions=True prevents a
            #    single Redis hiccup from crashing the entire broadcast cycle ──
            results = await asyncio.gather(
                rc.get("trinity:status"),
                rc.get("trinity:positions"),
                rc.get("trinity:balances"),
                rc.get("trinity:opportunities"),
                rc.lrange("trinity:logs", 0, 19),
                rc.get("trinity:pnl:latest"),
                rc.get("trinity:summary"),
                rc.zrange("trinity:trades:history", 0, -1, withscores=True),
                return_exceptions=True,
            )
            (
                status_data, positions_data, balances_data,
                opportunities_data, logs_data, pnl_latest,
                summary_data, trades_history_raw,
            ) = [None if isinstance(r, Exception) else r for r in results]

            # Log any gather errors so operators notice Redis issues.
            for key, res in zip(
                ("status", "positions", "balances", "opportunities", "logs",
                 "pnl_latest", "summary", "trades_history"),
                results,
            ):
                if isinstance(res, Exception):
                    print(f"[broadcast] Redis read error for '{key}': {res}")

            # ── Parse trade history ONCE — reuse for summary, pnl, trades_list ──
            all_trades: list[tuple[dict, float]] = []
            if trades_history_raw:
                for entry in trades_history_raw:
                    try:
                        if isinstance(entry, tuple):
                            raw_json, score = entry
                        else:
                            continue  # unexpected format
                        all_trades.append((json.loads(raw_json), float(score)))
                    except Exception:
                        pass

            # ── Summary (computed inline from all trades) ──
            base_summary: dict = {
                "total_pnl": 0, "total_trades": 0, "win_rate": 0,
                "active_positions": 0, "uptime_hours": 0,
                "all_time_pnl": 0, "avg_pnl": 0,
            }
            try:
                if summary_data:
                    base_summary.update(json.loads(summary_data))
            except Exception:
                pass

            all_time_pnl = 0.0
            winning = 0
            for td, _ in all_trades:
                pnl_v = float(td.get('total_pnl', 0))
                all_time_pnl += pnl_v
                if pnl_v > 0:
                    winning += 1
            trade_count = len(all_trades)
            base_summary['all_time_pnl'] = round(all_time_pnl, 4)
            base_summary['avg_pnl'] = round(all_time_pnl / trade_count, 4) if trade_count > 0 else 0.0
            base_summary['total_trades'] = trade_count
            base_summary['win_rate'] = round(winning / trade_count, 3) if trade_count > 0 else 0.0
            summary = base_summary

            # ── PnL struct (last 24h, from pre-parsed trades) ──
            pnl_struct = None
            try:
                cutoff = _time.time() - 86400
                dp = []
                cumulative = 0.0
                for td, ts in all_trades:
                    if ts < cutoff:
                        continue
                    pnl_val = float(td.get('total_pnl') or td.get('net_profit') or 0)
                    cumulative += pnl_val
                    dp.append({
                        "pnl": pnl_val,
                        "cumulative_pnl": cumulative,
                        "timestamp": ts,
                        "symbol": td.get('symbol', '?'),
                    })
                unrealized = float(json.loads(pnl_latest).get('unrealized_pnl', 0)) if pnl_latest else 0.0
                pnl_struct = {
                    "data_points": dp,
                    "total_pnl": cumulative + unrealized,
                    "realized_pnl": cumulative,
                    "unrealized_pnl": unrealized,
                }
            except Exception as exc:
                print(f"PnL structure build error: {exc}")

            # ── Trades list (last 20, from pre-parsed trades) ──
            trades_list = []
            try:
                for td, _ in reversed(all_trades[-20:]):
                    invested = float(td.get('invested') or 0)
                    total_pnl_t = float(td.get('total_pnl') or 0)
                    pnl_pct = (total_pnl_t / invested) if invested > 0 else 0.0
                    entry_edge = td.get('entry_edge_pct')
                    trades_list.append({
                        **td,
                        'pnl': total_pnl_t,
                        'pnl_percentage': pnl_pct,
                        'open_time': td.get('opened_at'),
                        'close_time': td.get('closed_at'),
                        'exchanges': {'long': td.get('long_exchange'), 'short': td.get('short_exchange')},
                        'size': f"${invested:,.0f}",
                        'entry_spread': float(entry_edge) / 100 if entry_edge else None,
                        'entry_basis_pct': float(td['entry_basis_pct']) / 100 if td.get('entry_basis_pct') is not None else None,
                        'exit_spread': None,
                        'price_pnl': float(td.get('price_pnl') or 0),
                        'funding_net': float(td.get('funding_net') or 0),
                        'invested': invested,
                        'mode': td.get('mode', 'hold'),
                        'exit_reason': td.get('exit_reason'),
                        'funding_collections': int(td.get('funding_collections') or 0),
                        'funding_collected_usd': float(td.get('funding_collected_usd') or 0),
                    })
            except Exception as exc:
                print(f"Trades list build error: {exc}")

            # ── Normalize positions to always be a flat list ──────
            positions_parsed = json.loads(positions_data) if positions_data else []
            if isinstance(positions_parsed, dict):
                positions_parsed = positions_parsed.get("positions", [])

            update = {
                "type": "full_update",
                "schema_version": 1,
                "data": {
                    "status": json.loads(status_data) if status_data else None,
                    "positions": positions_parsed,
                    "balances": json.loads(balances_data) if balances_data else None,
                    "opportunities": json.loads(opportunities_data) if opportunities_data else None,
                    "summary": summary,
                    "pnl": pnl_struct,
                    "logs": [json.loads(l) for l in logs_data] if logs_data else [],
                    "trades": trades_list,
                },
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            await manager.broadcast(json.dumps(update))

        except Exception as e:
            print(f"Error in broadcast_updates: {e}")
            # Even on error — send a heartbeat so WS AGE stays green.
            try:
                if manager.active_connections:
                    await manager.broadcast(_heartbeat_json())
            except Exception:
                pass

        await asyncio.sleep(2)


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
