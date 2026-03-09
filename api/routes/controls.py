"""
Bot Controls API Routes
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional
import json
import logging
import os
import time

if TYPE_CHECKING:
    from src.storage.redis_client import RedisClient

logger = logging.getLogger("trinity.api.controls")

redis_client: RedisClient | None = None

# ── Simple in-memory rate limiter ──────────────────────────────────
_rate_limit_ledger: dict[str, list[float]] = {}
_RATE_LIMIT_WINDOW = 60.0   # seconds
_RATE_LIMIT_MAX_CALLS = 10  # max calls per window per endpoint


def _check_rate_limit(endpoint: str) -> None:
    """Raise 429 if this endpoint has been called too often."""
    now = time.monotonic()
    timestamps = _rate_limit_ledger.setdefault(endpoint, [])
    # Prune old entries outside the window
    cutoff = now - _RATE_LIMIT_WINDOW
    _rate_limit_ledger[endpoint] = [ts for ts in timestamps if ts > cutoff]
    timestamps = _rate_limit_ledger[endpoint]
    if len(timestamps) >= _RATE_LIMIT_MAX_CALLS:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded: max {_RATE_LIMIT_MAX_CALLS} calls "
                   f"per {int(_RATE_LIMIT_WINDOW)}s for {endpoint}",
        )
    timestamps.append(now)


def _require_admin_token(x_admin_token: Optional[str]) -> None:
    expected = os.environ.get("ADMIN_TOKEN")
    if expected and x_admin_token != expected:
        raise HTTPException(status_code=403, detail="Invalid or missing admin token")


async def _audit_control_action(action: str, payload: dict[str, Any]) -> None:
    if not redis_client:
        return
    event = {
        "action": action,
        "payload": payload,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    try:
        await redis_client.lpush("trinity:audit:controls", json.dumps(event))
        await redis_client.ltrim("trinity:audit:controls", 0, 499)
    except Exception:
        # Audit should not break control paths.
        pass

def set_redis_client(client: RedisClient) -> None:
    global redis_client
    redis_client = client

router = APIRouter(redirect_slashes=False)


class BotCommand(BaseModel):
    action: str  # start, stop, pause, resume


_ALLOWED_CONFIG_KEYS = frozenset({
    "max_concurrent_trades", "min_funding_spread", "strategy",
    "max_position_usd", "min_edge_pct", "mode",
})


class ConfigUpdate(BaseModel):
    key: str
    value: Any


@router.post("/command")
async def send_command(command: BotCommand, x_admin_token: Optional[str] = Header(None)):
    """Send command to bot"""
    try:
        if not redis_client:
            raise HTTPException(status_code=503, detail="Redis not connected")
        
        _require_admin_token(x_admin_token)
        _check_rate_limit("command")

        command_data = {
            "action": command.action,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
        await redis_client.publish("trinity:commands", json.dumps(command_data))
        await _audit_control_action("command", command_data)
        
        return {
            "status": "success",
            "message": f"Command '{command.action}' sent to bot"
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/config")
async def update_config(update: ConfigUpdate, x_admin_token: Optional[str] = Header(None)):
    """Update bot configuration"""
    try:
        if not redis_client:
            raise HTTPException(status_code=503, detail="Redis not connected")

        _require_admin_token(x_admin_token)
        _check_rate_limit("config")

        if update.key not in _ALLOWED_CONFIG_KEYS:
            raise HTTPException(
                status_code=400,
                detail=f"Config key '{update.key}' is not allowed. "
                       f"Allowed: {', '.join(sorted(_ALLOWED_CONFIG_KEYS))}",
            )

        # Update config in Redis
        config_key = f"trinity:config:{update.key}"
        await redis_client.set(config_key, json.dumps(update.value))
        
        # Notify bot of config change
        command = {
            "action": "config_update",
            "key": update.key,
            "value": update.value,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
        await redis_client.publish("trinity:commands", json.dumps(command))
        await _audit_control_action("config_update", command)
        
        return {
            "status": "success",
            "message": f"Config '{update.key}' updated"
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/emergency_stop")
async def emergency_stop(x_emergency_token: Optional[str] = Header(None)):
    """Emergency stop - close all positions and stop bot.
    Requires X-Emergency-Token header if EMERGENCY_TOKEN env var is set."""
    expected = os.environ.get("EMERGENCY_TOKEN")
    if expected and x_emergency_token != expected:
        raise HTTPException(status_code=403, detail="Invalid or missing emergency token")
    _check_rate_limit("emergency_stop")
    try:
        if not redis_client:
            raise HTTPException(status_code=503, detail="Redis not connected")
        
        command = {
            "action": "emergency_stop",
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
        await redis_client.publish("trinity:commands", json.dumps(command))
        await _audit_control_action("emergency_stop", command)
        
        return {
            "status": "success",
            "message": "Emergency stop initiated"
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/exchanges")
async def get_exchanges():
    """Get exchange statuses"""
    try:
        if not redis_client:
            return {"exchanges": []}
        
        exchanges_key = "trinity:exchanges"
        exchanges_data = await redis_client.get(exchanges_key)
        
        if not exchanges_data:
            return {"exchanges": []}
        
        return json.loads(exchanges_data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
