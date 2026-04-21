"""
Positions API Routes
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException
from datetime import datetime, timezone
import json
import logging

if TYPE_CHECKING:
    from src.storage.redis_client import RedisClient

from ..auth import require_trade_token
from ..deps import require_redis_client

logger = logging.getLogger("trinity.api.positions")

router = APIRouter(redirect_slashes=False)


@router.get("/")
@router.get("")
async def get_positions(
    redis_client: RedisClient = Depends(require_redis_client),
):
    """Get all active positions"""
    try:
        # Get positions from Redis
        positions_key = "trinity:positions"
        positions_data = await redis_client.get(positions_key)
        
        if not positions_data:
            return {"positions": [], "count": 0}
        
        positions = json.loads(positions_data)
        
        # Normalize: if stored as dict with "positions" key, extract the list
        if isinstance(positions, dict):
            positions = positions.get("positions", [])
        
        return {
            "positions": positions,
            "count": len(positions),
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    except Exception as e:
        logger.exception("Unexpected error in get_positions")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/{position_id}")
async def get_position(
    position_id: str,
    redis_client: RedisClient = Depends(require_redis_client),
):
    """Get specific position details"""
    try:
        position_key = f"trinity:position:{position_id}"
        position_data = await redis_client.get(position_key)
        
        if not position_data:
            raise HTTPException(status_code=404, detail="Position not found")
        
        return json.loads(position_data)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Unexpected error in get_position")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.delete("/{position_id}")
async def close_position(
    position_id: str,
    redis_client: RedisClient = Depends(require_redis_client),
    _auth: None = Depends(require_trade_token),
):
    """Close a specific position (requires X-Trade-Token header)."""
    try:
        # Send close command to bot via Redis
        command = {
            "action": "close_position",
            "position_id": position_id,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
        await redis_client.publish("trinity:commands", json.dumps(command))
        
        return {
            "status": "success",
            "message": f"Close command sent for position {position_id}"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Unexpected error in close_position")
        raise HTTPException(status_code=500, detail="Internal server error")
