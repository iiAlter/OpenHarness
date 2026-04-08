"""ChannelBridge: connects the MessageBus to a QueryEngine instance.

Usage::

    bridge = ChannelBridge(engine=query_engine, bus=message_bus)
    asyncio.create_task(bridge.run())

The bridge continuously consumes inbound messages from the bus, feeds them
to QueryEngine.submit_message(), and publishes the assembled reply as an
OutboundMessage back to the bus for delivery by ChannelManager.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from openharness.channels.bus.events import InboundMessage, OutboundMessage
from openharness.channels.bus.queue import MessageBus
from openharness.engine.stream_events import AssistantTextDelta, AssistantTurnComplete

if TYPE_CHECKING:
    from openharness.engine.query_engine import QueryEngine

logger = logging.getLogger(__name__)


class ChannelBridge:
    """Bridges inbound channel messages to the QueryEngine and routes replies back.

    One bridge instance should be created per QueryEngine.  It owns the asyncio
    loop integration and handles back-pressure through the MessageBus queues.
    """

    def __init__(self, *, engine: "QueryEngine", bus: MessageBus) -> None:
        self._engine = engine
        self._bus = bus
        self._running = False
        self._task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # Public control API
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the bridge loop as a background task."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="channel-bridge")
        logger.info("ChannelBridge started")

    async def stop(self) -> None:
        """Stop the bridge loop gracefully."""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("ChannelBridge stopped")

    async def run(self) -> None:
        """Run the bridge inline (blocks until stopped or cancelled)."""
        self._running = True
        try:
            await self._loop()
        finally:
            self._running = False

    # ------------------------------------------------------------------
    # Internal loop
    # ------------------------------------------------------------------

    async def _loop(self) -> None:
        """Main processing loop: consume → process → publish."""
        while self._running:
            try:
                msg = await asyncio.wait_for(
                    self._bus.consume_inbound(),
                    timeout=1.0,
                )
                await self._handle(msg)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("ChannelBridge: unhandled error processing message")

    async def _handle(self, msg: InboundMessage) -> None:
        """Process one inbound message and publish the reply."""
        logger.debug("ChannelBridge received from %s/%s", msg.channel, msg.chat_id)

        reply_parts: list[str] = []
        try:
            async for event in self._engine.submit_message(msg.content):
                if isinstance(event, AssistantTextDelta):
                    reply_parts.append(event.text)
                elif isinstance(event, AssistantTurnComplete):
                    # Turn is done; we'll send the accumulated text below
                    pass
        except Exception:
            logger.exception(
                "ChannelBridge: engine error for message from %s/%s",
                msg.channel,
                msg.chat_id,
            )
            reply_parts = ["[Error: failed to process your message]"]

        reply_text = "".join(reply_parts).strip()
        if not reply_text:
            logger.debug("ChannelBridge: empty reply, skipping publish")
            return

        outbound = OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=reply_text,
            metadata={"_session_key": msg.session_key},
        )
        await self._bus.publish_outbound(outbound)
        logger.debug(
            "ChannelBridge published reply to %s/%s (%d chars)",
            msg.channel,
            msg.chat_id,
            len(reply_text),
        )


class SessionAwareBridge:
    """Advanced bridge that maintains per-session QueryEngine instances.

    API channel messages (``msg.channel == "api"``) are routed to independent
    ``QueryEngine`` instances keyed by ``session_key``.  Each session has its
    own async queue so that different sessions can be processed concurrently
    while messages within the same session remain sequential.

    Non-API messages (Telegram, Discord, etc.) are routed to a single shared
    engine, preserving the original ``ChannelBridge`` behaviour.

    Idle sessions are periodically evicted: their conversation history is
    persisted to disk via ``session_storage`` and the in-memory engine is
    destroyed.  When a client reconnects with a ``session_id`` the history
    is loaded back and a new engine is constructed.
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

        # Per-session state
        self._session_engines: dict[str, QueryEngine] = {}
        self._session_last_active: dict[str, float] = {}
        self._session_queues: dict[str, asyncio.Queue[InboundMessage]] = {}
        self._session_tasks: dict[str, asyncio.Task] = {}

        # Shared engine for non-API channels
        self._shared_engine: QueryEngine | None = None

        # Background tasks
        self._route_task: asyncio.Task | None = None
        self._cleanup_task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # Public control API
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the cleanup task and route loop as background tasks."""
        if self._running:
            return
        self._running = True
        self._route_task = asyncio.create_task(self._route_loop(), name="session-aware-bridge-route")
        self._cleanup_task = asyncio.create_task(self._cleanup_loop(), name="session-aware-bridge-cleanup")
        logger.info("SessionAwareBridge started")

    async def stop(self) -> None:
        """Save active sessions, cancel tasks, and stop the bridge."""
        self._running = False

        # Save all active sessions
        for session_key in list(self._session_engines):
            try:
                await self._save_session(session_key)
            except Exception:
                logger.exception("SessionAwareBridge: error saving session %s", session_key)

        # Cancel session workers
        for task in self._session_tasks.values():
            task.cancel()
        for task in self._session_tasks.values():
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._session_tasks.clear()

        # Cancel background tasks
        for task in (self._cleanup_task, self._route_task):
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._route_task = None
        self._cleanup_task = None

        logger.info("SessionAwareBridge stopped")

    async def run(self) -> None:
        """Run the bridge inline (blocks until stopped or cancelled)."""
        self._running = True
        try:
            await self._route_loop()
        finally:
            self._running = False

    # ------------------------------------------------------------------
    # Route loop
    # ------------------------------------------------------------------

    async def _route_loop(self) -> None:
        """Consume from bus and route by channel type."""
        while self._running:
            try:
                msg = await asyncio.wait_for(
                    self._bus.consume_inbound(),
                    timeout=1.0,
                )
                if msg.channel == "api":
                    await self._dispatch_to_session(msg)
                else:
                    await self._dispatch_to_shared(msg)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("SessionAwareBridge: unhandled error in route loop")

    async def _process_one(self) -> None:
        """Process a single inbound message (useful for testing)."""
        msg = await self._bus.consume_inbound()
        if msg.channel == "api":
            await self._dispatch_to_session(msg)
        else:
            await self._dispatch_to_shared(msg)

    # ------------------------------------------------------------------
    # API session dispatch
    # ------------------------------------------------------------------

    async def _dispatch_to_session(self, msg: InboundMessage) -> None:
        """Route an API message to the per-session queue."""
        session_key = msg.session_key
        self._session_last_active[session_key] = time.time()

        if session_key not in self._session_queues:
            self._session_queues[session_key] = asyncio.Queue()
            task = asyncio.create_task(
                self._session_worker(session_key),
                name=f"session-worker-{session_key}",
            )
            self._session_tasks[session_key] = task

        await self._session_queues[session_key].put(msg)

    async def _session_worker(self, session_key: str) -> None:
        """Process messages for a session sequentially."""
        queue = self._session_queues[session_key]
        try:
            while self._running:
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=1.0)
                    await self._handle_session_message(session_key, msg)
                except asyncio.TimeoutError:
                    continue
                except asyncio.CancelledError:
                    break
                except Exception:
                    logger.exception(
                        "SessionAwareBridge: error in session worker for %s",
                        session_key,
                    )
        finally:
            logger.debug("SessionAwareBridge: worker exited for %s", session_key)

    async def _handle_session_message(self, session_key: str, msg: InboundMessage) -> None:
        """Process one message for an API session with streaming deltas."""
        logger.debug("SessionAwareBridge handling API message for %s", session_key)
        engine = await self._get_or_create_session_engine(session_key)
        self._session_last_active[session_key] = time.time()

        try:
            async for event in engine.submit_message(msg.content):
                if isinstance(event, AssistantTextDelta):
                    outbound = OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        content=event.text,
                        metadata={
                            "_session_key": session_key,
                            "_streaming": True,
                        },
                    )
                    await self._bus.publish_outbound(outbound)
                elif isinstance(event, AssistantTurnComplete):
                    # Send final streaming message marker
                    outbound = OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        content="",
                        metadata={
                            "_session_key": session_key,
                            "_streaming": True,
                            "_streaming_final": True,
                        },
                    )
                    await self._bus.publish_outbound(outbound)
        except Exception:
            logger.exception(
                "SessionAwareBridge: engine error for API session %s",
                session_key,
            )
            error_outbound = OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content="[Error: failed to process your message]",
                metadata={
                    "_session_key": session_key,
                    "_streaming": True,
                    "_streaming_final": True,
                },
            )
            await self._bus.publish_outbound(error_outbound)

    # ------------------------------------------------------------------
    # Non-API (shared) dispatch
    # ------------------------------------------------------------------

    async def _dispatch_to_shared(self, msg: InboundMessage) -> None:
        """Route a non-API message to the single shared engine."""
        if self._shared_engine is None:
            self._shared_engine = self._create_engine("_shared")
        await self._handle_shared_message(msg)

    async def _handle_shared_message(self, msg: InboundMessage) -> None:
        """Process one non-API message: accumulate text, single outbound."""
        logger.debug("SessionAwareBridge handling non-API message from %s/%s", msg.channel, msg.chat_id)

        reply_parts: list[str] = []
        try:
            async for event in self._shared_engine.submit_message(msg.content):
                if isinstance(event, AssistantTextDelta):
                    reply_parts.append(event.text)
                elif isinstance(event, AssistantTurnComplete):
                    pass
        except Exception:
            logger.exception(
                "SessionAwareBridge: engine error for message from %s/%s",
                msg.channel,
                msg.chat_id,
            )
            reply_parts = ["[Error: failed to process your message]"]

        reply_text = "".join(reply_parts).strip()
        if not reply_text:
            logger.debug("SessionAwareBridge: empty reply, skipping publish")
            return

        outbound = OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=reply_text,
            metadata={"_session_key": msg.session_key},
        )
        await self._bus.publish_outbound(outbound)
        logger.debug(
            "SessionAwareBridge published reply to %s/%s (%d chars)",
            msg.channel,
            msg.chat_id,
            len(reply_text),
        )

    # ------------------------------------------------------------------
    # Engine management
    # ------------------------------------------------------------------

    async def _get_or_create_session_engine(self, session_key: str) -> QueryEngine:
        """Return existing engine for session or create a new one."""
        if session_key not in self._session_engines:
            self._session_engines[session_key] = self._create_engine(session_key)
        return self._session_engines[session_key]

    def _create_engine(self, session_key: str) -> QueryEngine:
        """Construct a new QueryEngine for the given session."""
        from openharness.api.client import AnthropicApiClient
        from openharness.api.openai_client import OpenAICompatibleClient
        from openharness.config.settings import PermissionSettings
        from openharness.engine.query_engine import QueryEngine
        from openharness.permissions.checker import PermissionChecker
        from openharness.permissions.modes import PermissionMode
        from openharness.tools import create_default_tool_registry

        api_format = getattr(self._settings, "api_format", "anthropic")
        api_key = getattr(self._settings, "api_key", "")
        base_url = getattr(self._settings, "base_url", None)

        if api_format == "openai":
            api_client = OpenAICompatibleClient(api_key, base_url=base_url)
        else:
            api_client = AnthropicApiClient(api_key, base_url=base_url)

        return QueryEngine(
            api_client=api_client,
            tool_registry=create_default_tool_registry(),
            permission_checker=PermissionChecker(PermissionSettings(mode=PermissionMode.FULL_AUTO)),
            cwd=self._cwd,
            model=getattr(self._settings, "model", "claude-sonnet-4-20250514"),
            system_prompt=getattr(self._settings, "system_prompt", None) or "You are a helpful assistant.",
            max_tokens=getattr(self._settings, "max_tokens", 16384),
            max_turns=getattr(self._settings, "max_turns", 200),
        )

    # ------------------------------------------------------------------
    # Session persistence
    # ------------------------------------------------------------------

    async def _save_session(self, session_key: str) -> None:
        """Persist session state to disk."""
        engine = self._session_engines.get(session_key)
        if engine is None:
            return

        try:
            from openharness.services.session_storage import save_session_snapshot

            save_session_snapshot(
                cwd=self._cwd,
                model=engine._model,
                system_prompt=engine._system_prompt,
                messages=engine.messages,
                usage=engine.total_usage,
                session_id=session_key,
            )
            logger.debug("SessionAwareBridge: saved session %s", session_key)
        except Exception:
            logger.exception("SessionAwareBridge: failed to save session %s", session_key)

    async def _restore_session(self, session_key: str, session_id: str) -> None:
        """Load session state from disk and rebuild the engine."""
        try:
            from openharness.services.session_storage import load_session_by_id

            snapshot = load_session_by_id(self._cwd, session_id)
            if snapshot is None:
                logger.warning("SessionAwareBridge: no snapshot found for session %s", session_id)
                return

            engine = self._create_engine(session_key)

            from openharness.engine.messages import ConversationMessage

            messages = [ConversationMessage.model_validate(m) for m in snapshot.get("messages", [])]
            engine.load_messages(messages)
            self._session_engines[session_key] = engine
            self._session_last_active[session_key] = time.time()
            logger.info(
                "SessionAwareBridge: restored session %s (%d messages)",
                session_key,
                len(messages),
            )
        except Exception:
            logger.exception("SessionAwareBridge: failed to restore session %s", session_id)

    # ------------------------------------------------------------------
    # Idle cleanup
    # ------------------------------------------------------------------

    async def _cleanup_loop(self) -> None:
        """Periodically evict idle sessions."""
        while self._running:
            try:
                await asyncio.sleep(60)
                await self._cleanup_idle_sessions()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("SessionAwareBridge: error in cleanup loop")

    async def _cleanup_idle_sessions(self) -> None:
        """Evict engines that have been idle longer than the timeout."""
        now = time.time()
        timeout_seconds = self._idle_timeout_minutes * 60
        expired_keys = [
            key
            for key, last_active in self._session_last_active.items()
            if (now - last_active) > timeout_seconds
        ]
        for key in expired_keys:
            logger.info("SessionAwareBridge: evicting idle session %s", key)
            try:
                await self._save_session(key)
            except Exception:
                logger.exception("SessionAwareBridge: error saving idle session %s", key)
            self._session_engines.pop(key, None)
            self._session_last_active.pop(key, None)
            self._session_queues.pop(key, None)
            task = self._session_tasks.pop(key, None)
            if task is not None:
                task.cancel()
