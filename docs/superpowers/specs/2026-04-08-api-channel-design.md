# API Channel Design: External Communication with OpenHarness

**Date:** 2026-04-08
**Status:** Draft
**Scope:** Scenario 1 — External applications chat with OH

---

## 1. Goal

Provide a generic HTTP/WebSocket API that allows external applications to send messages to OpenHarness and receive replies. Implemented as a new Channel within the existing channels system, with a SessionAwareBridge to support concurrent multi-session conversations.

## 2. Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Protocol | WebSocket receive + HTTP REST send | Consistent with existing IM channel patterns |
| Integration | New Channel (`APIChannel`) | Reuses MessageBus + ChannelManager architecture |
| Concurrency | SessionAwareBridge with per-session QueryEngines | Enables parallel conversations from different clients |
| Authentication | Token-based (configurable `api_token`) | Prevents unauthorized access |
| Session persistence | Reuse existing `session_storage` module | Minimal new code, proven mechanism |
| Session timeout | 30 min idle (configurable) | Balance between UX and resource usage |

## 3. Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    External Clients (multiple)                │
│  Client A ──── WS /api/v1/ws/{client_id} ─────────┐         │
│  Client B ──── WS /api/v1/ws/{client_id} ─────────┤         │
│  Client C ──── HTTP POST /api/v1/send ─────────────┤         │
└─────────────────────────────────────────────────────┼────────┘
                                                      │
                                                      ▼
┌─────────────────────────────────────────────────────────────┐
│                       APIChannel                              │
│  (inherits BaseChannel, registered in ChannelManager)         │
│                                                               │
│  HTTP Server (FastAPI):                                       │
│    POST /api/v1/send          → accept message, return ack    │
│    GET  /api/v1/sessions      → list sessions for a client    │
│    GET  /api/v1/health        → health check (no auth)        │
│                                                               │
│  WebSocket Server (FastAPI WS):                               │
│    WS /api/v1/ws/{client_id}  → push replies to client        │
│                                  → heartbeat keep-alive        │
│                                  → reconnect with session_id   │
│                                  → also accepts send messages  │
│                                                               │
│  Internal state:                                              │
│    ws_map: dict[str, WebSocket]   # client_id → WS connection │
│                                                               │
│  send(msg: OutboundMessage):                                  │
│    → lookup ws_map[msg.chat_id] → ws.send(reply)              │
│    → if no active WS: log warning, message dropped            │
└───────────────────────────┬───────────────────────────────────┘
                            │ InboundMessage / OutboundMessage
                            ▼
┌─────────────────────────────────────────────────────────────┐
│            SessionAwareBridge (supersedes ChannelBridge)      │
│                                                               │
│  Consumes ALL inbound messages from the single MessageBus.    │
│  Routes to the appropriate handler:                           │
│                                                               │
│  API sessions:                                                │
│    session_engines: dict[str, QueryEngine]                     │
│    "api:client-a"  → QueryEngine-A  (independent history)     │
│    "api:client-b"  → QueryEngine-B  (independent history)     │
│    Each session processed as independent asyncio.Task.         │
│                                                               │
│  Non-API channels (Telegram, Discord, etc.):                  │
│    Routed to a shared default QueryEngine, serialized.        │
│    Behavior identical to the original ChannelBridge.          │
│                                                               │
│  Idle timeout (30 min):                                       │
│    → save history to disk (via session_storage)               │
│    → destroy in-memory QueryEngine                            │
│                                                               │
│  Reconnect:                                                   │
│    → load history from disk → rebuild QueryEngine             │
│    → conversation continues                                   │
└───────────────────────────────────────────────────────────────┘
```

### Message Flow

**Sending a message (inbound):**

```
Client → POST /api/v1/send {client_id, content, session_id?}
       → APIChannel validates token
       → APIChannel._handle_message(
             sender_id=client_id,
             chat_id=client_id,
             content=...,
             session_key_override=session_id  # if provided
           )
       → InboundMessage published to bus (single bus, single consumer)
       → return {msg_id, session_id} to client immediately
```

**Receiving a reply (outbound):**

```
SessionAwareBridge consumes from bus
  → routes by msg.session_key to per-session engine (API)
     or shared engine (non-API)
  → processes message, collects reply + streaming deltas
  → for API sessions: publishes OutboundMessage per streaming chunk
  → for non-API sessions: publishes single OutboundMessage (unchanged)
  → MessageBus.outbound → ChannelManager._dispatch_outbound()
  → APIChannel.send(msg)
  → ws_map[client_id].send(reply)
  → Client receives reply via WebSocket
