# API Channel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an HTTP/WebSocket API channel so external applications can chat with OpenHarness concurrently.

**Architecture:** A new `APIChannel` (FastAPI HTTP+WS) registered in `ChannelManager`, plus a `SessionAwareBridge` that replaces `ChannelBridge` when the API channel is enabled. The bridge maintains a pool of independent `QueryEngine` instances keyed by `session_key`, enabling concurrent conversations. Session persistence reuses the existing `session_storage` module.

**Tech Stack:** Python 3.10+, FastAPI, uvicorn, asyncio, Pydantic, pytest, pytest-asyncio, httpx (test client)

**Spec:** `docs/superpowers/specs/2026-04-08-api-channel-design.md`

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `src/openharness/config/schema.py` | **Create** | `Config`, `ChannelsConfig`, `ApiChannelConfig`, and stub configs for all existing channels |
| `src/openharness/channels/impl/api.py` | **Create** | `APIChannel` — FastAPI HTTP + WebSocket server |
| `src/openharness/channels/adapter.py` | **Modify** | Add `SessionAwareBridge` class alongside existing `ChannelBridge` |
| `src/openharness/channels/impl/manager.py` | **Modify** | Register `APIChannel`, skip API in `_validate_allow_from()` |
| `pyproject.toml` | **Modify** | Add `fastapi`, `uvicorn[standard]` dependencies |
| `tests/test_api_channel.py` | **Create** | Tests for APIChannel HTTP/WS endpoints |
| `tests/test_session_aware_bridge.py` | **Create** | Tests for SessionAwareBridge routing and lifecycle |

---

### Task 1: Add dependencies to pyproject.toml

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add fastapi and uvicorn dependencies**

In `pyproject.toml`, add to the `dependencies` list:

```toml
"fastapi>=0.115.0",
"uvicorn[standard]>=0.30.0",
```

- [ ] **Step 2: Install dependencies**

Run: `cd /home/yuholy/github/OpenHarness && uv sync`
Expected: Dependencies resolved and installed.

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore: add fastapi and uvicorn dependencies for API channel"
```

---

### Task 2: Create config/schema.py

The file `src/openharness/config/schema.py` does not exist but is imported by `ChannelManager` and all channel implementations. It must be created with the `Config` class and per-channel config stubs. Each stub config needs only the fields actually accessed by `ChannelManager` and the channel constructors.

**Files:**
- Create: `src/openharness/config/schema.py`
- Test: `tests/test_config_schema.py`

- [ ] **Step 1: Write the test**

```python
# tests/test_config_schema.py
import pytest
from openharness.config.schema import ApiChannelConfig, ChannelsConfig, Config


def test_api_channel_config_defaults():
    cfg = ApiChannelConfig()
    assert cfg.enabled is False
    assert cfg.api_token == ""
    assert cfg.host == "0.0.0.0"
    assert cfg.port == 8080
    assert cfg.idle_timeout_minutes == 30
    assert cfg.session_retention_days == 7
    assert cfg.allow_from == ["*"]


def test_api_channel_config_custom():
    cfg = ApiChannelConfig(
        enabled=True,
        api_token="secret",
        port=9090,
        allow_from=["client-1"],
    )
    assert cfg.enabled is True
    assert cfg.api_token == "secret"
    assert cfg.port == 9090
    assert cfg.allow_from == ["client-1"]


def test_channels_config_has_api():
    cfg = ChannelsConfig()
    assert hasattr(cfg, "api")
    assert isinstance(cfg.api, ApiChannelConfig)


def test_config_has_channels():
    cfg = Config()
    assert hasattr(cfg, "channels")
    assert isinstance(cfg.channels, ChannelsConfig)


def test_config_channels_has_existing_platforms():
    cfg = Config()
    assert hasattr(cfg.channels, "telegram")
    assert hasattr(cfg.channels, "discord")
    assert hasattr(cfg.channels, "slack")
    assert hasattr(cfg.channels, "whatsapp")
    assert hasattr(cfg.channels, "feishu")
    assert hasattr(cfg.channels, "dingtalk")
    assert hasattr(cfg.channels, "mochat")
    assert hasattr(cfg.channels, "email")
    assert hasattr(cfg.channels, "qq")
    assert hasattr(cfg.channels, "matrix")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_config_schema.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'openharness.config.schema'`

- [ ] **Step 3: Create config/schema.py**

```python
"""Configuration schema for channels and providers.

This module defines the configuration model consumed by ChannelManager
and channel implementations.
"""

from __future__ import annotations

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Per-channel config stubs
# ---------------------------------------------------------------------------

class TelegramConfig(BaseModel):
    enabled: bool = False
    token: str = ""
    allow_from: list[str] = []


class DiscordConfig(BaseModel):
    enabled: bool = False
    token: str = ""
    allow_from: list[str] = []


class SlackConfig(BaseModel):
    enabled: bool = False
    app_token: str = ""
    bot_token: str = ""
    allow_from: list[str] = []


class WhatsAppConfig(BaseModel):
    enabled: bool = False
    bridge_url: str = "ws://localhost:3000"
    bridge_token: str = ""
    allow_from: list[str] = []


class FeishuConfig(BaseModel):
    enabled: bool = False
    app_id: str = ""
    app_secret: str = ""
    allow_from: list[str] = []


class DingTalkConfig(BaseModel):
    enabled: bool = False
    client_id: str = ""
    client_secret: str = ""
    allow_from: list[str] = []


