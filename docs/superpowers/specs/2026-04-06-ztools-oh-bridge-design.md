# ZTools - OpenHarness Bridge Plugin

## Overview

A ZTools plugin (Vue template) that bridges to OpenHarness via a Python WebSocket bridge, enabling persistent chat sessions with streaming responses, tool call visualization, permission approval, and multi-session management.

## Architecture

```
ZTools UI (Vue)
    ↕ ztools API / IPC
preload.js
    ↕ WebSocket (localhost, dynamic port via bridge stdout)
oh_bridge.py (Python)
    ↕ stdin/stdout (OHJSON:-prefixed JSON-lines)
openharness --backend-only --cwd <path>
```

### Why this approach

- preload.js cannot reliably manage long-lived stdin/stdout streams with a child process in Electron
- A Python bridge script is natural since OH itself requires Python
- WebSocket provides a stable, buffered, reconnectable transport
- Each session maps to an independent OH process managed by the bridge

### Multi-Session Model

The OH `--backend-only` protocol is single-session per process — there is no `create_session` or `destroy_session` request type. Multi-session support works by having the bridge manage multiple OH child processes:

- The bridge listens on a single WebSocket port
- Each WS connection is assigned a `channel_id`
- Each channel spawns its own OH child process
- preload.js maintains multiple WS connections (one per session)
- SessionList UI switches between WS connections at the preload layer

## OH Backend Protocol

OH's `--backend-only` mode uses **line-buffered JSON-lines with a prefix**:

### Outbound (OH → frontend)

Every line written to stdout is prefixed with `OHJSON:`:

```
OHJSON:{"type":"ready","state":{...},"tasks":[],...}
OHJSON:{"type":"assistant_delta","message":"Hello"}
OHJSON:{"type":"assistant_complete","message":"Hello world","item":{...}}
OHJSON:{"type":"line_complete"}
```

Non-prefixed lines are log output and should be ignored (or logged to stderr by the bridge).

### Inbound (frontend → OH)

OH reads raw JSON-lines from stdin (no prefix):

```
{"type":"submit_line","line":"help me write a function"}
{"type":"permission_response","request_id":"abc","allowed":true}
{"type":"question_response","request_id":"xyz","answer":"yes"}
{"type":"list_sessions"}
{"type":"shutdown"}
```

### Event State Machine

The UI must track a `busy` state:

1. User sends `submit_line` → `busy = true`, disable input
2. Backend sends `assistant_delta` events → append to streaming text buffer
3. Backend sends `assistant_complete` → finalize message, add to transcript, `busy` may remain true (tools follow)
4. Backend sends `tool_started` → show tool card with spinner
5. Backend sends `tool_completed` → update tool card with result
6. Steps 2-5 may repeat for multiple turns
7. Backend sends `line_complete` → `busy = false`, re-enable input

### `ready` Event

The first event emitted after OH starts. Must be captured to initialize UI state:

```json
{
  "type": "ready",
  "state": {
    "model": "claude-sonnet-4-20250514",
    "cwd": "/home/user/project",
    "provider": "anthropic",
    "permission_mode": "Default",
    "theme": "default",
    "fast_mode": false,
    "mcp_connected": 2,
    ...
  },
  "tasks": [],
  "mcp_servers": [],
  "bridge_sessions": [],
  "commands": ["/help", "/clear", "/fast", ...]
}
```

## Plugin Structure

```
ztools-oh-bridge/
├── plugin.json              # ZTools plugin manifest
├── package.json             # Node dependencies
├── preload/
│   └── index.js             # ZTools preload script
├── src/
│   ├── App.vue              # Main chat panel
│   ├── components/
│   │   ├── ChatMessage.vue  # Single message (text/tool)
│   │   ├── InputBar.vue     # Input box + send button
│   │   ├── SessionList.vue  # Multi-session sidebar
│   │   ├── PermissionModal.vue  # Permission approval (allow/deny)
│   │   └── QuestionModal.vue    # Question input (text answer)
│   └── main.js              # Vue entry
├── bridge/
│   ├── oh_bridge.py         # WebSocket → OH bridge
│   └── requirements.txt     # Python deps (websockets only)
└── icon.png                 # Plugin icon
```