```

### Single-Consumer Bus Resolution

The `MessageBus` uses `asyncio.Queue` — single consumer, destructive reads. The `SessionAwareBridge` is the **sole consumer** of `bus.inbound`. It consumes every message and routes internally:

- Messages with `msg.channel == "api"` → per-session engine pool (concurrent)
- All other messages → shared default engine (serialized, same as before)

The existing `ChannelBridge` is kept for backward compatibility but is **not started** when `SessionAwareBridge` is active. The bridge selection is determined at startup based on whether the API channel is enabled.

## 4. API Endpoints

### 4.1 Authentication

All endpoints except `/api/v1/health` require an `Authorization: Bearer <token>` header. The token is validated against `config.channels.api.api_token`. The health endpoint is unauthenticated for load balancer probes.

### 4.2 HTTP Endpoints

#### `POST /api/v1/send`

Send a message to OH and receive an acknowledgment. The reply is delivered asynchronously via WebSocket.

**Request:**
```json
{
  "client_id": "user-abc",
  "content": "Explain this codebase",
  "session_id": "optional-existing-session-id"
}
```

**Response:**
```json
{
  "msg_id": "msg-xyz123",
  "session_id": "auto-generated-or-existing"
}
```

**msg_id tracking:** The `msg_id` is a UUID generated by `APIChannel` at request time. It is stored in `InboundMessage.metadata["_msg_id"]`. The bridge propagates it to `OutboundMessage.metadata["_msg_id"]`. The `APIChannel.send()` method includes `msg_id` in the WebSocket JSON payload so clients can correlate replies to requests.

**session_id routing:** If `session_id` is provided in the request, it is passed as `session_key_override` to `_handle_message()`, which sets `InboundMessage.session_key` to the provided value. The bridge uses this to look up or create the matching engine. If not provided, the default `"api:{client_id}"` session key is used.

#### `GET /api/v1/sessions?client_id=user-abc`

List persisted sessions for a client.

**Response:**
```json
{
  "sessions": [
    {
      "session_id": "abc123",
      "summary": "Explain this codebase",
      "message_count": 12,
      "created_at": 1712505600.0
    }
  ]
}
```

#### `GET /api/v1/health`

Unauthenticated health check endpoint.

**Response:**
```json
{
  "status": "ok",
  "channels": ["api", "telegram"]
}
```

### 4.3 WebSocket Endpoint

#### `WS /api/v1/ws/{client_id}?session_id=optional`

Connect to receive real-time replies. Optional `session_id` query parameter to resume a previous conversation.

**Constraints:**
- One active WS connection per `client_id`. If a second connection arrives with the same `client_id`, the first is disconnected.
- If a client sends a new message while the previous one is still being processed, the new message is queued and processed after the current one completes (per-session serialization).

**Client-to-server messages (JSON):**
```json
{"type": "ping"}
{"type": "send", "content": "Hello OH", "session_id": "optional"}
```

**Server-to-client messages (JSON):**
```json
{"type": "pong"}
{"type": "reply", "msg_id": "msg-xyz123", "session_id": "abc123", "content": "I can help you with..."}
{"type": "reply_delta", "msg_id": "msg-xyz123", "session_id": "abc123", "content": "I can help"}
{"type": "error", "msg_id": "msg-xyz123", "message": "Failed to process"}
{"type": "session_restored", "session_id": "abc123", "message_count": 12}
```

**HTTP send without active WS:** If a client sends via `POST /api/v1/send` but has no active WebSocket connection, the message is still processed by the engine. The `OutboundMessage` reaches `APIChannel.send()`, which finds no WS connection in `ws_map`, logs a warning, and drops the reply. The client is responsible for maintaining a WS connection to receive replies.

## 5. SessionAwareBridge

Added to `src/openharness/channels/adapter.py` alongside the existing `ChannelBridge`. At startup, if the API channel is enabled, `SessionAwareBridge` is instantiated instead of `ChannelBridge`. If the API channel is not enabled, the original `ChannelBridge` is used — zero impact on existing deployments.

### Key behaviors

- **Sole consumer of bus.inbound**: Consumes all messages, routes internally by channel type
- **API routing**: Messages with `msg.channel == "api"` → per-session engine pool, each processed as an independent `asyncio.Task` for concurrency
- **Non-API routing**: Messages from other channels → single shared engine, processed sequentially (identical to current `ChannelBridge` behavior)
- **Per-session engine pool**: `dict[str, QueryEngine]` keyed by `session_key`
- **Streaming for API**: For API sessions, the bridge publishes incremental `OutboundMessage`s (one per `AssistantTextDelta`) to enable real-time streaming via WebSocket. Non-API sessions continue to publish a single accumulated reply (unchanged behavior).
- **Engine lifecycle**:
  - **Create**: On first message from a new `session_key`, construct a `QueryEngine` directly (not via `build_runtime()` — see Section 7)
  - **Reuse**: Subsequent messages from the same `session_key` use the existing engine
  - **Timeout**: After 30 min idle, save conversation history to disk via `session_storage.save_session_snapshot()`, then destroy the engine
  - **Restore**: When a client reconnects with a `session_id`, load history via `session_storage.load_session_by_id()` and reconstruct the engine
- **Graceful shutdown**: On `stop()`, save all active session states to disk before destroying engines
- **Per-session queue**: Each session has its own `asyncio.Queue` for inbound messages. This ensures messages for the same session are processed in order while different sessions run concurrently.

### Engine construction for API sessions

The `SessionAwareBridge` constructs `QueryEngine` instances directly, bypassing the UI-layer `build_runtime()`. This avoids pulling in UI-specific dependencies (AppStateStore, CommandRegistry, HookReloader, keybindings). The bridge creates:

- An API client (same provider as configured in settings)
- A tool registry
- Auto-approve permission callbacks (always returns `True`) and empty question callbacks (always returns `""`) — the API channel has no interactive terminal
- A hook executor (optional, can be disabled for headless mode)

### Session persistence directory

API channel sessions use a dedicated `cwd` value for `session_storage` functions: `Path.home() / ".openharness" / "api-sessions"`. Note that `session_storage.get_project_session_dir(cwd)` uses `cwd` as a namespace key — it computes a directory as `get_sessions_dir() / "api-sessions-<hash>"` under the existing sessions hierarchy. The actual files end up under `~/.openharness/data/sessions/`, isolated from CLI project-based sessions.

### Streaming delta metadata

Streaming `OutboundMessage`s from the bridge carry `metadata["_streaming"] = True` and `metadata["_streaming_final"] = True` on the last chunk. The `ChannelManager._dispatch_outbound()` must pass these through without filtering (they are not `_progress` or `_tool_hint` messages). The API channel's `send()` method assembles streaming chunks into WebSocket `reply_delta` messages, and the final chunk into a `reply` message.

### WebSocket send exception handling

If `APIChannel.send()` encounters a `ws.send()` exception (e.g., connection closed mid-write), it should:
1. Remove the client from `ws_map` (stale connection)
2. Log a warning
3. Do NOT retry — the client must reconnect

This prevents stale connections from accumulating in `ws_map`.

### Idle timeout and cleanup

```
Session last active time tracked per engine.