class MochatConfig(BaseModel):
    enabled: bool = False
    server_url: str = ""
    token: str = ""
    allow_from: list[str] = []


class EmailConfig(BaseModel):
    enabled: bool = False
    imap_host: str = ""
    imap_port: int = 993
    smtp_host: str = ""
    smtp_port: int = 587
    username: str = ""
    password: str = ""
    allow_from: list[str] = []


class QQConfig(BaseModel):
    enabled: bool = False
    appid: str = ""
    token: str = ""
    allow_from: list[str] = []


class MatrixConfig(BaseModel):
    enabled: bool = False
    homeserver: str = ""
    user_id: str = ""
    password: str = ""
    allow_from: list[str] = []


# ---------------------------------------------------------------------------
# API channel config
# ---------------------------------------------------------------------------

class ApiChannelConfig(BaseModel):
    enabled: bool = False
    api_token: str = ""
    host: str = "0.0.0.0"
    port: int = 8080
    idle_timeout_minutes: int = 30
    session_retention_days: int = 7
    allow_from: list[str] = ["*"]


# ---------------------------------------------------------------------------
# Aggregates
# ---------------------------------------------------------------------------

class GroqProviderConfig(BaseModel):
    api_key: str = ""


class ProviderConfig(BaseModel):
    groq: GroqProviderConfig = GroqProviderConfig()


class ChannelsConfig(BaseModel):
    send_progress: bool = True
    send_tool_hints: bool = False
    telegram: TelegramConfig = TelegramConfig()
    discord: DiscordConfig = DiscordConfig()
    slack: SlackConfig = SlackConfig()
    whatsapp: WhatsAppConfig = WhatsAppConfig()
    feishu: FeishuConfig = FeishuConfig()
    dingtalk: DingTalkConfig = DingTalkConfig()
    mochat: MochatConfig = MochatConfig()
    email: EmailConfig = EmailConfig()
    qq: QQConfig = QQConfig()
    matrix: MatrixConfig = MatrixConfig()
    api: ApiChannelConfig = ApiChannelConfig()


class Config(BaseModel):
    channels: ChannelsConfig = ChannelsConfig()
    providers: ProviderConfig = ProviderConfig()
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_config_schema.py -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/openharness/config/schema.py tests/test_config_schema.py
git commit -m "feat: create config schema module with ApiChannelConfig"
```

---

### Task 3: Create SessionAwareBridge

Add the `SessionAwareBridge` class to the existing adapter module. It coexists with `ChannelBridge` — when the API channel is enabled, `SessionAwareBridge` is used instead.

**Files:**
- Modify: `src/openharness/channels/adapter.py`
- Test: `tests/test_session_aware_bridge.py`

- [ ] **Step 1: Write the test**

```python
# tests/test_session_aware_bridge.py
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openharness.channels.adapter import SessionAwareBridge
from openharness.channels.bus.events import InboundMessage, OutboundMessage
from openharness.channels.bus.queue import MessageBus


def _make_inbound(channel: str, chat_id: str, content: str = "hello") -> InboundMessage:
    return InboundMessage(
        channel=channel,
        sender_id=chat_id,
        chat_id=chat_id,
        content=content,
    )


@pytest.fixture
def bus():
    return MessageBus()


@pytest.fixture
def bridge(bus):
    return SessionAwareBridge(
        bus=bus,
        settings=MagicMock(
            model="test-model",
            max_tokens=1024,
            max_turns=10,
            system_prompt="test",
            api_format="anthropic",
            api_key="test-key",
            base_url=None,
        ),
        cwd="/tmp",
        idle_timeout_minutes=30,
    )


class TestSessionAwareBridgeRouting:
    """Test that messages are routed to the correct handler."""

    @pytest.mark.asyncio
    async def test_api_message_creates_session_engine(self, bridge, bus):
        """API messages should trigger per-session engine creation."""
        msg = _make_inbound("api", "client-a")

        with patch.object(bridge, "_create_engine") as mock_create:
            mock_engine = AsyncMock()
            mock_engine.submit_message = AsyncMock(return_value=aiter([]))
            mock_create.return_value = mock_engine

            await bus.publish_inbound(msg)
            task = asyncio.create_task(bridge._process_one())
            await asyncio.sleep(0.1)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

            mock_create.assert_called_once_with("api:client-a")

    @pytest.mark.asyncio
    async def test_non_api_message_uses_shared_engine(self, bridge, bus):
        """Non-API messages should use a single shared engine."""
        msg = _make_inbound("telegram", "123")

        with patch.object(bridge, "_create_engine") as mock_create:
            mock_engine = AsyncMock()
            mock_engine.submit_message = AsyncMock(return_value=aiter([]))
            mock_create.return_value = mock_engine

            await bus.publish_inbound(msg)
            task = asyncio.create_task(bridge._process_one())
            await asyncio.sleep(0.1)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

            mock_create.assert_called_once()


