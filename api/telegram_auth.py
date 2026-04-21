"""Telegram Mini App `initData` authentication.

When the dashboard is opened inside Telegram, the client-side SDK exposes a
signed query-string payload at `window.Telegram.WebApp.initData`. The signature
is HMAC-SHA256 with a key derived from the bot token, allowing the backend to
cryptographically verify that the request really came from the Telegram client
for this specific bot (not a replayed / forged session).

Validation steps implemented below (spec:
https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app):

1. Parse the querystring into a mapping.
2. Pop the ``hash`` field — that's the signature.
3. Sort the remaining key=value pairs lexicographically and join with ``\n``
   (the "data_check_string").
4. Derive the secret key: ``HMAC_SHA256(key="WebAppData", msg=bot_token)``.
5. Compute ``HMAC_SHA256(key=secret_key, msg=data_check_string)`` hex digest.
6. Constant-time compare against the hash. Reject if different.
7. Also reject if ``auth_date`` is older than 24 h (prevents replay of old
   initData captured from another session).
8. If ``allowed_user_ids`` is configured, parse the nested ``user`` JSON and
   confirm ``user.id`` is whitelisted.

Exposes a FastAPI dependency ``verify_telegram_or_read_token`` that accepts
**either**:
  * ``X-Read-Token`` (existing dashboard auth) — keeps desktop flow working, or
  * ``X-Telegram-Init-Data`` (new) — Mini App flow.

Either header alone is sufficient; both can coexist without conflict.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qsl

from fastapi import Header, HTTPException

logger = logging.getLogger("trinity.telegram_auth")

# Max staleness for initData (seconds). Telegram issues fresh data each
# time the user re-opens the Mini App, so 24h is very lenient but matches
# Telegram's own documented expiry.
_INITDATA_MAX_AGE_SEC = 86400


# ── Core validator ───────────────────────────────────────────────

def validate_init_data(
    init_data: str, *, bot_token: str, max_age_sec: int = _INITDATA_MAX_AGE_SEC,
) -> Optional[Dict[str, Any]]:
    """Validate a Telegram Mini App ``initData`` payload.

    Args:
        init_data: The raw querystring from ``window.Telegram.WebApp.initData``.
        bot_token: The bot token associated with this Mini App.
        max_age_sec: Maximum accepted staleness of ``auth_date``.

    Returns:
        The parsed user-facing fields as a dict (including the decoded
        ``user`` object) when validation succeeds. ``None`` on any failure.
    """
    if not init_data or not bot_token:
        return None
    try:
        # Telegram sends the payload URL-encoded. `parse_qsl` preserves order
        # but we'll resort for the data-check-string anyway.
        pairs = parse_qsl(init_data, keep_blank_values=True)
    except Exception as exc:
        logger.debug("initData parse failed: %s", exc)
        return None

    if not pairs:
        return None
    data = dict(pairs)
    received_hash = data.pop("hash", None)
    if not received_hash:
        return None

    # ── auth_date freshness check ───────────────────────────────
    try:
        auth_date = int(data.get("auth_date", "0"))
    except ValueError:
        return None
    if auth_date == 0 or (time.time() - auth_date) > max_age_sec:
        logger.debug("initData auth_date expired (age=%s)", time.time() - auth_date)
        return None

    # ── Build data-check-string ─────────────────────────────────
    # Sort remaining keys alphabetically, join as "key=value\nkey=value..."
    data_check = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))

    # ── HMAC verification ──────────────────────────────────────
    secret_key = hmac.new(
        key=b"WebAppData", msg=bot_token.encode(), digestmod=hashlib.sha256,
    ).digest()
    computed = hmac.new(
        key=secret_key, msg=data_check.encode(), digestmod=hashlib.sha256,
    ).hexdigest()

    # Constant-time compare to thwart timing attacks
    if not hmac.compare_digest(computed, received_hash):
        logger.debug("initData HMAC mismatch")
        return None

    # ── Decode nested user JSON ─────────────────────────────────
    user_obj: Optional[Dict[str, Any]] = None
    if "user" in data:
        try:
            user_obj = json.loads(data["user"])
        except (ValueError, TypeError):
            user_obj = None

    return {
        "auth_date": auth_date,
        "user": user_obj,
        "query_id": data.get("query_id"),
        "start_param": data.get("start_param"),
    }


# ── FastAPI dependencies ─────────────────────────────────────────

def _get_bot_token() -> Optional[str]:
    """Read bot token from env at request time (picks up .env reload)."""
    return os.environ.get("TELEGRAM_BOT_TOKEN") or None


def _get_allowed_user_ids() -> List[int]:
    raw = os.environ.get("TELEGRAM_ALLOWED_USER_IDS", "")
    if not raw.strip():
        return []
    out: List[int] = []
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            out.append(int(part))
    return out


def verify_telegram(x_telegram_init_data: Optional[str] = Header(None)) -> Dict[str, Any]:
    """FastAPI dependency — require a valid Telegram ``initData`` payload.

    Raises 401 if missing/invalid. Use this on routes that should *only* be
    callable from a Mini App context (e.g. a dedicated ``/api/telegram/me``).

    Returns the decoded payload so handlers can access ``user.id`` etc.
    """
    token = _get_bot_token()
    if not token or token == "dummy":
        raise HTTPException(status_code=503, detail="Telegram auth not configured")
    if not x_telegram_init_data:
        raise HTTPException(status_code=401, detail="Missing X-Telegram-Init-Data")
    payload = validate_init_data(x_telegram_init_data, bot_token=token)
    if payload is None:
        raise HTTPException(status_code=401, detail="Invalid Telegram init data")

    # Allowlist (optional)
    allowed = _get_allowed_user_ids()
    if allowed:
        user_id = (payload.get("user") or {}).get("id")
        if user_id not in allowed:
            raise HTTPException(status_code=403, detail="User not in allowlist")

    return payload


def verify_telegram_or_read_token(
    x_read_token: Optional[str] = Header(None),
    x_telegram_init_data: Optional[str] = Header(None),
) -> None:
    """Accept *either* the existing read-only token *or* a valid Mini App
    initData payload. Used on read-only dashboard routes so that desktop
    browsers (using tokens) and Telegram Mini App users (using initData)
    can both fetch the same data.

    Fails closed:
      * If neither header is present → 401.
      * If both mechanisms fail → 401/403.
    """
    # Try Read Token first (fast path — no crypto)
    expected = os.environ.get("READ_TOKEN") or os.environ.get("ADMIN_TOKEN")
    if expected and x_read_token == expected:
        return

    # Fall back to Telegram init-data
    if x_telegram_init_data:
        token = _get_bot_token()
        if not token or token == "dummy":
            raise HTTPException(status_code=503, detail="Telegram auth not configured")
        payload = validate_init_data(x_telegram_init_data, bot_token=token)
        if payload is not None:
            allowed = _get_allowed_user_ids()
            if allowed:
                user_id = (payload.get("user") or {}).get("id")
                if user_id not in allowed:
                    raise HTTPException(status_code=403, detail="User not in allowlist")
            return  # OK

    # Neither mechanism accepted
    raise HTTPException(
        status_code=401,
        detail="Authentication required (X-Read-Token or X-Telegram-Init-Data)",
    )
