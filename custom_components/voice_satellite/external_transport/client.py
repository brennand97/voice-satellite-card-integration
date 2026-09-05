"""Home Assistant-side client for External Transport Protocol v1."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from aiohttp import ClientSession, WSMsgType

from .protocol import ProtocolError, SessionStart, validate_ready, validate_server_message
from .session import ExternalSessionState, SessionState


class ExternalTransportClient:
    """One authenticated external transport session.

    The caller owns PCM buffering. ``connect`` waits for ``session.ready`` so
    callers can safely drain Kiosk Satellite pre-roll afterwards.
    """

    def __init__(
        self,
        session: ClientSession,
        url: str,
        token: str,
        start: SessionStart,
        ready_timeout: float,
    ) -> None:
        self._http = session
        self._url = url
        self._token = token
        self._start = start
        self._ready_timeout = ready_timeout
        self._ws = None
        self.state = ExternalSessionState()

    async def connect(self) -> None:
        self.state.transition(SessionState.CONNECTING)
        try:
            self._ws = await self._http.ws_connect(
                self._url,
                headers={"Authorization": f"Bearer {self._token}"},
                heartbeat=20,
            )
            await self._ws.send_json(self._start.as_message())
            raw = await asyncio.wait_for(self._ws.receive(), timeout=self._ready_timeout)
            message = self._decode_json(raw)
            validate_ready(message, self._start.session_id)
            self.state.transition(SessionState.READY)
            self.state.transition(SessionState.LISTENING)
        except Exception:
            if self.state.current not in (SessionState.FINISHED, SessionState.FAILED):
                self.state.transition(SessionState.FAILED)
            await self.close()
            raise

    async def write_audio(self, pcm: bytes) -> None:
        if self.state.current not in (SessionState.LISTENING, SessionState.RESPONDING):
            raise ProtocolError("cannot write audio before session is ready")
        if not pcm or len(pcm) % 2:
            raise ProtocolError("audio frames must be non-empty PCM16LE")
        await self._ws.send_bytes(pcm)

    async def end_input(self) -> None:
        if self._ws is not None and not self._ws.closed:
            await self._ws.send_json({"type": "input.end"})

    async def cancel(self, reason: str) -> None:
        if self.state.current not in (SessionState.FINISHED, SessionState.FAILED, SessionState.CANCELLING):
            self.state.transition(SessionState.CANCELLING)
        if self._ws is not None and not self._ws.closed:
            await self._ws.send_json({"type": "session.cancel", "reason": reason})

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        if self._ws is None:
            raise ProtocolError("session is not connected")
        async for raw in self._ws:
            message = self._decode_json(raw)
            event = validate_server_message(message)
            if event["type"] == "assistant.response_started" and self.state.current == SessionState.LISTENING:
                self.state.transition(SessionState.RESPONDING)
            elif event["type"] == "session.finished":
                if self.state.current not in (SessionState.FINISHED, SessionState.FAILED):
                    self.state.transition(SessionState.FINISHED)
            yield event

    async def close(self) -> None:
        if self._ws is not None and not self._ws.closed:
            await self._ws.close()
        if self.state.current not in (SessionState.FINISHED, SessionState.FAILED):
            self.state.transition(SessionState.FINISHED)

    @staticmethod
    def _decode_json(raw: Any) -> dict[str, Any]:
        if raw.type != WSMsgType.TEXT:
            raise ProtocolError("expected a JSON text frame from external transport")
        try:
            return raw.json()
        except Exception as err:
            raise ProtocolError("external transport returned invalid JSON") from err
