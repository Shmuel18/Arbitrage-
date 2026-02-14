"""
Positions API Routes
"""

from fastapi import APIRouter, HTTPException
from datetime import datetime
import json

# Will be set by main.py during startup
redis_client = None

def set_redis_client(client):
    global redis_client
    redis_client = client

router = APIRouter()


@router.get("/")
async def get_positions():
    """Get all active positions"""
    try:
        if not redis_client:
            return {"positions": [], "count": 0}
        
        # Get positions from Redis
        positions_key = "trinity:positions"
        positions_data = await redis_client._client.get(positions_key)
        
        if not positions_data:
            return {"positions": [], "count": 0}
        
        positions = json.loads(positions_data)
        
        return {
            "positions": positions,
            "count": len(positions),
            "timestamp": datetime.utcnow().isoformat()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{position_id}")
async def get_position(position_id: str):
    """Get specific position details"""
    try:
        if not redis_client:
            raise HTTPException(status_code=503, detail="Redis not connected")
        
        position_key = f"trinity:position:{position_id}"
        position_data = await redis_client._client.get(position_key)
        
        if not position_data:
            raise HTTPException(status_code=404, detail="Position not found")
        
        return json.loads(position_data)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{position_id}")
async def close_position(position_id: str):
    """Close a specific position"""
    try:
        if not redis_client:
            raise HTTPException(status_code=503, detail="Redis not connected")
        
        # Send close command to bot via Redis
        command = {
            "action": "close_position",
            "position_id": position_id,
            "timestamp": datetime.utcnow().isoformat()
        }
        
        await redis_client._client.publish("trinity:commands", json.dumps(command))
        
        return {
            "status": "success",
            "message": f"Close command sent for position {position_id}"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
