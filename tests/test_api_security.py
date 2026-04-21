"""Security and hardening tests for API auth/rate-limit/dependencies."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException, WebSocketDisconnect

from api import auth
from api import deps
from api.main import manager, websocket_endpoint
from api.routes import controls


class _DummyState:
    """Simple container for app.state in dependency tests."""

    def __init__(self, redis_client: Any | None) -> None:
        self.redis_client = redis_client


class _DummyApp:
    """Simple container for request.app in dependency tests."""

    def __init__(self, redis_client: Any | None) -> None:
        self.state = _DummyState(redis_client)


class _DummyRequest:
    """Simple request shim used by dependency unit tests."""

    def __init__(self, redis_client: Any | None) -> None:
        self.app = _DummyApp(redis_client)


class _StubWebSocket:
    """WebSocket stub that supports close/receive_text for endpoint tests."""

    def __init__(self, disconnect_immediately: bool = False, cookie_token: str | None = None) -> None:
        self.close = AsyncMock()
        self._disconnect_immediately = disconnect_immediately
        self.cookies = {}
        if cookie_token is not None:
            self.cookies["trinity_ws_token"] = cookie_token

    async def receive_text(self) -> str:
        if self._disconnect_immediately:
            raise WebSocketDisconnect()
        return "ping"


class TestRedisDependency:
    """Tests for Redis dependency providers in api.deps."""

    def test_require_redis_client_returns_client_when_present(self) -> None:
        """require_redis_client should return app.state.redis_client when set."""
        expected = object()
        request = _DummyRequest(expected)

        got = deps.require_redis_client(request)

        assert got is expected

    def test_require_redis_client_raises_503_when_missing(self) -> None:
        """require_redis_client must fail with 503 when no client is configured."""
        request = _DummyRequest(None)

        with pytest.raises(HTTPException) as exc_info:
            deps.require_redis_client(request)

        assert exc_info.value.status_code == 503
        assert exc_info.value.detail == "Redis not connected"


class TestDistributedRateLimit:
    """Tests for Redis-backed distributed rate limiting in controls route."""

    @pytest.mark.asyncio
    async def test_first_hit_sets_ttl(self) -> None:
        """First request in window should increment and set TTL."""
        redis_client = AsyncMock()
        redis_client.incr.return_value = 1
        redis_client.expire.return_value = True

        await controls._check_rate_limit(redis_client, "command", "127.0.0.1")

        redis_client.incr.assert_awaited_once_with("trinity:rate_limit:command:127.0.0.1")
        redis_client.expire.assert_awaited_once_with(
            "trinity:rate_limit:command:127.0.0.1",
            60,
        )

    @pytest.mark.asyncio
    async def test_non_first_hit_does_not_reset_ttl(self) -> None:
        """Subsequent requests should not reset key TTL every call."""
        redis_client = AsyncMock()
        redis_client.incr.return_value = 2

        await controls._check_rate_limit(redis_client, "config", "10.0.0.2")

        redis_client.incr.assert_awaited_once_with("trinity:rate_limit:config:10.0.0.2")
        redis_client.expire.assert_not_called()

    @pytest.mark.asyncio
    async def test_exceed_limit_raises_429(self) -> None:
        """Calls above threshold must be rejected with HTTP 429."""
        redis_client = AsyncMock()
        redis_client.incr.return_value = 11

        with pytest.raises(HTTPException) as exc_info:
            await controls._check_rate_limit(redis_client, "emergency_stop", "10.0.0.3")

        assert exc_info.value.status_code == 429


class TestWebSocketAuth:
    """Tests for fail-closed WebSocket authentication policy."""

    @pytest.mark.asyncio
    async def test_ws_rejects_when_admin_token_not_configured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """WS must reject with 1008 if ADMIN_TOKEN is missing."""
        monkeypatch.delenv("ADMIN_TOKEN", raising=False)
        mock_connect = AsyncMock()
        monkeypatch.setattr(manager, "connect", mock_connect)

        websocket = _StubWebSocket()
        await websocket_endpoint(websocket)

        websocket.close.assert_awaited_once()
        kwargs = websocket.close.await_args.kwargs
        assert kwargs.get("code") == 1008
        mock_connect.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_ws_rejects_invalid_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """WS must reject with 1008 when cookie token does not match ADMIN_TOKEN."""
        monkeypatch.setenv("ADMIN_TOKEN", "secret")
        mock_connect = AsyncMock()
        monkeypatch.setattr(manager, "connect", mock_connect)

        websocket = _StubWebSocket(cookie_token="wrong")
        await websocket_endpoint(websocket)

        websocket.close.assert_awaited_once()
        kwargs = websocket.close.await_args.kwargs
        assert kwargs.get("code") == 1008
        mock_connect.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_ws_accepts_valid_token_and_connects(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """WS should connect when cookie token matches ADMIN_TOKEN."""
        monkeypatch.setenv("ADMIN_TOKEN", "secret")
        mock_connect = AsyncMock()
        mock_disconnect = AsyncMock()
        monkeypatch.setattr(manager, "connect", mock_connect)
        monkeypatch.setattr(manager, "disconnect", mock_disconnect)

        websocket = _StubWebSocket(disconnect_immediately=True, cookie_token="secret")
        await websocket_endpoint(websocket)

        mock_connect.assert_awaited_once_with(websocket)
        mock_disconnect.assert_awaited_once_with(websocket)
        websocket.close.assert_not_called()


class TestScopedAuthDependencies:
    """Tests for command/config scoped token dependencies."""

    def test_require_command_token_accepts_command_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """COMMAND_TOKEN should authorize command actions."""
        monkeypatch.setenv("COMMAND_TOKEN", "cmd-secret")
        monkeypatch.delenv("ADMIN_TOKEN", raising=False)

        auth.require_command_token("cmd-secret")

    def test_require_command_token_falls_back_to_admin_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When COMMAND_TOKEN is missing, ADMIN_TOKEN should still authorize."""
        monkeypatch.delenv("COMMAND_TOKEN", raising=False)
        monkeypatch.setenv("ADMIN_TOKEN", "admin-secret")

        auth.require_command_token("admin-secret")

    def test_require_command_token_rejects_invalid_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Mismatched command token should be rejected with 403."""
        monkeypatch.setenv("COMMAND_TOKEN", "cmd-secret")

        with pytest.raises(HTTPException) as exc_info:
            auth.require_command_token("wrong")

        assert exc_info.value.status_code == 403

    def test_require_config_token_accepts_config_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """CONFIG_TOKEN should authorize config actions."""
        monkeypatch.setenv("CONFIG_TOKEN", "cfg-secret")
        monkeypatch.delenv("ADMIN_TOKEN", raising=False)

        auth.require_config_token("cfg-secret")

    def test_require_config_token_falls_back_to_admin_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When CONFIG_TOKEN is missing, ADMIN_TOKEN should still authorize."""
        monkeypatch.delenv("CONFIG_TOKEN", raising=False)
        monkeypatch.setenv("ADMIN_TOKEN", "admin-secret")

        auth.require_config_token("admin-secret")

    def test_require_config_token_rejects_when_unconfigured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Config dependency should fail closed when no token is configured."""
        monkeypatch.delenv("CONFIG_TOKEN", raising=False)
        monkeypatch.delenv("ADMIN_TOKEN", raising=False)

        with pytest.raises(HTTPException) as exc_info:
            auth.require_config_token("anything")

        assert exc_info.value.status_code == 403

    def test_require_trade_token_accepts_trade_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """TRADE_TOKEN should authorize trade action endpoints."""
        monkeypatch.setenv("TRADE_TOKEN", "trade-secret")
        monkeypatch.delenv("ADMIN_TOKEN", raising=False)

        auth.require_trade_token("trade-secret")

    def test_require_trade_token_falls_back_to_admin_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When TRADE_TOKEN is missing, ADMIN_TOKEN should still authorize."""
        monkeypatch.delenv("TRADE_TOKEN", raising=False)
        monkeypatch.setenv("ADMIN_TOKEN", "admin-secret")

        auth.require_trade_token("admin-secret")

    def test_require_trade_token_rejects_invalid_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Mismatched trade token should be rejected with 403."""
        monkeypatch.setenv("TRADE_TOKEN", "trade-secret")

        with pytest.raises(HTTPException) as exc_info:
            auth.require_trade_token("wrong")

        assert exc_info.value.status_code == 403


class TestPathTraversalJail:
    """Tests for static-file path-traversal protection in the React catch-all route."""

    def test_traversal_above_build_dir_raises(self) -> None:
        """Resolving '../..' out of build dir must raise ValueError from relative_to()."""
        from pathlib import Path

        build_dir = Path("/app/frontend/build").resolve()
        # Simulate ../../etc/passwd payload
        candidate = (build_dir / "../../etc/passwd").resolve()

        with pytest.raises(ValueError):
            candidate.relative_to(build_dir)

    def test_normal_asset_stays_inside_build_dir(self) -> None:
        """A normal asset path must not raise ValueError."""
        from pathlib import Path

        build_dir = Path("/app/frontend/build").resolve()
        candidate = (build_dir / "assets/main.js").resolve()

        # Should succeed without raising
        candidate.relative_to(build_dir)

    def test_encoded_traversal_collapses_via_resolve(self) -> None:
        """Path.resolve() must collapse all '../' components before the jail check."""
        from pathlib import Path

        build_dir = Path("/app/frontend/build").resolve()
        # Even deeply nested traversal is neutralised by resolve()
        candidate = (build_dir / "assets/../../../../../../etc/shadow").resolve()

        with pytest.raises(ValueError):
            candidate.relative_to(build_dir)


class TestReadEndpointAuthPolicy:
    """Tests that read-only telemetry endpoints are behind require_read_token."""

    def test_read_token_fails_closed_when_not_configured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """require_read_token must reject when neither READ_TOKEN nor ADMIN_TOKEN is set."""
        monkeypatch.delenv("READ_TOKEN", raising=False)
        monkeypatch.delenv("ADMIN_TOKEN", raising=False)

        with pytest.raises(HTTPException) as exc_info:
            auth.require_read_token(None)

        assert exc_info.value.status_code == 403

    def test_read_token_accepts_read_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """READ_TOKEN env should authorize the read token dependency."""
        monkeypatch.setenv("READ_TOKEN", "read-secret")
        monkeypatch.delenv("ADMIN_TOKEN", raising=False)

        auth.require_read_token("read-secret")

    def test_read_token_falls_back_to_admin_token(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When READ_TOKEN is absent, ADMIN_TOKEN must serve as fallback."""
        monkeypatch.delenv("READ_TOKEN", raising=False)
        monkeypatch.setenv("ADMIN_TOKEN", "admin-secret")

        auth.require_read_token("admin-secret")

    def test_read_token_rejects_wrong_value(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Wrong token value must be rejected with 403."""
        monkeypatch.setenv("READ_TOKEN", "read-secret")

        with pytest.raises(HTTPException) as exc_info:
            auth.require_read_token("wrong-value")

        assert exc_info.value.status_code == 403


class TestIdentityAwareRateLimit:
    """Tests for identity-scoped Redis rate-limit key structure."""

    @pytest.mark.asyncio
    async def test_identity_token_uses_hashed_key(self) -> None:
        """Non-anonymous identity token must produce a hashed key segment."""
        import hashlib

        redis_client = AsyncMock()
        redis_client.incr.return_value = 1
        redis_client.expire.return_value = True

        await controls._check_rate_limit(
            redis_client, "command", "1.2.3.4", identity_token="my-token"
        )

        expected_hash = hashlib.sha256(b"my-token").hexdigest()[:16]
        expected_key = f"trinity:rate_limit:command:{expected_hash}:1.2.3.4"
        redis_client.incr.assert_awaited_once_with(expected_key)

    @pytest.mark.asyncio
    async def test_anonymous_identity_uses_legacy_key(self) -> None:
        """Legacy 'anonymous' identity must use the old key format (no hash segment)."""
        redis_client = AsyncMock()
        redis_client.incr.return_value = 1
        redis_client.expire.return_value = True

        await controls._check_rate_limit(
            redis_client, "command", "1.2.3.4", identity_token="anonymous"
        )

        redis_client.incr.assert_awaited_once_with("trinity:rate_limit:command:1.2.3.4")

    @pytest.mark.asyncio
    async def test_two_users_same_ip_get_separate_buckets(self) -> None:
        """Different identity tokens on the same IP must produce independent keys."""
        import hashlib

        redis_client = AsyncMock()
        redis_client.incr.return_value = 1
        redis_client.expire.return_value = True

        await controls._check_rate_limit(
            redis_client, "config", "5.5.5.5", identity_token="user-a"
        )
        await controls._check_rate_limit(
            redis_client, "config", "5.5.5.5", identity_token="user-b"
        )

        calls = [str(c) for c in redis_client.incr.await_args_list]
        # Both calls must differ — user-a and user-b have different hash segments
        assert calls[0] != calls[1]