class TestSessionAwareBridgeConcurrency:
    """Test that different sessions process concurrently."""

    @pytest.mark.asyncio
    async def test_different_sessions_get_different_engines(self, bridge, bus):
        """Two API sessions should have independent engine instances."""
        with patch.object(bridge, "_create_engine") as mock_create:
            engine_a = AsyncMock()
            engine_a.submit_message = AsyncMock(return_value=aiter([]))
            engine_b = AsyncMock()
            engine_b.submit_message = AsyncMock(return_value=aiter([]))
            mock_create.side_effect = [engine_a, engine_b]

            msg_a = _make_inbound("api", "client-a", "hello A")
            msg_b = _make_inbound("api", "client-b", "hello B")

            await bus.publish_inbound(msg_a)
            task_a = asyncio.create_task(bridge._process_one())
            await asyncio.sleep(0.05)

            await bus.publish_inbound(msg_b)
            task_b = asyncio.create_task(bridge._process_one())
            await asyncio.sleep(0.05)

            for t in [task_a, task_b]:
                t.cancel()
                try:
                    await t
                except asyncio.CancelledError:
                    pass

            assert mock_create.call_count == 2


class TestSessionAwareBridgeStreaming:
    """Test streaming delta support for API sessions."""

    @pytest.mark.asyncio
    async def test_api_session_publishes_streaming_deltas(self, bridge, bus):
        """API sessions should publish incremental OutboundMessages."""
        from openharness.engine.stream_events import AssistantTextDelta, AssistantTurnComplete
        from openharness.engine.messages import ConversationMessage
        from openharness.api.usage import UsageSnapshot

        msg = _make_inbound("api", "client-a", "hello")

        async def fake_stream(prompt):
            yield AssistantTextDelta(text="Hello")
            yield AssistantTextDelta(text=" world")
            yield AssistantTurnComplete(
                message=ConversationMessage(role="assistant", content=[]),
                usage=MagicMock(),
            )

        with patch.object(bridge, "_create_engine") as mock_create:
            mock_engine = AsyncMock()
            mock_engine.submit_message = fake_stream
            mock_create.return_value = mock_engine

            await bus.publish_inbound(msg)
            task = asyncio.create_task(bridge._process_one())
            await asyncio.sleep(0.2)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # Should have published outbound messages
        assert bus.outbound.qsize() >= 2


class TestSessionAwareBridgeLifecycle:
    """Test engine lifecycle (create, timeout, restore)."""

    def test_idle_timeout_is_configurable(self, bus):
        bridge = SessionAwareBridge(
            bus=bus,
            settings=MagicMock(),
            cwd="/tmp",
            idle_timeout_minutes=60,
        )
        assert bridge._idle_timeout_minutes == 60

    @pytest.mark.asyncio
    async def test_get_or_create_returns_same_engine_for_same_session(self, bridge):
        """Same session_key should return the same engine."""
        with patch.object(bridge, "_create_engine") as mock_create:
            mock_engine = MagicMock()
            mock_create.return_value = mock_engine

            engine1 = await bridge._get_or_create_session_engine("api:client-a")
            engine2 = await bridge._get_or_create_session_engine("api:client-a")

            assert engine1 is engine2
            assert mock_create.call_count == 1


# Helper: async iterator from list
async def aiter(items):
    for item in items:
        yield item
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_session_aware_bridge.py -v`
Expected: FAIL — `ImportError: cannot import name 'SessionAwareBridge'`

- [ ] **Step 3: Implement SessionAwareBridge**

Add the following class to the end of `src/openharness/channels/adapter.py` (keep the existing `ChannelBridge` untouched):

```python
# Add these imports at the top of the file (if not already present):
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any, TYPE_CHECKING

from openharness.channels.bus.events import InboundMessage, OutboundMessage
from openharness.channels.bus.queue import MessageBus
from openharness.engine.stream_events import AssistantTextDelta, AssistantTurnComplete

if TYPE_CHECKING:
    from openharness.engine.query_engine import QueryEngine

logger = logging.getLogger(__name__)


# ... existing ChannelBridge class unchanged ...


