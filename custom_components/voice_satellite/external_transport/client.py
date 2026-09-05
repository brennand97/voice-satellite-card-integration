"""Low-level persistent client for External Transport Protocol v1."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from aiohttp import ClientSession, WSMsgType

from .protocol import (
    ProtocolError,
    ServerCapabilities,
    SessionStart,
    validate_event,
    validate_ready,
)
from .session import ExternalSessionState, SessionState


class ExternalTransportClient:
    """One authenticated, multi-turn external transport session.

    This class deliberately owns no frontend binding or audio queue. One runtime
    is its sole event consumer and serializes turn ownership above this layer.
    """

    def __init__(self, session: ClientSession, url: str, token: str, start: SessionStart, ready_timeout: float) -> None:
        self._http, self._url, self._token = session, url, token
        self._start, self._ready_timeout = start, ready_timeout
        self._ws: Any = None
        self._send_lock = asyncio.Lock()
        self._open_turn: tuple[str, str] | None = None
        self._used_turns: set[str] = set()
        self.capabilities: ServerCapabilities | None = None
        self.state = ExternalSessionState()

    @property
    def open_turn_id(self) -> str | None:
        return self._open_turn[0] if self._open_turn else None

    async def connect(self) -> ServerCapabilities:
        self.state.transition(SessionState.CONNECTING)
        try:
            self._ws = await self._http.ws_connect(self._url, headers={"Authorization": f"Bearer {self._token}"}, heartbeat=20)
            await self._ws.send_json(self._start.as_message())
            raw = await asyncio.wait_for(self._ws.receive(), timeout=self._ready_timeout)
            self.capabilities = validate_ready(self._decode_json(raw), self._start.session_id)
            self.state.transition(SessionState.READY)
            self.state.transition(SessionState.LISTENING)
            return self.capabilities
        except Exception:
            if self.state.current not in (SessionState.FINISHED, SessionState.FAILED):
                self.state.transition(SessionState.FAILED)
            await self.close()
            raise

    async def start_turn(self, turn_id: str, input_type: str) -> None:
        if input_type not in {"audio", "text"} or not turn_id:
            raise ProtocolError("turn requires a non-empty ID and audio or text input")
        async with self._send_lock:
            self._ensure_ready()
            if turn_id in self._used_turns or self._open_turn is not None:
                raise ProtocolError("another turn is already open")
            await self._send_json({"type": "turn.start", "turn_id": turn_id, "input": input_type})
            self._used_turns.add(turn_id)
            self._open_turn = (turn_id, input_type)

    async def write_audio(self, turn_id: str, pcm: bytes) -> None:
        if not pcm or len(pcm) % 2:
            raise ProtocolError("audio frames must be non-empty PCM16LE")
        async with self._send_lock:
            self._require_turn(turn_id, "audio")
            await self._ws.send_bytes(pcm)

    async def write_text(self, turn_id: str, text: str) -> None:
        if not text or len(text.encode()) > 4_000:
            raise ProtocolError("text must be between 1 and 4,000 bytes")
        async with self._send_lock:
            self._require_turn(turn_id, "text")
            await self._send_json({"type": "input.text", "turn_id": turn_id, "text": text})

    async def end_turn(self, turn_id: str) -> None:
        async with self._send_lock:
            self._require_turn(turn_id, None)
            await self._send_json({"type": "turn.end", "turn_id": turn_id})
            self._open_turn = None

    async def cancel_response(self, response_id: str, reason: str) -> None:
        async with self._send_lock:
            self._ensure_ready()
            await self._send_json({"type": "response.cancel", "response_id": response_id, "reason": reason})

    async def cancel_session(self, reason: str) -> None:
        async with self._send_lock:
            if self._ws is not None and not self._ws.closed:
                if self.state.current not in (SessionState.FINISHED, SessionState.FAILED, SessionState.CANCELLING):
                    self.state.transition(SessionState.CANCELLING)
                await self._send_json({"type": "session.cancel", "reason": reason})

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        if self._ws is None:
            raise ProtocolError("session is not connected")
        async for raw in self._ws:
            event = validate_event(self._decode_json(raw), self._start.session_id)
            if event["type"] == "assistant.response_started" and self.state.current == SessionState.LISTENING:
                self.state.transition(SessionState.RESPONDING)
            elif event["type"] in {"assistant.response_finished", "assistant.interrupted"} and self.state.current == SessionState.RESPONDING:
                self.state.transition(SessionState.LISTENING)
            elif event["type"] == "session.finished" and self.state.current not in (SessionState.FINISHED, SessionState.FAILED):
                self.state.transition(SessionState.FINISHED)
            yield event

    async def close(self) -> None:
        if self._ws is not None and not self._ws.closed:
            try:
                await asyncio.wait_for(self._ws.close(), timeout=3)
            except TimeoutError:
                pass
        if self.state.current not in (SessionState.FINISHED, SessionState.FAILED):
            self.state.transition(SessionState.FINISHED)

    async def _send_json(self, message: dict[str, Any]) -> None:
        if self._ws is None or self._ws.closed:
            raise ProtocolError("session is closed")
        await self._ws.send_json(message)

    def _ensure_ready(self) -> None:
        if self.state.current not in (SessionState.LISTENING, SessionState.RESPONDING):
            raise ProtocolError("session is not ready")

    def _require_turn(self, turn_id: str, input_type: str | None) -> None:
        self._ensure_ready()
        if self._open_turn is None or self._open_turn[0] != turn_id:
            raise ProtocolError("turn is not active")
        if input_type is not None and self._open_turn[1] != input_type:
            raise ProtocolError("turn input type does not match")

    @staticmethod
    def _decode_json(raw: Any) -> dict[str, Any]:
        if raw.type != WSMsgType.TEXT:
            raise ProtocolError("expected a JSON text frame from external transport")
        try:
            return raw.json()
        except Exception as err:
            raise ProtocolError("external transport returned invalid JSON") from err
