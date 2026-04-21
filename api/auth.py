"""
Shared authentication helpers for Trinity API routes.
"""

from __future__ import annotations

import os
from typing import Optional

from fastapi import Header, HTTPException


def _resolve_expected_token(*env_keys: str) -> Optional[str]:
    for key in env_keys:
        val = os.environ.get(key)
        if val:
            return val
    return None


def require_admin_token(x_admin_token: Optional[str] = Header(None)) -> None:
    """FastAPI dependency — enforce X-Admin-Token header.

    Fails closed: if ADMIN_TOKEN is not set in the environment, all requests
    are rejected (prevents accidental open access on misconfigured deployments).
    """
    expected = _resolve_expected_token("ADMIN_TOKEN")
    if not expected:
        raise HTTPException(
            status_code=403,
            detail="Admin access is not configured on this server",
        )
    if x_admin_token != expected:
        raise HTTPException(status_code=403, detail="Invalid or missing admin token")


def require_emergency_token(x_emergency_token: Optional[str] = Header(None)) -> None:
    """FastAPI dependency — enforce X-Emergency-Token header.

    Fails closed: if EMERGENCY_TOKEN is not set in the environment, all requests
    are rejected.
    """
    expected = _resolve_expected_token("EMERGENCY_TOKEN")
    if not expected:
        raise HTTPException(
            status_code=403,
            detail="Emergency token is not configured on this server",
        )
    if x_emergency_token != expected:
        raise HTTPException(status_code=403, detail="Invalid or missing emergency token")


def require_read_token(x_read_token: Optional[str] = Header(None)) -> None:
    """FastAPI dependency — enforce read-only token for telemetry endpoints.

    Token source priority:
    1) READ_TOKEN (dedicated read token)
    2) ADMIN_TOKEN (fallback)
    """
    expected = _resolve_expected_token("READ_TOKEN", "ADMIN_TOKEN")
    if not expected:
        raise HTTPException(
            status_code=403,
            detail="Read API access is not configured on this server",
        )
    if x_read_token != expected:
        raise HTTPException(status_code=403, detail="Invalid or missing read token")


def require_command_token(x_command_token: Optional[str] = Header(None)) -> None:
    """FastAPI dependency — enforce token for bot command actions.

    Token source priority:
    1) COMMAND_TOKEN (dedicated command token)
    2) ADMIN_TOKEN (fallback for backward compatibility)
    """
    expected = _resolve_expected_token("COMMAND_TOKEN", "ADMIN_TOKEN")
    if not expected:
        raise HTTPException(
            status_code=403,
            detail="Command access is not configured on this server",
        )
    if x_command_token != expected:
        raise HTTPException(status_code=403, detail="Invalid or missing command token")


def require_config_token(x_config_token: Optional[str] = Header(None)) -> None:
    """FastAPI dependency — enforce token for config-update actions.

    Token source priority:
    1) CONFIG_TOKEN (dedicated config token)
    2) ADMIN_TOKEN (fallback for backward compatibility)
    """
    expected = _resolve_expected_token("CONFIG_TOKEN", "ADMIN_TOKEN")
    if not expected:
        raise HTTPException(
            status_code=403,
            detail="Config access is not configured on this server",
        )
    if x_config_token != expected:
        raise HTTPException(status_code=403, detail="Invalid or missing config token")


def require_trade_token(x_trade_token: Optional[str] = Header(None)) -> None:
    """FastAPI dependency — enforce token for trade action endpoints.

    Token source priority:
    1) TRADE_TOKEN (dedicated trade action token)
    2) ADMIN_TOKEN (fallback for backward compatibility)
    """
    expected = _resolve_expected_token("TRADE_TOKEN", "ADMIN_TOKEN")
    if not expected:
        raise HTTPException(
            status_code=403,
            detail="Trade action access is not configured on this server",
        )
    if x_trade_token != expected:
        raise HTTPException(status_code=403, detail="Invalid or missing trade token")