class SessionAwareBridge:
    """Bridges inbound messages to per-session QueryEngines with concurrency.

    When the API channel is enabled, this bridge replaces ChannelBridge.
    API sessions get independent engines processed concurrently.
    Non-API sessions share a single engine, processed sequentially (same as ChannelBridge).
    """

    def __init__(
        self,
        *,
        bus: MessageBus,
        settings: Any,
        cwd: str | Path = ".",
        idle_timeout_minutes: int = 30,
    ) -> None:
        self._bus = bus
        self._settings = settings
        self._cwd = str(cwd)
        self._idle_timeout_minutes = idle_timeout_minutes
        self._running = False

        # Per-session engines for API channel
        self._session_engines: dict[str, "QueryEngine"] = {}
        self._session_last_active: dict[str, float] = {}
        self._session_queues: dict[str, asyncio.Queue[InboundMessage]] = {}
        self._session_tasks: dict[str, asyncio.Task] = {}

        # Shared engine for non-API channels
        self._shared_engine: "QueryEngine | None" = None

        # Cleanup task
        self._cleanup_task: asyncio.Task | None = None

    async def start(self) -> None:
        self._running = True
        self._cleanup_task = asyncio.create_task(self._cleanup_loop(), name="session-cleanup")
        asyncio.create_task(self._route_loop(), name="session-aware-bridge")
        logger.info("SessionAwareBridge started")

    async def stop(self) -> None:
        self._running = False
        # Save all active sessions
        for session_key in list(self._session_engines.keys()):
            await self._save_session(session_key)
        # Cancel all tasks
        for task in self._session_tasks.values():
            task.cancel()
        if self._cleanup_task:
            self._cleanup_task.cancel()
        logger.info("SessionAwareBridge stopped")

    async def run(self) -> None:
        await self.start()
        try:
            while self._running:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            await self.stop()

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------

    async def _route_loop(self) -> None:
        """Main loop: consume inbound, dispatch to per-session or shared engine."""
        while self._running:
            try:
                msg = await asyncio.wait_for(self._bus.consume_inbound(), timeout=1.0)
                if msg.channel == "api":
                    await self._dispatch_to_session(msg)
                else:
                    await self._dispatch_to_shared(msg)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("SessionAwareBridge: routing error")

    async def _process_one(self) -> None:
        """Process a single inbound message (for testing)."""
        msg = await asyncio.wait_for(self._bus.consume_inbound(), timeout=2.0)
        if msg.channel == "api":
            await self._dispatch_to_session(msg)
        else:
            await self._dispatch_to_shared(msg)

    # ------------------------------------------------------------------
    # API session handling (concurrent)
    # ------------------------------------------------------------------

    async def _dispatch_to_session(self, msg: InboundMessage) -> None:
        session_key = msg.session_key
        await self._get_or_create_session_engine(session_key)
        self._session_last_active[session_key] = time.time()

        # Ensure per-session queue exists
        if session_key not in self._session_queues:
            self._session_queues[session_key] = asyncio.Queue()

        await self._session_queues[session_key].put(msg)

        # Ensure per-session processor is running
        if session_key not in self._session_tasks or self._session_tasks[session_key].done():
            self._session_tasks[session_key] = asyncio.create_task(
                self._session_worker(session_key),
                name=f"session-{session_key}",
            )

    async def _session_worker(self, session_key: str) -> None:
        """Process messages for a single session sequentially."""
        queue = self._session_queues[session_key]
        while self._running:
            try:
                msg = await asyncio.wait_for(queue.get(), timeout=1.0)
                await self._handle_session_message(session_key, msg)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Session worker error for %s", session_key)

    async def _handle_session_message(self, session_key: str, msg: InboundMessage) -> None:
        engine = self._session_engines.get(session_key)
        if not engine:
            logger.error("No engine for session %s", session_key)
            return

        msg_id = msg.metadata.get("_msg_id", "")
        try:
            async for event in engine.submit_message(msg.content):
                if isinstance(event, AssistantTextDelta):
                    # Publish streaming delta
                    await self._bus.publish_outbound(OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        content=event.text,
                        metadata={
                            "_msg_id": msg_id,
                            "_streaming": True,
                            "_session_key": session_key,
                        },
                    ))
                elif isinstance(event, AssistantTurnComplete):
                    # Publish final complete reply
                    full_text = event.message.text.strip() if event.message.text else ""
                    if full_text:
                        await self._bus.publish_outbound(OutboundMessage(
                            channel=msg.channel,
                            chat_id=msg.chat_id,
                            content=full_text,
                            metadata={
                                "_msg_id": msg_id,
                                "_streaming": True,
                                "_streaming_final": True,
                                "_session_key": session_key,
                            },
                        ))
        except Exception:
            logger.exception("Engine error for session %s", session_key)
            await self._bus.publish_outbound(OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content="[Error processing message]",
                metadata={"_msg_id": msg_id, "_error": True},
            ))

    # ------------------------------------------------------------------
    # Non-API session handling (serialized, same as ChannelBridge)
    # ------------------------------------------------------------------

    async def _dispatch_to_shared(self, msg: InboundMessage) -> None:
        if self._shared_engine is None:
            self._shared_engine = self._create_engine(msg.session_key)
        await self._handle_shared_message(msg)

    async def _handle_shared_message(self, msg: InboundMessage) -> None:
        reply_parts: list[str] = []
        try:
            async for event in self._shared_engine.submit_message(msg.content):
                if isinstance(event, AssistantTextDelta):
                    reply_parts.append(event.text)
        except Exception:
            logger.exception("Shared engine error")
            reply_parts = ["[Error processing message]"]

        reply_text = "".join(reply_parts).strip()
        if reply_text:
            await self._bus.publish_outbound(OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=reply_text,
                metadata={"_session_key": msg.session_key},
            ))

    async def _get_or_create_shared_engine(self) -> "QueryEngine":
        if self._shared_engine is None:
            self._shared_engine = self._create_engine("shared")
        return self._shared_engine

    # ------------------------------------------------------------------
    # Engine lifecycle
    # ------------------------------------------------------------------

    async def _get_or_create_session_engine(self, session_key: str) -> "QueryEngine":
        if session_key not in self._session_engines:
            self._session_engines[session_key] = self._create_engine(session_key)
            logger.info("Created engine for session %s", session_key)
        self._session_last_active[session_key] = time.time()
        return self._session_engines[session_key]

    def _create_engine(self, session_key: str) -> "QueryEngine":
        """Construct a QueryEngine for headless (non-UI) use."""
        from openharness.engine.query_engine import QueryEngine
        from openharness.config.settings import PermissionSettings
        from openharness.permissions.checker import PermissionChecker
        from openharness.permissions.modes import PermissionMode
        from openharness.tools import create_default_tool_registry

        # Create API client based on format
        api_format = getattr(self._settings, "api_format", "anthropic")
        api_key = getattr(self._settings, "api_key", "")
        base_url = getattr(self._settings, "base_url", None)

        if api_format == "openai":
            from openharness.api.openai_client import OpenAICompatibleClient
            api_client = OpenAICompatibleClient(api_key, base_url=base_url)
        else:
            from openharness.api.client import AnthropicApiClient
            api_client = AnthropicApiClient(api_key, base_url=base_url)

        tool_registry = create_default_tool_registry()
        # FULL_AUTO mode: auto-approve all tool calls (no interactive prompts)
        permission_checker = PermissionChecker(
            PermissionSettings(mode=PermissionMode.FULL_AUTO)
        )

        return QueryEngine(
            api_client=api_client,
            tool_registry=tool_registry,
            permission_checker=permission_checker,
            cwd=self._cwd,
            model=self._settings.model,
            system_prompt=self._settings.system_prompt or "You are a helpful assistant.",
            max_tokens=self._settings.max_tokens,
            max_turns=self._settings.max_turns,
            permission_prompt=None,  # auto-approve
            ask_user_prompt=None,    # no interactive prompts
        )

    async def _save_session(self, session_key: str) -> None:
        """Persist a session's conversation history to disk."""
        engine = self._session_engines.get(session_key)
        if not engine:
            return
        try:
            from openharness.services.session_storage import save_session_snapshot
            from openharness.api.usage import UsageSnapshot

            # Use public `messages` property; access model/prompt via private attrs
            # (no public accessors exist, see query_engine.py)
            save_session_snapshot(
                cwd=Path.home() / ".openharness" / "api-sessions",
                model=getattr(engine, "_model", self._settings.model),
                system_prompt=getattr(engine, "_system_prompt", self._settings.system_prompt or ""),
                messages=engine._messages,
                usage=UsageSnapshot(),
            )
            logger.info("Saved session %s to disk", session_key)
        except Exception:
            logger.exception("Failed to save session %s", session_key)

    async def _restore_session(self, session_key: str, session_id: str) -> None:
        """Restore a session from disk."""
        try:
            from openharness.services.session_storage import load_session_by_id

            data = load_session_by_id(
                Path.home() / ".openharness" / "api-sessions",
                session_id,
            )
            if data and data.get("messages"):
                engine = await self._get_or_create_session_engine(session_key)
                from openharness.engine.messages import ConversationMessage
                engine._messages = [
                    ConversationMessage.model_validate(m) for m in data["messages"]
                ]
                logger.info("Restored session %s (%d messages)", session_key, len(engine._messages))
        except Exception:
            logger.exception("Failed to restore session %s", session_key)

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    async def _cleanup_loop(self) -> None:
        """Periodically clean up idle sessions."""
        while self._running:
            try:
                await asyncio.sleep(60)
                await self._cleanup_idle_sessions()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Cleanup loop error")

    async def _cleanup_idle_sessions(self) -> None:
        now = time.time()
        timeout_secs = self._idle_timeout_minutes * 60
        for key in list(self._session_last_active.keys()):
            if now - self._session_last_active[key] > timeout_secs:
                await self._save_session(key)
                self._session_engines.pop(key, None)
                self._session_last_active.pop(key, None)
                task = self._session_tasks.pop(key, None)
                if task and not task.done():
                    task.cancel()
                self._session_queues.pop(key, None)
                logger.info("Cleaned up idle session %s", key)
