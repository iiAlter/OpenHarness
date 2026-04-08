"""Tests for APIChannel -- HTTP + WebSocket server."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from openharness.channels.bus.events import InboundMessage, OutboundMessage
from openharness.channels.bus.queue import MessageBus
from openharness.config.schema import ApiChannelConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(
    *,
    api_token: str = "test-secret",
    host: str = "127.0.0.1",
    port: int = 0,
    allow_from: list[str] | None = None,
) -> ApiChannelConfig:
    return ApiChannelConfig(
        enabled=True,
        api_token=api_token,
        host=host,
        port=port,
        allow_from=allow_from or ["*"],
    )


def _build_app_with_auth(channel):
    """Build a new FastAPI app with the same routes plus auth middleware."""
    app = channel._app
    api_token = channel.config.api_token

    app_with_mw = type(app)(title="TestApp")
    for route in app.routes:
        app_with_mw.routes.append(route)

    async def _auth(request, call_next):
        if request.url.path == "/api/v1/health":
            return await call_next(request)
        if request.url.path.startswith("/api/v1/ws"):
            return await call_next(request)
        auth = request.headers.get("Authorization", "")
        if auth != f"Bearer {api_token}":
            return JSONResponse(
                status_code=401, content={"detail": "Unauthorized"}
            )
        return await call_next(request)

    app_with_mw.add_middleware(BaseHTTPMiddleware, dispatch=_auth)
    return app_with_mw


@pytest.fixture()
def bus() -> MessageBus:
    return MessageBus()


@pytest.fixture()
def channel(bus: MessageBus):
    from openharness.channels.impl.api import APIChannel
    cfg = _make_config()
    return APIChannel(cfg, bus)


@pytest.fixture()
def authed_client(channel):
    """Return an httpx async client backed by the app *with* auth middleware."""
    app_with_mw = _build_app_with_auth(channel)
    transport = httpx.ASGITransport(app=app_with_mw)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


@pytest.fixture()
def plain_client(channel):
    """Return an httpx async client backed by the raw app (no middleware)."""
    transport = httpx.ASGITransport(app=channel._app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


# ---------------------------------------------------------------------------
# 1. Auth middleware tests
# ---------------------------------------------------------------------------


class TestAuthMiddleware:
    @pytest.mark.asyncio
    async def test_post_without_token_returns_401(self, authed_client) -> None:
        resp = await authed_client.post(
            "/api/v1/send",
            json={"client_id": "alice", "content": "hello"},
        )
        assert resp.status_code == 401
        await authed_client.aclose()

    @pytest.mark.asyncio
    async def test_post_with_valid_token_returns_200(self, channel, authed_client) -> None:
        resp = await authed_client.post(
            "/api/v1/send",
            json={"client_id": "alice", "content": "hello"},
            headers={"Authorization": f"Bearer {channel.config.api_token}"},
        )
        assert resp.status_code == 200
        await authed_client.aclose()


# ---------------------------------------------------------------------------
# 2. Health endpoint -- no auth required
# ---------------------------------------------------------------------------


class TestHealthEndpoint:
    @pytest.mark.asyncio
    async def test_health_no_auth(self, plain_client) -> None:
        resp = await plain_client.get("/api/v1/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data == {"status": "ok"}
        await plain_client.aclose()

    @pytest.mark.asyncio
    async def test_health_also_works_with_auth(self, channel, authed_client) -> None:
        resp = await authed_client.get(
            "/api/v1/health",
            headers={"Authorization": f"Bearer {channel.config.api_token}"},
        )
        assert resp.status_code == 200
        await authed_client.aclose()


# ---------------------------------------------------------------------------
# 3. POST /send publishes InboundMessage to bus
# ---------------------------------------------------------------------------


class TestSendMessage:
    @pytest.mark.asyncio
    async def test_send_publishes_to_bus(self, channel, bus) -> None:
        app_with_mw = _build_app_with_auth(channel)
        transport = httpx.ASGITransport(app=app_with_mw)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
            resp = await c.post(
                "/api/v1/send",
                json={"client_id": "alice", "content": "ping"},
                headers={"Authorization": f"Bearer {channel.config.api_token}"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert "msg_id" in body
        assert "session_id" in body

        msg = await asyncio.wait_for(bus.consume_inbound(), timeout=1.0)
        assert isinstance(msg, InboundMessage)
        assert msg.channel == "api"
        assert msg.sender_id == "alice"
        assert msg.chat_id == "alice"
        assert msg.content == "ping"
        assert msg.metadata.get("_msg_id") == body["msg_id"]


# ---------------------------------------------------------------------------
# 4. send() with active ws_map sends JSON
# ---------------------------------------------------------------------------


class TestSendMethod:
    @pytest.mark.asyncio
    async def test_send_reply(self, channel) -> None:
        mock_ws = AsyncMock()
        channel._ws_map["bob"] = mock_ws

        msg = OutboundMessage(
            channel="api",
            chat_id="bob",
            content="Hello!",
            metadata={"_msg_id": "abc123", "_session_key": "sess1"},
        )
        await channel.send(msg)

        mock_ws.send_json.assert_called_once()
        sent = mock_ws.send_json.call_args[0][0]
        assert sent["type"] == "reply"
        assert sent["msg_id"] == "abc123"
        assert sent["session_id"] == "sess1"
        assert sent["content"] == "Hello!"

    @pytest.mark.asyncio
    async def test_send_streaming_delta(self, channel) -> None:
        mock_ws = AsyncMock()
        channel._ws_map["bob"] = mock_ws

        msg = OutboundMessage(
            channel="api",
            chat_id="bob",
            content="partial",
            metadata={
                "_msg_id": "m1",
                "_session_key": "s1",
                "_streaming": True,
                "_streaming_final": False,
            },
        )
        await channel.send(msg)

        sent = mock_ws.send_json.call_args[0][0]
        assert sent["type"] == "reply_delta"

    @pytest.mark.asyncio
    async def test_send_error_message(self, channel) -> None:
        mock_ws = AsyncMock()
        channel._ws_map["bob"] = mock_ws

        msg = OutboundMessage(
            channel="api",
            chat_id="bob",
            content="Something went wrong",
            metadata={"_msg_id": "m2", "_error": True},
        )
        await channel.send(msg)

        sent = mock_ws.send_json.call_args[0][0]
        assert sent["type"] == "error"
        assert sent["message"] == "Something went wrong"

    @pytest.mark.asyncio
    async def test_send_streaming_final_sends_reply(self, channel) -> None:
        mock_ws = AsyncMock()
        channel._ws_map["bob"] = mock_ws

        msg = OutboundMessage(
            channel="api",
            chat_id="bob",
            content="done",
            metadata={
                "_msg_id": "m3",
                "_session_key": "s2",
                "_streaming": True,
                "_streaming_final": True,
            },
        )
        await channel.send(msg)

        sent = mock_ws.send_json.call_args[0][0]
        assert sent["type"] == "reply"


# ---------------------------------------------------------------------------
# 5. send() without ws connection drops message (no error)
# ---------------------------------------------------------------------------


class TestSendNoConnection:
    @pytest.mark.asyncio
    async def test_send_drops_without_ws(self, channel) -> None:
        msg = OutboundMessage(
            channel="api",
            chat_id="nobody",
            content="orphan",
            metadata={"_msg_id": "xyz"},
        )
        # Should not raise
        await channel.send(msg)


# ---------------------------------------------------------------------------
# 6. Sessions endpoint returns list
# ---------------------------------------------------------------------------


class TestSessionsEndpoint:
    @pytest.mark.asyncio
    async def test_sessions_returns_list(self, channel) -> None:
        app_with_mw = _build_app_with_auth(channel)
        transport = httpx.ASGITransport(app=app_with_mw)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
            resp = await c.get(
                "/api/v1/sessions",
                params={"client_id": "alice"},
                headers={"Authorization": f"Bearer {channel.config.api_token}"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert "sessions" in body
        assert isinstance(body["sessions"], list)


# ---------------------------------------------------------------------------
# 7. E2E smoke test -- full message round-trip
# ---------------------------------------------------------------------------


class TestAPIChannelEndToEnd:
    """Integration test: HTTP send -> bus -> reply via send() -> WS receive."""

    @pytest.mark.asyncio
    async def test_full_message_round_trip(self):
        """Send a message via HTTP, verify it hits the bus, simulate a reply, verify WS receives it."""
        from openharness.channels.impl.api import APIChannel

        bus = MessageBus()
        config = _make_config()
        channel = APIChannel(config, bus)

        # Set up a mock WebSocket
        mock_ws = AsyncMock()
        channel._ws_map["client-a"] = mock_ws

        # Send message via HTTP (raw app, no auth middleware)
        transport = httpx.ASGITransport(app=channel._app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/send",
                json={"client_id": "client-a", "content": "hello"},
            )
            assert resp.status_code == 200
            msg_id = resp.json()["msg_id"]

        # Verify inbound message on bus
        assert bus.inbound.qsize() == 1
        inbound = bus.inbound.get_nowait()
        assert inbound.content == "hello"
        assert inbound.metadata["_msg_id"] == msg_id

        # Simulate bridge sending a reply through the channel
        await channel.send(OutboundMessage(
            channel="api",
            chat_id="client-a",
            content="Hello! How can I help?",
            metadata={"_msg_id": msg_id, "_session_key": "api:client-a"},
        ))

        # Verify WS received the reply
        mock_ws.send_json.assert_called()
        calls = mock_ws.send_json.call_args_list
        sent = calls[-1][0][0]
        assert sent["type"] == "reply"
        assert sent["content"] == "Hello! How can I help?"
        assert sent["msg_id"] == msg_id

    @pytest.mark.asyncio
    async def test_streaming_round_trip(self):
        """Verify streaming deltas are delivered via WS."""
        from openharness.channels.impl.api import APIChannel

        bus = MessageBus()
        config = _make_config()
        channel = APIChannel(config, bus)

        mock_ws = AsyncMock()
        channel._ws_map["client-a"] = mock_ws

        # Simulate streaming deltas from bridge
        await channel.send(OutboundMessage(
            channel="api", chat_id="client-a", content="Hello",
            metadata={"_msg_id": "m1", "_streaming": True, "_session_key": "api:client-a"},
        ))
        await channel.send(OutboundMessage(
            channel="api", chat_id="client-a", content=" world",
            metadata={"_msg_id": "m1", "_streaming": True, "_session_key": "api:client-a"},
        ))
        await channel.send(OutboundMessage(
            channel="api", chat_id="client-a", content="Hello world",
            metadata={
                "_msg_id": "m1",
                "_streaming": True,
                "_streaming_final": True,
                "_session_key": "api:client-a",
            },
        ))

        calls = mock_ws.send_json.call_args_list
        types = [c[0][0]["type"] for c in calls]
        assert "reply_delta" in types
        assert "reply" in types
