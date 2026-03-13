"""
Bot Controls API Routes
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any, Literal
import json
import logging
import hashlib

if TYPE_CHECKING:
    from src.storage.redis_client import RedisClient

from ..auth import require_command_token, require_config_token, require_emergency_token
from ..deps import require_redis_client

logger = logging.getLogger("trinity.api.controls")

_RATE_LIMIT_WINDOW_SECONDS = 60
_RATE_LIMIT_MAX_CALLS = 10


async def _check_rate_limit(
    redis_client: RedisClient,
    endpoint: str,
    client_ip: str,
    identity_token: str = "anonymous",
) -> None:
    """Distributed rate limit using Redis atomic counters.

    Key shape:
    - Legacy: ``trinity:rate_limit:<endpoint>:<ip>``
    - Scoped: ``trinity:rate_limit:<endpoint>:<identity-hash>:<ip>``
    """
    if identity_token == "anonymous":
        # Backward-compatible key shape used by legacy tests and tools.
        key = f"trinity:rate_limit:{endpoint}:{client_ip}"
    else:
        identity_hash = hashlib.sha256(identity_token.encode("utf-8")).hexdigest()[:16]
        key = f"trinity:rate_limit:{endpoint}:{identity_hash}:{client_ip}"
    current = await redis_client.incr(key)
    if current == 1:
        await redis_client.expire(key, _RATE_LIMIT_WINDOW_SECONDS)
    if current > _RATE_LIMIT_MAX_CALLS:
        raise HTTPException(
            status_code=429,
            detail=(
                f"Rate limit exceeded: max {_RATE_LIMIT_MAX_CALLS} calls "
                f"per {_RATE_LIMIT_WINDOW_SECONDS}s"
            ),
        )



async def _audit_control_action(
    redis_client: RedisClient,
    action: str,
    payload: dict[str, Any],
) -> None:
    event = {
        "action": action,
        "payload": payload,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    try:
        await redis_client.lpush("trinity:audit:controls", json.dumps(event))
        await redis_client.ltrim("trinity:audit:controls", 0, 499)
    except Exception as exc:
        # Audit should not break control paths — log at debug so operators can
        # see if audit logging is broken without generating noise in production.
        logger.debug("Failed to write audit log entry: %s", exc)

router = APIRouter(redirect_slashes=False)


class BotCommand(BaseModel):
    action: Literal["start", "stop", "pause", "resume"]


_ALLOWED_CONFIG_KEYS = frozenset({
    "max_concurrent_trades", "min_funding_spread", "strategy",
    "max_position_usd", "min_edge_pct", "mode",
})


class ConfigUpdate(BaseModel):
    key: str
    value: Any


def _validate_config_update_value(key: str, value: Any) -> Any:
    """Validate and normalize dynamic config updates by key."""
    numeric_ranges: dict[str, tuple[Decimal, Decimal]] = {
        "min_funding_spread": (Decimal("0"), Decimal("100")),
        "max_position_usd": (Decimal("1"), Decimal("10000000")),
        "min_edge_pct": (Decimal("0"), Decimal("100")),
    }

    if key == "max_concurrent_trades":
        if not isinstance(value, int):
            raise HTTPException(status_code=400, detail="max_concurrent_trades must be integer")
        if value < 1 or value > 100:
            raise HTTPException(status_code=400, detail="max_concurrent_trades out of range (1-100)")
        return value

    if key in numeric_ranges:
        try:
            numeric = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            raise HTTPException(status_code=400, detail=f"{key} must be numeric")
        min_v, max_v = numeric_ranges[key]
        if numeric < min_v or numeric > max_v:
            raise HTTPException(
                status_code=400,
                detail=f"{key} out of range ({min_v} - {max_v})",
            )
        return float(numeric)

    if key in {"strategy", "mode"}:
        if not isinstance(value, str) or not value.strip():
            raise HTTPException(status_code=400, detail=f"{key} must be non-empty string")
        if len(value.strip()) > 64:
            raise HTTPException(status_code=400, detail=f"{key} is too long")
        return value.strip()

    raise HTTPException(status_code=400, detail=f"No validator for key '{key}'")


@router.post("/command")
async def send_command(
    request: Request,
    command: BotCommand,
    redis_client: RedisClient = Depends(require_redis_client),
    _auth: None = Depends(require_command_token),
):
    """Send command to bot"""
    try:
        identity = request.headers.get("x-command-token") or "anonymous"
        await _check_rate_limit(
            redis_client,
            "command",
            request.client.host if request.client else "unknown",
            identity,
        )

        command_data = {
            "action": command.action,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
        await redis_client.publish("trinity:commands", json.dumps(command_data))
        await _audit_control_action(redis_client, "command", command_data)
        
        return {
            "status": "success",
            "message": f"Command '{command.action}' sent to bot"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Unexpected error in send_command")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/config")
async def update_config(
    request: Request,
    update: ConfigUpdate,
    redis_client: RedisClient = Depends(require_redis_client),
    _auth: None = Depends(require_config_token),
):
    """Update bot configuration"""
    try:
        identity = request.headers.get("x-config-token") or "anonymous"
        await _check_rate_limit(
            redis_client,
            "config",
            request.client.host if request.client else "unknown",
            identity,
        )

        if update.key not in _ALLOWED_CONFIG_KEYS:
            raise HTTPException(
                status_code=400,
                detail=f"Config key '{update.key}' is not allowed. "
                       f"Allowed: {', '.join(sorted(_ALLOWED_CONFIG_KEYS))}",
            )

        normalized_value = _validate_config_update_value(update.key, update.value)

        # Update config in Redis
        config_key = f"trinity:config:{update.key}"
        await redis_client.set(config_key, json.dumps(normalized_value))
        
        # Notify bot of config change
        command = {
            "action": "config_update",
            "key": update.key,
            "value": normalized_value,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
        await redis_client.publish("trinity:commands", json.dumps(command))
        await _audit_control_action(redis_client, "config_update", command)
        
        return {
            "status": "success",
            "message": f"Config '{update.key}' updated"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Unexpected error in update_config")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/emergency_stop")
async def emergency_stop(
    request: Request,
    redis_client: RedisClient = Depends(require_redis_client),
    _auth: None = Depends(require_emergency_token),
):
    """Emergency stop - close all positions and stop bot.
    Requires X-Emergency-Token header (EMERGENCY_TOKEN env var must be set)."""
    identity = request.headers.get("x-emergency-token") or "anonymous"
    await _check_rate_limit(
        redis_client,
        "emergency_stop",
        request.client.host if request.client else "unknown",
        identity,
    )
    try:
        command = {
            "action": "emergency_stop",
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
        await redis_client.publish("trinity:commands", json.dumps(command))
        await _audit_control_action(redis_client, "emergency_stop", command)
        
        return {
            "status": "success",
            "message": "Emergency stop initiated"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Unexpected error in emergency_stop")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/exchanges")
async def get_exchanges(
    redis_client: RedisClient = Depends(require_redis_client),
):
    """Get exchange statuses"""
    try:
        exchanges_key = "trinity:exchanges"
        exchanges_data = await redis_client.get(exchanges_key)
        
        if not exchanges_data:
            return {"exchanges": []}
        
        return json.loads(exchanges_data)
    except Exception as e:
        logger.exception("Unexpected error in get_exchanges")
        raise HTTPException(status_code=500, detail="Internal server error")