```

Note: The `_create_engine` method references `create_api_client` and `create_default_tool_registry`. If these functions do not exist with these exact names, the implementer should check the actual API registry and tool registry modules and adjust the import paths accordingly.

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_session_aware_bridge.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/openharness/channels/adapter.py tests/test_session_aware_bridge.py
git commit -m "feat: add SessionAwareBridge with per-session concurrent engines"
```

---

### Task 4: Create APIChannel

**Files:**
- Create: `src/openharness/channels/impl/api.py`
- Test: `tests/test_api_channel.py`

- [ ] **Step 1: Write the test**

```python
# tests/test_api_channel.py
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from openharness.channels.bus.queue import MessageBus
from openharness.channels.impl.api import APIChannel


@pytest.fixture
def bus():
    return MessageBus()


@pytest.fixture
def api_config():
    from openharness.config.schema import ApiChannelConfig
    return ApiChannelConfig(
        enabled=True,
        api_token="test-token",
        host="127.0.0.1",
        port=0,  # let OS pick port
        allow_from=["*"],
    )


@pytest.fixture
def channel(api_config, bus):
    return APIChannel(api_config, bus)


class TestAPIChannelAuthentication:
    @pytest.mark.asyncio
    async def test_send_requires_token(self, channel):
        transport = ASGITransport(app=channel._app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/v1/send", json={
                "client_id": "test",
                "content": "hello",
            })
            assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_send_with_valid_token(self, channel):
        transport = ASGITransport(app=channel._app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/send",
                json={"client_id": "test", "content": "hello"},
                headers={"Authorization": "Bearer test-token"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert "msg_id" in data
            assert "session_id" in data


class TestAPIChannelHealthEndpoint:
    @pytest.mark.asyncio
    async def test_health_no_auth_required(self, channel):
        transport = ASGITransport(app=channel._app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/health")
            assert resp.status_code == 200
            assert resp.json()["status"] == "ok"


class TestAPIChannelSendPublishesToBus:
    @pytest.mark.asyncio
    async def test_send_publishes_inbound_message(self, channel, bus):
        transport = ASGITransport(app=channel._app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await client.post(
                "/api/v1/send",
                json={"client_id": "client-a", "content": "hello"},
                headers={"Authorization": "Bearer test-token"},
            )

        # Message should be on the bus
        assert bus.inbound.qsize() == 1
        msg = bus.inbound.get_nowait()
        assert msg.channel == "api"
        assert msg.chat_id == "client-a"
        assert msg.content == "hello"


class TestAPIChannelWsMap:
    @pytest.mark.asyncio
    async def test_send_stores_ws_mapping(self, channel):
        """send() should push to WebSocket when connection exists."""
        mock_ws = AsyncMock()
        channel._ws_map["client-a"] = mock_ws

        from openharness.channels.bus.events import OutboundMessage
        msg = OutboundMessage(
            channel="api",
            chat_id="client-a",
            content="Hello back",
            metadata={"_msg_id": "msg-1"},
        )
        await channel.send(msg)

        mock_ws.send_json.assert_called_once()
        sent_data = mock_ws.send_json.call_args[0][0]
        assert sent_data["type"] == "reply"
        assert sent_data["content"] == "Hello back"
        assert sent_data["msg_id"] == "msg-1"

    @pytest.mark.asyncio
    async def test_send_drops_when_no_ws(self, channel):
        """send() should log warning and drop when no WS connection."""
        from openharness.channels.bus.events import OutboundMessage
        msg = OutboundMessage(
            channel="api",
            chat_id="unknown",
            content="orphan",
            metadata={},
        )
        # Should not raise
        await channel.send(msg)


class TestAPIChannelSessions:
    @pytest.mark.asyncio
    async def test_sessions_endpoint_returns_list(self, channel):
        transport = ASGITransport(app=channel._app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/sessions?client_id=test",
                headers={"Authorization": "Bearer test-token"},
            )
            assert resp.status_code == 200
            assert "sessions" in resp.json()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_api_channel.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'openharness.channels.impl.api'`

