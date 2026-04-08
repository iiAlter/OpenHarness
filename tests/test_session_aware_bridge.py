"""Tests for SessionAwareBridge."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openharness.channels.adapter import SessionAwareBridge
from openharness.channels.bus.events import InboundMessage, OutboundMessage
from openharness.channels.bus.queue import MessageBus
from openharness.engine.stream_events import AssistantTextDelta, AssistantTurnComplete


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_inbound(
    content: str = "hello",
    channel: str = "api",
    chat_id: str = "client-a",
    sender_id: str = "client-a",
    session_key_override: str | None = None,
) -> InboundMessage:
    return InboundMessage(
        channel=channel,
        sender_id=sender_id,
        chat_id=chat_id,
        content=content,
        timestamp=datetime.now(),
        session_key_override=session_key_override,
    )


def _aiter(items):
    """Return an async iterator over *items*."""
    async def _gen():
        for item in items:
            yield item
    return _gen()


def _make_settings(**overrides) -> Any:
    """Return a lightweight settings stub."""
    defaults = dict(
        api_key="test-key",
        base_url="http://localhost:11434",
        model="test-model",
        system_prompt="You are helpful.",
        max_tokens=1024,
        max_turns=10,
        api_format="anthropic",
    )
    defaults.update(overrides)
    return MagicMock(**defaults)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSessionAwareBridgeRouting:
    """API messages route to per-session engines; non-API to shared."""

    @pytest.mark.asyncio
    async def test_api_message_creates_per_session_engine(self):
        bus = MessageBus()
        settings = _make_settings()
        bridge = SessionAwareBridge(bus=bus, settings=settings, idle_timeout_minutes=30)

        with patch.object(bridge, "_create_engine", return_value=MagicMock()) as mock_create:
            mock_engine = mock_create.return_value
            mock_engine.submit_message = MagicMock(return_value=_aiter([
                AssistantTextDelta(text="world"),
                AssistantTurnComplete(message=MagicMock(), usage=MagicMock()),
            ]))

            await bridge.start()
            msg = _make_inbound(channel="api", chat_id="client-a")
            await bus.publish_inbound(msg)
            # Give the event loop a tick to process
            await asyncio.sleep(0.1)
            await bridge.stop()

        mock_create.assert_called_once_with("api:client-a")

    @pytest.mark.asyncio
    async def test_non_api_message_uses_shared_engine(self):
        bus = MessageBus()
        settings = _make_settings()
        bridge = SessionAwareBridge(bus=bus, settings=settings)

        mock_engine = MagicMock()
        mock_engine.submit_message = MagicMock(return_value=_aiter([
            AssistantTextDelta(text="reply"),
            AssistantTurnComplete(message=MagicMock(), usage=MagicMock()),
        ]))

        with patch.object(bridge, "_create_engine", return_value=mock_engine):
            await bridge.start()
            msg = _make_inbound(channel="telegram", chat_id="chat-1")
            await bus.publish_inbound(msg)
            await asyncio.sleep(0.1)
            await bridge.stop()

        # Shared engine should have been created once
        assert bridge._shared_engine is mock_engine

    @pytest.mark.asyncio
    async def test_different_sessions_get_different_engines(self):
        bus = MessageBus()
        settings = _make_settings()
        bridge = SessionAwareBridge(bus=bus, settings=settings)

        engine_a = MagicMock()
        engine_a.submit_message = MagicMock(return_value=_aiter([
            AssistantTextDelta(text="A"),
            AssistantTurnComplete(message=MagicMock(), usage=MagicMock()),
        ]))
        engine_b = MagicMock()
        engine_b.submit_message = MagicMock(return_value=_aiter([
            AssistantTextDelta(text="B"),
            AssistantTurnComplete(message=MagicMock(), usage=MagicMock()),
        ]))

        engines = {"api:client-a": engine_a, "api:client-b": engine_b}
        call_count = 0

        def create_engine_side_effect(session_key):
            nonlocal call_count
            call_count += 1
            return engines[session_key]

        with patch.object(bridge, "_create_engine", side_effect=create_engine_side_effect):
            await bridge.start()

            msg_a = _make_inbound(channel="api", chat_id="client-a")
            msg_b = _make_inbound(channel="api", chat_id="client-b")
            await bus.publish_inbound(msg_a)
            await bus.publish_inbound(msg_b)
            await asyncio.sleep(0.2)
            await bridge.stop()

        assert call_count == 2
        assert bridge._session_engines["api:client-a"] is engine_a
        assert bridge._session_engines["api:client-b"] is engine_b

    @pytest.mark.asyncio
    async def test_same_session_key_returns_same_engine(self):
        bus = MessageBus()
        settings = _make_settings()
        bridge = SessionAwareBridge(bus=bus, settings=settings)

        mock_engine = MagicMock()
        mock_engine.submit_message = MagicMock(return_value=_aiter([
            AssistantTextDelta(text="reply"),
            AssistantTurnComplete(message=MagicMock(), usage=MagicMock()),
        ]))

        mock_create = MagicMock(return_value=mock_engine)
        with patch.object(bridge, "_create_engine", mock_create):
            await bridge.start()

            msg1 = _make_inbound(channel="api", chat_id="client-a")
            msg2 = _make_inbound(channel="api", chat_id="client-a")
            await bus.publish_inbound(msg1)
            await asyncio.sleep(0.1)
            await bus.publish_inbound(msg2)
            await asyncio.sleep(0.1)
            await bridge.stop()

        # Engine should only be created once for same session_key
        assert mock_create.call_count == 1


class TestSessionAwareBridgeStreaming:
    """API sessions get streaming delta outbound messages."""

    @pytest.mark.asyncio
    async def test_streaming_delta_for_api_session(self):
        bus = MessageBus()
        settings = _make_settings()
        bridge = SessionAwareBridge(bus=bus, settings=settings)

        mock_engine = MagicMock()
        mock_engine.submit_message = MagicMock(return_value=_aiter([
            AssistantTextDelta(text="Hello "),
            AssistantTextDelta(text="world"),
            AssistantTurnComplete(message=MagicMock(), usage=MagicMock()),
        ]))

        with patch.object(bridge, "_create_engine", return_value=mock_engine):
            await bridge.start()
            msg = _make_inbound(channel="api", chat_id="client-x", content="hi")
            await bus.publish_inbound(msg)
            await asyncio.sleep(0.2)
            await bridge.stop()

        # Collect outbound messages
        outbound_messages: list[OutboundMessage] = []
        while not bus.outbound.empty():
            outbound_messages.append(await bus.consume_outbound())

        # Should have streaming deltas + final
        assert len(outbound_messages) >= 2
        # First delta
        assert outbound_messages[0].content == "Hello "
        assert outbound_messages[0].metadata.get("_streaming") is True
        # Second delta
        assert outbound_messages[1].content == "world"
        assert outbound_messages[1].metadata.get("_streaming") is True
        # Final message (streaming_final)
        final = [m for m in outbound_messages if m.metadata.get("_streaming_final")]
        assert len(final) == 1


class TestSessionAwareBridgeIdleTimeout:
    """Idle sessions are evicted after timeout."""

    @pytest.mark.asyncio
    async def test_idle_timeout_configuration(self):
        bus = MessageBus()
        settings = _make_settings()
        bridge = SessionAwareBridge(bus=bus, settings=settings, idle_timeout_minutes=5)
        assert bridge._idle_timeout_minutes == 5

    @pytest.mark.asyncio
    async def test_cleanup_evicts_idle_sessions(self):
        bus = MessageBus()
        settings = _make_settings()
        bridge = SessionAwareBridge(bus=bus, settings=settings, idle_timeout_minutes=0)
        # Manually inject a session engine with old timestamp
        mock_engine = MagicMock()
        bridge._session_engines["api:old-client"] = mock_engine
        bridge._session_last_active["api:old-client"] = time.time() - 3600  # 1 hour ago

        with patch.object(bridge, "_save_session", new_callable=AsyncMock):
            await bridge._cleanup_idle_sessions()

        assert "api:old-client" not in bridge._session_engines

    @pytest.mark.asyncio
    async def test_active_session_not_evicted(self):
        bus = MessageBus()
        settings = _make_settings()
        bridge = SessionAwareBridge(bus=bus, settings=settings, idle_timeout_minutes=30)
        mock_engine = MagicMock()
        bridge._session_engines["api:active-client"] = mock_engine
        bridge._session_last_active["api:active-client"] = time.time()

        await bridge._cleanup_idle_sessions()

        assert "api:active-client" in bridge._session_engines


class TestSessionAwareBridgeProcessOne:
    """Test _process_one helper for single-message processing."""

    @pytest.mark.asyncio
    async def test_process_one_handles_api_message(self):
        bus = MessageBus()
        settings = _make_settings()
        bridge = SessionAwareBridge(bus=bus, settings=settings)

        mock_engine = MagicMock()
        mock_engine.submit_message = MagicMock(return_value=_aiter([
            AssistantTextDelta(text="pong"),
            AssistantTurnComplete(message=MagicMock(), usage=MagicMock()),
        ]))

        with patch.object(bridge, "_create_engine", return_value=mock_engine):
            # Set _running so the session worker stays alive
            bridge._running = True
            msg = _make_inbound(channel="api", chat_id="client-1")
            await bus.publish_inbound(msg)
            await bridge._process_one()
            # The session worker runs as a background task; give it time
            await asyncio.sleep(0.2)
            bridge._running = False
            # Cancel the worker
            for task in bridge._session_tasks.values():
                task.cancel()
            for task in bridge._session_tasks.values():
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        assert "api:client-1" in bridge._session_engines

    @pytest.mark.asyncio
    async def test_process_one_handles_non_api_message(self):
        bus = MessageBus()
        settings = _make_settings()
        bridge = SessionAwareBridge(bus=bus, settings=settings)

        mock_engine = MagicMock()
        mock_engine.submit_message = MagicMock(return_value=_aiter([
            AssistantTextDelta(text="ok"),
            AssistantTurnComplete(message=MagicMock(), usage=MagicMock()),
        ]))

        with patch.object(bridge, "_create_engine", return_value=mock_engine):
            msg = _make_inbound(channel="discord", chat_id="chan-1")
            await bus.publish_inbound(msg)
            await bridge._process_one()

        assert bridge._shared_engine is mock_engine


class TestSessionAwareBridgeStop:
    """Stop saves active sessions."""

    @pytest.mark.asyncio
    async def test_stop_saves_all_active_sessions(self):
        bus = MessageBus()
        settings = _make_settings()
        bridge = SessionAwareBridge(bus=bus, settings=settings)

        mock_engine = MagicMock()
        bridge._session_engines["api:s1"] = mock_engine
        bridge._session_engines["api:s2"] = mock_engine
        bridge._session_last_active["api:s1"] = time.time()
        bridge._session_last_active["api:s2"] = time.time()

        with patch.object(bridge, "_save_session", new_callable=AsyncMock) as mock_save:
            await bridge.stop()
            assert mock_save.call_count == 2
