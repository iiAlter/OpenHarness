"""Interactive session entry points."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from openharness.api.client import SupportsStreamingMessages
from openharness.engine.stream_events import StreamEvent
from openharness.ui.backend_host import run_backend_host
from openharness.ui.react_launcher import launch_react_tui
from openharness.ui.runtime import build_runtime, close_runtime, handle_line, start_runtime

if TYPE_CHECKING:
    from openharness.channels.adapter import ChannelBridge, SessionAwareBridge
    from openharness.channels.impl.manager import ChannelManager

logger = logging.getLogger(__name__)


async def start_channels(
    *,
    cwd: str | None = None,
    settings: object | None = None,
) -> tuple["ChannelManager", "ChannelBridge | SessionAwareBridge"] | None:
    """Start the channel system as background tasks.

    Loads channel configuration, creates the MessageBus and ChannelManager,
    picks the appropriate bridge (SessionAwareBridge when API channel is
    enabled, ChannelBridge otherwise), and starts everything as background
    asyncio tasks so the main REPL is not blocked.

    Returns ``(manager, bridge)`` on success, or ``None`` when no channels
    are enabled (or on startup failure).
    """
    try:
        from openharness.channels import ChannelManager, MessageBus
        from openharness.channels.adapter import ChannelBridge, SessionAwareBridge
        from openharness.config.schema import Config

        config = Config()
        bus = MessageBus()
        manager = ChannelManager(config, bus)

        if not manager.channels:
            return None

        work_dir = cwd or str(Path.cwd())

        if "api" in manager.channels:
            bridge: ChannelBridge | SessionAwareBridge = SessionAwareBridge(
                bus=bus,
                settings=settings or config,
                cwd=work_dir,
            )
        else:
            from openharness.api.client import AnthropicApiClient
            from openharness.config.settings import PermissionSettings
            from openharness.engine.query_engine import QueryEngine
            from openharness.permissions.checker import PermissionChecker
            from openharness.permissions.modes import PermissionMode
            from openharness.tools import create_default_tool_registry

            api_key = ""
            base_url = None
            model = "claude-sonnet-4-20250514"
            if settings is not None:
                api_key = getattr(settings, "api_key", "") or ""
                base_url = getattr(settings, "base_url", None)
                model = getattr(settings, "model", model)

            engine = QueryEngine(
                api_client=AnthropicApiClient(api_key=api_key, base_url=base_url),
                tool_registry=create_default_tool_registry(),
                permission_checker=PermissionChecker(
                    PermissionSettings(mode=PermissionMode.FULL_AUTO)
                ),
                cwd=work_dir,
                model=model,
                max_turns=200,
            )
            bridge = ChannelBridge(engine=engine, bus=bus)

        await bridge.start()
        asyncio.create_task(manager.start_all(), name="channel-manager")
        logger.info("Channel system started (%s)", ", ".join(manager.enabled_channels))
        return manager, bridge

    except Exception:
        logger.exception("Failed to start channel system; continuing without channels")
        return None


async def run_repl(
    *,
    prompt: str | None = None,
    cwd: str | None = None,
    model: str | None = None,
    max_turns: int | None = None,
    base_url: str | None = None,
    system_prompt: str | None = None,
    api_key: str | None = None,
    api_format: str | None = None,
    api_client: SupportsStreamingMessages | None = None,
    backend_only: bool = False,
    restore_messages: list[dict] | None = None,
) -> None:
    """Run the default OpenHarness interactive application (React TUI)."""
    # Load settings for channel startup
    from openharness.config import load_settings
    _settings = load_settings()

    # Start channels as background tasks (non-blocking)
    _channel_handle = await start_channels(cwd=cwd, settings=_settings)

    if backend_only:
        await run_backend_host(
            cwd=cwd,
            model=model,
            max_turns=max_turns,
            base_url=base_url,
            system_prompt=system_prompt,
            api_key=api_key,
            api_format=api_format,
            api_client=api_client,
            restore_messages=restore_messages,
        )
        return

    exit_code = await launch_react_tui(
        prompt=prompt,
        cwd=cwd,
        model=model,
        max_turns=max_turns,
        base_url=base_url,
        system_prompt=system_prompt,
        api_key=api_key,
        api_format=api_format,
    )
    if exit_code != 0:
        raise SystemExit(exit_code)


async def run_print_mode(
    *,
    prompt: str,
    output_format: str = "text",
    cwd: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    system_prompt: str | None = None,
    append_system_prompt: str | None = None,
    api_key: str | None = None,
    api_format: str | None = None,
    api_client: SupportsStreamingMessages | None = None,
    permission_mode: str | None = None,
    max_turns: int | None = None,
) -> None:
    """Non-interactive mode: submit prompt, stream output, exit."""
    from openharness.engine.stream_events import (
        AssistantTextDelta,
        AssistantTurnComplete,
        ToolExecutionCompleted,
        ToolExecutionStarted,
    )

    async def _noop_permission(tool_name: str, reason: str) -> bool:
        return True

    async def _noop_ask(question: str) -> str:
        return ""

    bundle = await build_runtime(
        prompt=prompt,
        model=model,
        max_turns=max_turns,
        base_url=base_url,
        system_prompt=system_prompt,
        api_key=api_key,
        api_format=api_format,
        api_client=api_client,
        permission_prompt=_noop_permission,
        ask_user_prompt=_noop_ask,
    )
    await start_runtime(bundle)

    collected_text = ""
    events_list: list[dict] = []

    try:
        async def _print_system(message: str) -> None:
            nonlocal collected_text
            if output_format == "text":
                print(message, file=sys.stderr)
            elif output_format == "stream-json":
                obj = {"type": "system", "message": message}
                print(json.dumps(obj), flush=True)
                events_list.append(obj)

        async def _render_event(event: StreamEvent) -> None:
            nonlocal collected_text
            if isinstance(event, AssistantTextDelta):
                collected_text += event.text
                if output_format == "text":
                    sys.stdout.write(event.text)
                    sys.stdout.flush()
                elif output_format == "stream-json":
                    obj = {"type": "assistant_delta", "text": event.text}
                    print(json.dumps(obj), flush=True)
                    events_list.append(obj)
            elif isinstance(event, AssistantTurnComplete):
                if output_format == "text":
                    sys.stdout.write("\n")
                    sys.stdout.flush()
                elif output_format == "stream-json":
                    obj = {"type": "assistant_complete", "text": event.message.text.strip()}
                    print(json.dumps(obj), flush=True)
                    events_list.append(obj)
            elif isinstance(event, ToolExecutionStarted):
                if output_format == "stream-json":
                    obj = {"type": "tool_started", "tool_name": event.tool_name, "tool_input": event.tool_input}
                    print(json.dumps(obj), flush=True)
                    events_list.append(obj)
            elif isinstance(event, ToolExecutionCompleted):
                if output_format == "stream-json":
                    obj = {"type": "tool_completed", "tool_name": event.tool_name, "output": event.output, "is_error": event.is_error}
                    print(json.dumps(obj), flush=True)
                    events_list.append(obj)

        async def _clear_output() -> None:
            pass

        await handle_line(
            bundle,
            prompt,
            print_system=_print_system,
            render_event=_render_event,
            clear_output=_clear_output,
        )

        if output_format == "json":
            result = {"type": "result", "text": collected_text.strip()}
            print(json.dumps(result))
    finally:
        await close_runtime(bundle)
