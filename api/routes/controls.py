"""
Bot Controls API Routes
"""

from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel
from datetime import datetime
from typing import Any, Optional
import json
import os

redis_client = None

def set_redis_client(client):
    global redis_client
    redis_client = client

router = APIRouter()


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
async def send_command(command: BotCommand):
    """Send command to bot"""
    try:
        if not redis_client:
            raise HTTPException(status_code=503, detail="Redis not connected")
        
        command_data = {
            "action": command.action,
            "timestamp": datetime.utcnow().isoformat()
        }
        
        await redis_client._client.publish("trinity:commands", json.dumps(command_data))
        
        return {
            "status": "success",
            "message": f"Command '{command.action}' sent to bot"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/config")
async def update_config(update: ConfigUpdate):
    """Update bot configuration"""
    try:
        if not redis_client:
            raise HTTPException(status_code=503, detail="Redis not connected")

        if update.key not in _ALLOWED_CONFIG_KEYS:
            raise HTTPException(
                status_code=400,
                detail=f"Config key '{update.key}' is not allowed. "
                       f"Allowed: {', '.join(sorted(_ALLOWED_CONFIG_KEYS))}",
            )

        # Update config in Redis
        config_key = f"trinity:config:{update.key}"
        await redis_client._client.set(config_key, json.dumps(update.value))
        
        # Notify bot of config change
        command = {
            "action": "config_update",
            "key": update.key,
            "value": update.value,
            "timestamp": datetime.utcnow().isoformat()
        }
        
        await redis_client._client.publish("trinity:commands", json.dumps(command))
        
        return {
            "status": "success",
            "message": f"Config '{update.key}' updated"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/emergency_stop")
async def emergency_stop(x_emergency_token: Optional[str] = Header(None)):
    """Emergency stop - close all positions and stop bot.
    Requires X-Emergency-Token header if EMERGENCY_TOKEN env var is set."""
    expected = os.environ.get("EMERGENCY_TOKEN")
    if expected and x_emergency_token != expected:
        raise HTTPException(status_code=403, detail="Invalid or missing emergency token")
    try:
        if not redis_client:
            raise HTTPException(status_code=503, detail="Redis not connected")
        
        command = {
            "action": "emergency_stop",
            "timestamp": datetime.utcnow().isoformat()
        }
        
        await redis_client._client.publish("trinity:commands", json.dumps(command))
        
        return {
            "status": "success",
            "message": "Emergency stop initiated"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/exchanges")
async def get_exchanges():
    """Get exchange statuses"""
    try:
        if not redis_client:
            return {"exchanges": []}
        
        exchanges_key = "trinity:exchanges"
        exchanges_data = await redis_client._client.get(exchanges_key)
        
        if not exchanges_data:
            return {"exchanges": []}
        
        return json.loads(exchanges_data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