- [ ] **Step 3: Implement APIChannel**

```python
"""API channel: HTTP + WebSocket server for external application integration."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from openharness.channels.bus.events import OutboundMessage
from openharness.channels.bus.queue import MessageBus
from openharness.channels.impl.base import BaseChannel

logger = logging.getLogger(__name__)


class SendMessageRequest(BaseModel):
    client_id: str
    content: str
    session_id: str | None = None


class APIChannel(BaseChannel):
    """Channel that exposes a FastAPI HTTP + WebSocket server."""

    name = "api"

    def __init__(self, config: Any, bus: MessageBus) -> None:
        super().__init__(config, bus)
        self.config = config
        self._ws_map: dict[str, WebSocket] = {}
        self._app = FastAPI(title="OpenHarness API")
        self._server: uvicorn.Server | None = None
        self._setup_routes()

    def _setup_routes(self) -> None:
        app = self._app

        @app.get("/api/v1/health")
        async def health():
            return {"status": "ok"}

        @app.get("/api/v1/sessions")
        async def list_sessions(client_id: str):
            from openharness.services.session_storage import list_session_snapshots
            from pathlib import Path
            sessions = list_session_snapshots(Path.home() / ".openharness" / "api-sessions")
            return {"sessions": sessions}

        @app.post("/api/v1/send")
        async def send_message(req: SendMessageRequest):
            # Auth check
            from fastapi import Request
            # Token validation is done via dependency, see below

            msg_id = uuid.uuid4().hex[:12]
            session_id = req.session_id or uuid.uuid4().hex[:12]

            await self._handle_message(
                sender_id=req.client_id,
                chat_id=req.client_id,
                content=req.content,
                metadata={"_msg_id": msg_id},
                session_key=req.session_id,
            )
            return {"msg_id": msg_id, "session_id": session_id}

        @app.websocket("/api/v1/ws/{client_id}")
        async def websocket_endpoint(ws: WebSocket, client_id: str):
            # Validate token from query parameter
            token = ws.query_params.get("token", "")
            if token != self.config.api_token:
                await ws.close(code=4001, reason="Unauthorized")
                return

            await ws.accept()
            # Close existing connection for this client_id
            old_ws = self._ws_map.get(client_id)
            if old_ws:
                try:
                    await old_ws.close()
                except Exception:
                    pass
            self._ws_map[client_id] = ws
            logger.info("WebSocket connected: %s", client_id)

            try:
                await ws.send_json({"type": "connected"})
                while self._running:
                    try:
                        data = await asyncio.wait_for(ws.receive_json(), timeout=30)
                        if data.get("type") == "ping":
                            await ws.send_json({"type": "pong"})
                        elif data.get("type") == "send":
                            msg_id = uuid.uuid4().hex[:12]
                            content = data.get("content", "")
                            session_id = data.get("session_id")
                            await self._handle_message(
                                sender_id=client_id,
                                chat_id=client_id,
                                content=content,
                                metadata={"_msg_id": msg_id},
                                session_key=session_id,
                            )
                            await ws.send_json({
                                "type": "ack",
                                "msg_id": msg_id,
                            })
                    except asyncio.TimeoutError:
                        # Send keepalive ping
                        try:
                            await ws.send_json({"type": "ping"})
                        except Exception:
                            break
            except WebSocketDisconnect:
                pass
            except Exception:
                logger.exception("WebSocket error for %s", client_id)
            finally:
                self._ws_map.pop(client_id, None)
                logger.info("WebSocket disconnected: %s", client_id)

    # ------------------------------------------------------------------
    # Middleware for auth
    # ------------------------------------------------------------------

    # Add auth middleware to the FastAPI app
    # (Applied in _setup_routes as a dependency for protected endpoints)

    # ------------------------------------------------------------------
    # BaseChannel interface
    # ------------------------------------------------------------------

    async def start(self) -> None:
        if not self.config.api_token:
            logger.error("API channel: api_token not configured")
            return

        self._running = True

        # Add auth middleware
        from starlette.middleware.base import BaseHTTPMiddleware
        from starlette.requests import Request
        from starlette.responses import Response

        async def auth_middleware(request: Request, call_next):
            # Skip auth for health endpoint
            if request.url.path == "/api/v1/health":
                return await call_next(request)
            # Skip auth for WebSocket (auth handled in ws handler)
            if request.url.path.startswith("/api/v1/ws"):
                return await call_next(request)
            # Check Bearer token
            auth = request.headers.get("Authorization", "")
            if auth != f"Bearer {self.config.api_token}":
                return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
            return await call_next(request)

        self._app.add_middleware(BaseHTTPMiddleware, dispatch=auth_middleware)

        config = uvicorn.Config(
            self._app,
            host=self.config.host,
            port=self.config.port,
            log_level="warning",
        )
        self._server = uvicorn.Server(config)
        logger.info("API channel starting on %s:%s", self.config.host, self.config.port)

        # Run server
        asyncio.create_task(self._server.serve())
        await asyncio.sleep(0.5)  # Let server bind

    async def stop(self) -> None:
        self._running = False
        if self._server:
            self._server.should_exit = True
            await self._server.shutdown()
        for client_id, ws in list(self._ws_map.items()):
            try:
                await ws.close()
            except Exception:
                pass
        self._ws_map.clear()
        logger.info("API channel stopped")

    async def send(self, msg: OutboundMessage) -> None:
        ws = self._ws_map.get(msg.chat_id)
        if not ws:
            logger.warning("No WebSocket for %s, dropping message", msg.chat_id)
            return

        msg_id = msg.metadata.get("_msg_id", "")
        session_key = msg.metadata.get("_session_key", "")
        is_streaming = msg.metadata.get("_streaming", False)
        is_final = msg.metadata.get("_streaming_final", False)
        is_error = msg.metadata.get("_error", False)

        try:
            if is_error:
                await ws.send_json({
                    "type": "error",
                    "msg_id": msg_id,
                    "message": msg.content,
                })
            elif is_streaming and not is_final:
                await ws.send_json({
                    "type": "reply_delta",
                    "msg_id": msg_id,
                    "session_id": session_key,
                    "content": msg.content,
                })
            else:
                await ws.send_json({
                    "type": "reply",
                    "msg_id": msg_id,
                    "session_id": session_key,
                    "content": msg.content,
                })
        except Exception:
            logger.warning("WebSocket send failed for %s, removing connection", msg.chat_id)
            self._ws_map.pop(msg.chat_id, None)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_api_channel.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/openharness/channels/impl/api.py tests/test_api_channel.py
git commit -m "feat: add APIChannel with FastAPI HTTP + WebSocket server"
```

