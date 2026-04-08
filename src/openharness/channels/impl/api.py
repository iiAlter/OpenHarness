"""API channel: HTTP + WebSocket server for external application integration."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from openharness.channels.bus.events import OutboundMessage
from openharness.channels.bus.queue import MessageBus
from openharness.channels.impl.base import BaseChannel

logger = logging.getLogger(__name__)


class SendMessageRequest(BaseModel):
    client_id: str
    content: str
    session_id: str | None = None


class APIChannel(BaseChannel):
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

            sessions = list_session_snapshots(
                client_id,
            )
            return {"sessions": sessions}

        @app.post("/api/v1/send")
        async def send_message(req: SendMessageRequest):
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
            token = ws.query_params.get("token", "")
            if token != self.config.api_token:
                await ws.close(code=4001, reason="Unauthorized")
                return

            await ws.accept()
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
                        data = await asyncio.wait_for(
                            ws.receive_json(), timeout=30
                        )
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
                            await ws.send_json({"type": "ack", "msg_id": msg_id})
                    except asyncio.TimeoutError:
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

    async def start(self) -> None:
        if not self.config.api_token:
            logger.error("API channel: api_token not configured")
            return
        self._running = True

        # Auth middleware
        async def auth_middleware(request: Request, call_next):
            if request.url.path == "/api/v1/health":
                return await call_next(request)
            if request.url.path.startswith("/api/v1/ws"):
                return await call_next(request)
            auth = request.headers.get("Authorization", "")
            if auth != f"Bearer {self.config.api_token}":
                return JSONResponse(
                    status_code=401, content={"detail": "Unauthorized"}
                )
            return await call_next(request)

        self._app.add_middleware(BaseHTTPMiddleware, dispatch=auth_middleware)

        config = uvicorn.Config(
            self._app,
            host=self.config.host,
            port=self.config.port,
            log_level="warning",
        )
        self._server = uvicorn.Server(config)
        logger.info(
            "API channel starting on %s:%s", self.config.host, self.config.port
        )
        asyncio.create_task(self._server.serve())
        await asyncio.sleep(0.5)

    async def stop(self) -> None:
        self._running = False
        if self._server:
            self._server.should_exit = True
            await self._server.shutdown()
        for ws in list(self._ws_map.values()):
            try:
                await ws.close()
            except Exception:
                pass
        self._ws_map.clear()
        logger.info("API channel stopped")

    async def send(self, msg: OutboundMessage) -> None:
        ws = self._ws_map.get(msg.chat_id)
        if not ws:
            logger.warning(
                "No WebSocket for %s, dropping message", msg.chat_id
            )
            return

        msg_id = msg.metadata.get("_msg_id", "")
        session_key = msg.metadata.get("_session_key", "")
        is_streaming = msg.metadata.get("_streaming", False)
        is_final = msg.metadata.get("_streaming_final", False)
        is_error = msg.metadata.get("_error", False)

        try:
            if is_error:
                await ws.send_json(
                    {
                        "type": "error",
                        "msg_id": msg_id,
                        "message": msg.content,
                    }
                )
            elif is_streaming and not is_final:
                await ws.send_json(
                    {
                        "type": "reply_delta",
                        "msg_id": msg_id,
                        "session_id": session_key,
                        "content": msg.content,
                    }
                )
            else:
                await ws.send_json(
                    {
                        "type": "reply",
                        "msg_id": msg_id,
                        "session_id": session_key,
                        "content": msg.content,
                    }
                )
        except Exception:
            logger.warning(
                "WebSocket send failed for %s, removing connection",
                msg.chat_id,
            )
            self._ws_map.pop(msg.chat_id, None)