Every 60 seconds, a cleanup task scans all engines:
  - idle > idle_timeout_minutes → save to disk → remove from memory
  - disk files older than session_retention_days → delete
```

## 6. APIChannel Implementation

New file: `src/openharness/channels/impl/api.py`

### Interface (BaseChannel compliance)

```python
class APIChannel(BaseChannel):
    name = "api"

    async def start(self) -> None:
        """Start FastAPI server (HTTP + WS) on configured host:port."""

    async def stop(self) -> None:
        """Gracefully shutdown FastAPI server and all WS connections."""

    async def send(self, msg: OutboundMessage) -> None:
        """Push reply to the WebSocket connection identified by msg.chat_id."""
```

### Internal state

- `ws_map: dict[str, WebSocket]` — maps `client_id` to active WebSocket connection. Enforces one connection per `client_id`.
- `_app: FastAPI` — the FastAPI application
- `_server: uvicorn.Server` — the running server instance

### Connection lifecycle

```
1. Client connects via WS /api/v1/ws/{client_id}?token=...&session_id=...
   → validate token
   → if ws_map[client_id] exists: close old connection
   → register ws_map[client_id] = websocket
   → send {"type": "connected"}
   → if session_id provided: trigger bridge to restore session
   → send {"type": "session_restored", ...} if restored