---

### Task 5: Register APIChannel in ChannelManager

**Files:**
- Modify: `src/openharness/channels/impl/manager.py`

- [ ] **Step 1: Add API channel registration to `_init_channels()`**

After the Matrix channel block (around line 151), before `self._validate_allow_from()`, add:

```python
        # API channel
        if self.config.channels.api.enabled:
            try:
                from openharness.channels.impl.api import APIChannel
                self.channels["api"] = APIChannel(
                    self.config.channels.api, self.bus
                )
                logger.info("API channel enabled on {}:{}",
                             self.config.channels.api.host,
                             self.config.channels.api.port)
            except ImportError as e:
                logger.warning("API channel not available: {}", e)
```

- [ ] **Step 2: Update `_validate_allow_from()` to skip API channel**

Replace the existing `_validate_allow_from` method (lines 155-161):

```python
    def _validate_allow_from(self) -> None:
        for name, ch in self.channels.items():
            if name == "api":
                continue  # API uses token auth, not allow_from
            if getattr(ch.config, "allow_from", None) == []:
                raise SystemExit(
                    f'Error: "{name}" has empty allowFrom (denies all). '
                    f'Set ["*"] to allow everyone, or add specific user IDs.'
                )
```

- [ ] **Step 3: Verify existing tests still pass**

Run: `uv run pytest tests/ -v --timeout=30`
Expected: All existing tests still pass

- [ ] **Step 4: Commit**

```bash
git add src/openharness/channels/impl/manager.py
git commit -m "feat: register APIChannel in ChannelManager"
```

---

### Task 6: Wire channels into main app startup

The channel system (ChannelManager + Bridge) is currently not connected to the main CLI entry point. When the API channel is enabled in config, we need to start the ChannelManager and the SessionAwareBridge alongside the normal OH session.

**Files:**
- Modify: `src/openharness/cli.py`
- Modify: `src/openharness/ui/app.py`

- [ ] **Step 1: Add channel startup helper to `ui/app.py`**

Add a new async function after the existing `run_repl` and `run_print_mode` functions:

