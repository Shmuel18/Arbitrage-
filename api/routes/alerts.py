"""
Alerts API Routes — structured notification history.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Query

if TYPE_CHECKING:
    from src.storage.redis_client import RedisClient

from ..deps import require_redis_client

logger = logging.getLogger("trinity.api.alerts")

router = APIRouter(redirect_slashes=False)


@router.get("/")
@router.get("")
async def get_alerts(
    redis_client: "RedisClient" = Depends(require_redis_client),
    limit: int = Query(50, ge=1, le=200),
) -> dict:
    """Return the most recent structured alerts (newest first, max 200)."""
    try:
        alerts = await redis_client.get_alerts(limit=limit)
        return {"alerts": alerts, "count": len(alerts)}
    except Exception as exc:
        logger.warning("Failed to read trinity:alerts from Redis: %s", exc)
        return {"alerts": [], "count": 0}