2. Client sends message (via HTTP POST or WS {"type": "send", ...})
   → generate msg_id (UUID)
   → publish InboundMessage to bus (msg_id in metadata)
   → return msg_id to client

3. Bridge processes → publishes OutboundMessage(s)
   → ChannelManager dispatches → APIChannel.send()
   → lookup ws_map[msg.chat_id]
   → if found: ws.send(reply JSON with msg_id)
   → if not found: log warning, drop

4. Client disconnects
   → remove from ws_map
   → bridge idle timeout countdown begins

5. Client reconnects (same client_id + session_id)
   → bridge restores engine from disk
   → send {"type": "session_restored", ...}
```

## 7. Configuration

### Config module

All channel configs (TelegramConfig, DiscordConfig, etc.) live in `openharness.config.schema` which is imported by `ChannelManager`. This module is not currently in the source tree at `src/openharness/config/schema.py` (it may be generated or installed separately). The `ApiChannelConfig` class must be added to this same module, wherever it is defined at runtime. If `schema.py` needs to be created in `src/openharness/config/`, it should contain all the channel config classes referenced by `manager.py`.

```python
class ApiChannelConfig(BaseModel):
    enabled: bool = False
    api_token: str = ""  # required when enabled
    host: str = "0.0.0.0"
    port: int = 8080
    idle_timeout_minutes: int = 30
    session_retention_days: int = 7
    allow_from: list[str] = ["*"]  # configure to restrict access
```

Note: `allow_from` defaults to `["*"]` (allow all) to be consistent with the existing `_validate_allow_from()` in `ChannelManager`, which raises `SystemExit` on empty lists. Users who want to restrict access should set specific client IDs. A warning log should be emitted at startup when `["*"]` is used for the API channel, advising the user to configure specific client IDs.

### _validate_allow_from update

The existing `_validate_allow_from()` in `ChannelManager` must be updated to skip the API channel, since the API channel uses token-based authentication (`api_token`) as its primary access control — not `allow_from`. The `allow_from` list for the API channel is an optional additional restriction, not the primary gate. Add to the validation:

```python
def _validate_allow_from(self) -> None:
    for name, ch in self.channels.items():
        if name == "api":
            continue  # API uses token auth, not allow_from
        if getattr(ch.config, "allow_from", None) == []:
            raise SystemExit(...)
```

Register API channel in `ChannelManager._init_channels()`, following the existing pattern with `try/except ImportError`:

```python
if self.config.channels.api.enabled:
    try:
        from openharness.channels.impl.api import APIChannel
        self.channels["api"] = APIChannel(self.config.channels.api, self.bus)
        logger.info("API channel enabled on {}:{}",
                     self.config.channels.api.host,
                     self.config.channels.api.port)
    except ImportError as e:
        logger.warning("API channel not available: {}", e)
```

### Timeout configuration

```json
{
  "channels": {
    "api": {
      "enabled": true,
      "api_token": "your-secret-token",
      "host": "0.0.0.0",
      "port": 8080,
      "idle_timeout_minutes": 30,
      "session_retention_days": 7,
      "allow_from": ["my-app-1", "my-app-2"]
    }
  }
}
```

## 8. File Changes Summary

| File | Change |
|------|--------|
| `src/openharness/channels/impl/api.py` | **New** — APIChannel implementation (FastAPI HTTP + WS) |
| `src/openharness/channels/adapter.py` | **Modify** — Add SessionAwareBridge alongside existing ChannelBridge |
| `src/openharness/channels/impl/manager.py` | **Modify** — Register APIChannel in `_init_channels()`, skip API in `_validate_allow_from()` |
| `src/openharness/config/schema.py` (or equivalent) | **Modify** — Add ApiChannelConfig |
| `pyproject.toml` | **Modify** — Add dependencies: `fastapi`, `uvicorn[standard]` |

## 9. Dependencies

- **FastAPI** — HTTP + WebSocket server framework
- **uvicorn[standard]** — ASGI server (includes `websockets` and `httptools`)

Both are lightweight, widely used, and async-native — consistent with the project's asyncio architecture.

## 10. Out of Scope (Future)

These are explicitly not part of this design but noted for future consideration:

- Full agent management API (session CRUD, tool config, permission management)
- Embedding OH into third-party products (multi-tenant, per-user isolation)
- Rate limiting per client
- Streaming partial responses via HTTP (SSE)
- File/media upload through the API
- Retry/queue for failed WebSocket deliveries
