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
│    POST /api/v1/send          → accept message, return msg_id │
│    GET  /api/v1/sessions      → list sessions for a client    │
│    GET  /api/v1/health        → health check                  │
│                                                               │
│  WebSocket Server (FastAPI WS):                               │
│    WS /api/v1/ws/{client_id}  → push replies to client        │
│                                  → heartbeat keep-alive        │
│                                  → reconnect with session_id   │
│                                                               │
│  Internal state:                                              │
│    ws_map: dict[str, WebSocket]   # client_id → WS connection │
│                                                               │
│  send(msg: OutboundMessage):                                  │
│    → lookup ws_map[msg.chat_id] → ws.send(reply)              │
└───────────────────────────┬───────────────────────────────────┘
                            │ InboundMessage / OutboundMessage
                            ▼
┌─────────────────────────────────────────────────────────────┐
│               SessionAwareBridge (replaces ChannelBridge)     │
│                                                               │
│  session_engines: dict[str, QueryEngine]                       │
│    "api:client-a"  → QueryEngine-A  (independent history)     │
│    "api:client-b"  → QueryEngine-B  (independent history)     │
│    "telegram:123"  → shared default engine (unchanged)        │
│                                                               │
│  Per-session processing as independent asyncio.Tasks →         │
│    concurrent execution, no blocking between sessions          │
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
       → APIChannel._handle_message(sender_id=client_id, chat_id=client_id, content=...)
       → MessageBus.inbound.put(InboundMessage)
       → return {msg_id, session_id} to client
```

**Receiving a reply (outbound):**

```
SessionAwareBridge processes InboundMessage
  → yields OutboundMessage(channel="api", chat_id=client_id, content=reply)
  → MessageBus.outbound.put(OutboundMessage)
  → ChannelManager._dispatch_outbound()
  → APIChannel.send(msg)
  → ws_map[client_id].send(reply)
  → Client receives reply via WebSocket
```

## 4. API Endpoints

### 4.1 Authentication

All endpoints require an `Authorization: Bearer <token>` header. The token is validated against `config.channels.api.api_token`.

### 4.2 HTTP Endpoints

#### `POST /api/v1/send`

Send a message to OH and receive a message ID. The reply is delivered asynchronously via WebSocket.

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

Health check endpoint.

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

**Client-to-server messages (JSON):**
```json
{"type": "ping"}
{"type": "send", "content": "Hello OH", "session_id": "optional"}
```

**Server-to-client messages (JSON):**
```json
{"type": "pong"}
{"type": "reply", "session_id": "abc123", "content": "I can help you with..."}
{"type": "reply_delta", "session_id": "abc123", "content": "I can help"}
{"type": "error", "message": "Failed to process"}
{"type": "session_restored", "session_id": "abc123", "message_count": 12}
```

## 5. SessionAwareBridge

Replaces `ChannelBridge` in `src/openharness/channels/adapter.py`.

### Key behaviors

- **Per-session engine pool**: Maintains a `dict[str, QueryEngine]` keyed by `session_key`
- **Concurrent processing**: Each session's message processing runs as an independent `asyncio.Task`, so multiple clients are served simultaneously
- **Backward compatible**: For non-API channels (Telegram, Discord, etc.), behavior remains unchanged — messages are still processed through the bus as before
- **Engine lifecycle**:
  - **Create**: On first message from a new `session_key`, build a fresh `QueryEngine` via `build_runtime()`
  - **Reuse**: Subsequent messages from the same `session_key` use the existing engine
  - **Timeout**: After 30 min idle, save conversation history to disk via `session_storage.save_session_snapshot()`, then destroy the engine
  - **Restore**: When a client reconnects with a `session_id`, load history via `session_storage.load_session_by_id()` and rebuild the engine

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
      "session_retention_days": 7
    }
  }
}
```

- `idle_timeout_minutes`: How long an idle engine stays in memory (default: 30)
- `session_retention_days`: How long session files are kept on disk (default: 7)

## 6. APIChannel Implementation

New file: `src/openharness/channels/impl/api.py`

### Interface (BaseChannel compliance)

```python
class APIChannel(BaseChannel):
    name = "api"

    async def start(self) -> None:
        """Start FastAPI server (HTTP + WS) on configured host:port."""

    async def stop(self) -> None:
        """Gracefully shutdown FastAPI server."""

    async def send(self, msg: OutboundMessage) -> None:
        """Push reply to the WebSocket connection identified by msg.chat_id."""
```

### Internal state

- `ws_map: dict[str, WebSocket]` — maps `client_id` to active WebSocket connection
- `_app: FastAPI` — the FastAPI application
- `_server: uvicorn.Server` — the running server instance

### Connection lifecycle

```
1. Client connects via WS /api/v1/ws/{client_id}
   → validate token
   → register in ws_map[client_id] = websocket
   → send {"type": "connected"}

2. Client sends message (via HTTP POST or WS JSON)
   → publish InboundMessage to bus

3. Bridge processes → publishes OutboundMessage
   → ChannelManager dispatches → APIChannel.send()
   → push reply via ws_map[client_id].send()

4. Client disconnects
   → remove from ws_map
   → bridge schedules idle timeout countdown

5. Client reconnects with session_id
   → bridge restores engine from disk
   → send {"type": "session_restored", ...}
```

## 7. Configuration

Add to `src/openharness/config/schema.py`:

```python
class ApiChannelConfig(BaseModel):
    enabled: bool = False
    api_token: str = ""
    host: str = "0.0.0.0"
    port: int = 8080
    idle_timeout_minutes: int = 30
    session_retention_days: int = 7
    allow_from: list[str] = ["*"]  # client_id allowlist
```

Register in `ChannelManager._init_channels()`:

```python
if self.config.channels.api.enabled:
    from openharness.channels.impl.api import APIChannel
    self.channels["api"] = APIChannel(self.config.channels.api, self.bus)
```

## 8. File Changes Summary

| File | Change |
|------|--------|
| `src/openharness/channels/impl/api.py` | **New** — APIChannel implementation (FastAPI HTTP + WS) |
| `src/openharness/channels/adapter.py` | **Modify** — Add SessionAwareBridge alongside existing ChannelBridge |
| `src/openharness/channels/impl/manager.py` | **Modify** — Register APIChannel |
| `src/openharness/config/schema.py` | **Modify** — Add ApiChannelConfig |
| `pyproject.toml` | **Modify** — Add dependencies: `fastapi`, `uvicorn` (standard), `websockets` |

## 9. Dependencies

- **FastAPI** — HTTP + WebSocket server framework
- **uvicorn** — ASGI server (with `standard` extras for `websockets` and `httptools`)

Both are lightweight, widely used, and async-native — consistent with the project's asyncio architecture.

## 10. Out of Scope (Future)

These are explicitly not part of this design but noted for future consideration:

- Full agent management API (session CRUD, tool config, permission management)
- Embedding OH into third-party products (multi-tenant, per-user isolation)
- Rate limiting per client
- Streaming partial responses via HTTP (SSE)
- File/media upload through the API