## oh_bridge.py

### Responsibilities

- Start OH `--backend-only` subprocess per channel
- Bridge `OHJSON:`-prefixed JSON-lines ↔ WebSocket messages
- Heartbeat (30s ping/pong)
- Cleanup child processes on disconnect
- Log bridge events to stderr for diagnostics

### Startup Flow

1. Parse CLI args: `--port 0` (auto-assign, print actual port to stdout as `PORT:<number>`)
2. Start WebSocket server on the assigned port
3. Print `READY:<port>` to stdout (preload.js reads this to know which port to connect to)
4. On WS client connect → assign `channel_id` → spawn `openharness --backend-only --cwd <path>`
5. Begin line-buffered reading of OH stdout:
   - For each line: if starts with `OHJSON:` → strip prefix, parse JSON, forward as WS message
   - Non-prefixed lines → write to bridge stderr (log)
6. OH emits `ready` event → forward to WS client
7. Bidirectional message forwarding begins

### Channel / Session Model

- The bridge maintains a map of `channel_id → (ws_connection, oh_process)`
- Each WS connection maps to exactly one OH process
- When a WS connection closes → terminate the corresponding OH process
- When OH process exits → send error to WS client, clean up channel

### Message Forwarding

| Direction | Mapping |
|-----------|---------|
| OH stdout → WS | Line-buffered read. Strip `OHJSON:` prefix, parse JSON, forward as WS JSON message. Non-prefixed lines → log to stderr. |
| WS → OH stdin | Serialize WS JSON message as single JSON-line + `\n`, write to OH stdin. No prefix needed. |
| OH exit → WS | Send `{"type":"error","message":"OH process exited with code <N>"}` |
| Bridge error → WS | Send `{"type":"error","message":"<description>"}` |

### Dependencies

- `websockets` (Python library) — only dependency
- `asyncio` (stdlib) — for subprocess and IO management

### Port Selection

Use `--port 0` to let the OS assign an available port, avoiding conflicts with other ZTools instances. The bridge prints `READY:<port>` to stdout so preload.js knows where to connect.

## preload.js

### Responsibilities

- Start oh_bridge.py as child process
- Read bridge stdout to discover the assigned port (`READY:<port>`)
- Manage WebSocket connection(s)
- Expose API to Vue UI via `ztools` global object

### API Exposed to Vue UI

```typescript
ztools.oh = {
  // Send prompt to active session
  sendPrompt(text: string): void

  // Respond to permission request
  respondPermission(requestId: string, allowed: boolean): void

  // Respond to question request
  respondQuestion(requestId: string, answer: string): void

  // Session management (multi-process model)
  createSession(cwd: string, model?: string): string  // returns session_id
  destroySession(sessionId: string): void              // closes WS + kills OH process
  switchSession(sessionId: string): void               // changes which WS events go to UI
  getSessions(): Session[]

  // Event listener
  onEvent(callback: (event: BackendEvent) => void): void

  // Connection status
  status: 'connecting' | 'connected' | 'disconnected'
}
```

**Implementation note**: `createSession` opens a new WebSocket connection to the bridge (which spawns a new OH process). `switchSession` changes which WS connection's events are routed to the UI. `destroySession` closes the WS connection and the bridge kills the OH process.

### Message Flow

**Sending**: Vue calls `ztools.oh.sendPrompt(text)` → preload constructs `{"type":"submit_line","line":text}` → sends via active session's WebSocket → bridge writes to OH stdin.

**Receiving**: OH outputs `OHJSON:{"type":"assistant_delta",...}` → bridge strips prefix, forwards via WS → preload parses → triggers `onEvent` callback → Vue UI updates.

### Trigger Methods

- **Text command**: `plugin.json` registers `oh` keyword command. User types `oh <prompt>` → preload parses → calls `sendPrompt`
- **Keyboard shortcut**: `plugin.json` registers global hotkey → opens Vue UI panel directly

## Vue UI Components

### Layout

