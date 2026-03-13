"""
FastAPI dependency providers for Trinity API routes.
"""

from __future__ import annotations

from typing import Optional

from fastapi import HTTPException, Request

from src.storage.redis_client import RedisClient


def get_redis_client(request: Request) -> Optional[RedisClient]:
    """Return the shared RedisClient from application state, or None.

    Routes that can degrade gracefully (e.g. return an empty list) should use
    this dependency and handle the None case themselves.  Routes that require
    Redis to function should use :func:`require_redis_client` instead.
    """
    return getattr(request.app.state, "redis_client", None)


def require_redis_client(request: Request) -> RedisClient:
    """Return shared RedisClient or raise 503 when unavailable."""
    redis_client = getattr(request.app.state, "redis_client", None)
    if redis_client is None:
        raise HTTPException(status_code=503, detail="Redis not connected")
    return redis_client
