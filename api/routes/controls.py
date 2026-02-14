"""
Bot Controls API Routes
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from datetime import datetime
from typing import Any
import json

redis_client = None

def set_redis_client(client):
    global redis_client
    redis_client = client

router = APIRouter()


class BotCommand(BaseModel):
    action: str  # start, stop, pause, resume


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
async def emergency_stop():
    """Emergency stop - close all positions and stop bot"""
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