```
┌─────────────────────────────────────────────────┐
│ SessionList (collapsible) │    ChatPanel         │
│ ┌──────────────┐          │ ┌──────────────────┐ │
│ │ ● session-1  │          │ │ [system] Ready.  │ │
│ │   session-2  │          │ │ [user] prompt    │ │
│ │              │          │ │ [assistant] ...   │ │
│ │ + New        │          │ │ [tool] Read ...   │ │
│ └──────────────┘          │ └──────────────────┘ │
│                           │ ┌──────────────────┐ │
│                           │ │ > input... [Send] │ │
│                           │ └──────────────────┘ │
└─────────────────────────────────────────────────┘
```

### Complete Event Rendering Table

| Event Type | UI Rendering |
|------------|--------------|
| `ready` | Initialize state (model, cwd, commands, permission_mode). Hide loading spinner. |
| `state_snapshot` | Update status bar (model, permission mode, etc.) |
| `transcript_item` (role=assistant) | AI reply bubble, Markdown rendered |
| `transcript_item` (role=user) | User message bubble |
| `transcript_item` (role=system) | System message, muted style |
| `transcript_item` (role=tool) | Tool card with name + input params |
| `transcript_item` (role=tool_result) | Tool result, attached to tool card |
| `assistant_delta` | Stream-append to current AI bubble (typewriter effect) |
| `assistant_complete` | Finalize streaming text, add complete message to transcript |
| `tool_started` | Show collapsible card: tool name + input, spinner icon |
| `tool_completed` | Update card: checkmark or cross, expandable output |
| `line_complete` | Set `busy = false`, re-enable input |
| `modal_request` (kind=permission) | PermissionModal popup with Allow/Deny buttons |
| `modal_request` (kind=question) | QuestionModal popup with text input |
| `select_request` | Option list popup |
| `clear_transcript` | Clear all messages from chat panel |
| `tasks_snapshot` | Update task list if task panel visible |
| `todo_update` | Render todo list if panel visible |
| `error` | Red error banner at top of chat |
| `shutdown` | Show "Session ended" message, disable input |

### InputBar.vue

- Multi-line textarea + send button
- Enter to send, Shift+Enter for newline
- Disabled when `busy = true`, re-enabled on `line_complete`
- Shows model name and permission mode from `ready`/`state_snapshot` state

### SessionList.vue

- Each entry = one WS connection + one OH process
- "+ New" button → calls `createSession(cwd)` → preload opens new WS → bridge spawns new OH
- Click session → calls `switchSession(id)` → UI shows that session's messages
- "×" button → calls `destroySession(id)` → closes WS, bridge kills OH
- Highlights active session

### PermissionModal.vue

- Listens for `modal_request` events where `modal.kind === "permission"`
- Shows tool name, description, and parameter summary
- "Allow" button → `respondPermission(request_id, true)`
- "Deny" button → `respondPermission(request_id, false)`

### QuestionModal.vue

- Listens for `modal_request` events where `modal.kind === "question"`
- Shows question text + text input field
- "Submit" button → `respondQuestion(request_id, answer)`

### State Management

Vue 3 `reactive` (no Vuex/Pinia):

```typescript
const state = reactive({
  // Connection
  status: 'connecting' as 'connecting' | 'connected' | 'disconnected',
  busy: false,

  // Sessions (multi-process model)
  sessions: [] as Session[],
  activeSessionId: null as string | null,

  // Chat
  messages: [] as Message[],
  streamingText: '',
  activeToolCalls: new Map<string, ToolCall>(),

  // From ready/state_snapshot
  model: '',
  cwd: '',
  permissionMode: '',
  commands: [] as string[],
})
```

## Error Handling

- Bridge process crash → preload detects WS disconnect → UI shows reconnect button
- OH process crash → bridge sends error event → UI shows error + "Restart Session" button
- WebSocket connect timeout (10s) → UI shows "Unable to connect to bridge"
- Bridge logs to stderr → preload captures and makes available for debugging

## Security

- WebSocket bound to localhost only
- Bridge validates incoming WS messages: must have valid `type` field matching FrontendRequest schema
- No credentials stored in plugin; OH auth handled by OH's own config
- Each session is isolated (separate OH process)