```python
async def start_channels(settings) -> tuple | None:
    """Start channel system if any channels are enabled.

    Returns (ChannelManager, bridge) tuple, or None if no channels enabled.
    """
    from pathlib import Path
    from openharness.channels import ChannelManager, MessageBus
    from openharness.config.schema import Config

    config = Config()

    bus = MessageBus()
    manager = ChannelManager(config, bus)

    if not manager.channels:
        return None

    # Start the appropriate bridge
    bridge = None
    if "api" in manager.channels:
        from openharness.channels.adapter import SessionAwareBridge
        bridge = SessionAwareBridge(
            bus=bus,
            settings=settings,
            cwd=str(Path.cwd()),
            idle_timeout_minutes=config.channels.api.idle_timeout_minutes,
        )
    else:
        from openharness.channels.adapter import ChannelBridge
        # For non-API channels, create a single shared engine
        from openharness.api.client import AnthropicApiClient
        from openharness.engine.query_engine import QueryEngine
        from openharness.config.settings import PermissionSettings
        from openharness.permissions.checker import PermissionChecker
        from openharness.permissions.modes import PermissionMode
        from openharness.tools import create_default_tool_registry

        api_client = AnthropicApiClient(settings.resolve_api_key())
        engine = QueryEngine(
            api_client=api_client,
            tool_registry=create_default_tool_registry(),
            permission_checker=PermissionChecker(
                PermissionSettings(mode=PermissionMode.FULL_AUTO)
            ),
            cwd=str(Path.cwd()),
            model=settings.model,
            system_prompt="You are a helpful assistant.",
            max_tokens=settings.max_tokens,
            max_turns=settings.max_turns,
        )
        bridge = ChannelBridge(engine=engine, bus=bus)

    if bridge:
        await bridge.start()
    await manager.start_all()
    return (manager, bridge)
```

- [ ] **Step 2: Wire into CLI entry point**

In `src/openharness/cli.py`, find the main callback function (the `_main` or `callback` decorated with `@app.callback`). This is where `run_repl()` or `run_print_mode()` is called.

After the REPL starts (in `run_repl`), add channel startup. The key is to start channels as a background task in the same event loop:

In `src/openharness/ui/app.py`, modify `run_repl()` to start channels before the main loop:

```python
# At the end of run_repl(), before entering the main event loop:
# Start channels if configured
try:
    from openharness.ui.app import start_channels
    channel_result = await start_channels(settings)
    if channel_result:
        logger.info("Channels started: %s", channel_result[0].enabled_channels)
except Exception as e:
    logger.warning("Failed to start channels: %s", e)
```

The exact insertion point depends on the event loop structure. If `run_repl` uses `asyncio.run()`, the channel startup must happen inside the same async context. If channels need to run alongside the React TUI, they need to share the backend host's event loop.

- [ ] **Step 3: Verify channels start with API enabled**

Create a minimal config file to test:

```bash
mkdir -p ~/.openharness
cat > ~/.openharness/settings.json << 'EOF'
{
  "channels": {
    "api": {
      "enabled": true,
      "api_token": "test-token",
      "port": 18080
    }
  }
}
EOF
```

Run: `uv run oh --help`
Expected: No errors. The API channel should log its startup.

Clean up: `rm ~/.openharness/settings.json`

- [ ] **Step 4: Commit**

```bash
git add src/openharness/cli.py src/openharness/ui/app.py
git commit -m "feat: wire channel system into main app startup"
```

---

### Task 7: End-to-end smoke test

**Files:**
- Test: `tests/test_api_channel.py` (add E2E test)

- [ ] **Step 1: Write the E2E smoke test**

```python
class TestAPIChannelEndToEnd:
    """Integration test: HTTP send → bus → bridge → bus → WS receive."""

    @pytest.mark.asyncio
    async def test_full_message_round_trip(self):
        """Send a message via HTTP, verify it hits the bus, and simulate a reply via WS."""
        from openharness.channels.bus.queue import MessageBus
        from openharness.config.schema import ApiChannelConfig
        from openharness.channels.impl.api import APIChannel
        from openharness.channels.bus.events import OutboundMessage
        from httpx import ASGITransport, AsyncClient

        bus = MessageBus()
        config = ApiChannelConfig(
            enabled=True, api_token="test-token", allow_from=["*"]
        )
        channel = APIChannel(config, bus)

        # Set up a mock WebSocket
        mock_ws = AsyncMock()
        channel._ws_map["client-a"] = mock_ws

        # Send message via HTTP
        transport = ASGITransport(app=channel._app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/send",
                json={"client_id": "client-a", "content": "hello"},
                headers={"Authorization": "Bearer test-token"},
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
            metadata={"_msg_id": msg_id},
        ))

        # Verify WS received the reply
        mock_ws.send_json.assert_called()
        last_call = mock_ws.send_json.call_args_list[-1]
        sent = last_call[0][0]
        assert sent["type"] == "reply"
        assert sent["content"] == "Hello! How can I help?"
        assert sent["msg_id"] == msg_id
```

- [ ] **Step 2: Run all tests**

Run: `uv run pytest tests/test_api_channel.py tests/test_session_aware_bridge.py tests/test_config_schema.py -v`
Expected: All tests PASS

- [ ] **Step 3: Run full test suite to check for regressions**

Run: `uv run pytest tests/ -v --timeout=30`
Expected: All existing tests still pass

- [ ] **Step 4: Commit**

```bash
git add tests/test_api_channel.py
git commit -m "test: add E2E smoke test for API channel round-trip"
```

---

### Task 8: Final cleanup and push

- [ ] **Step 1: Run linter**

Run: `uv run ruff check src tests`
Expected: No errors. Fix any that appear.

- [ ] **Step 2: Verify all tests pass**

Run: `uv run pytest tests/ -v --timeout=30`
Expected: All tests PASS

- [ ] **Step 3: Push to remote**

```bash
git push origin master
```

Note: The implementation branch is `master` (based on `main`). Push to `master` as created during setup.
