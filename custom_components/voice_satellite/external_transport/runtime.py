"""Persistent Home Assistant-side actor for External Transport conversations."""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from typing import Any

from aiohttp import ClientSession

from .client import ExternalTransportClient
from .protocol import ProtocolError, SessionStart, normalize_event

_LOGGER = logging.getLogger(__name__)
_CLOSE_TIMEOUT = 3.0
# Follow-up inactivity is owned by the frontend because only it knows when
# native/browser playback has actually ended. Home Assistant receives the
# terminal queue marker when that bounded frontend timer expires.


@dataclass(slots=True)
class _Binding:
    generation: int
    connection: Any
    msg_id: int
    audio_queue: asyncio.Queue[bytes] | None
    done: asyncio.Future[None]
    terminal_reason: str | None = None


class ExternalConversationRuntime:
    """Own one provider WebSocket across independently subscribed card runs."""

    def __init__(self, *, http: ClientSession, url: str, token: str, verify_tls: bool, ready_timeout: float, session_id: str, satellite_entity_id: str, satellite_name: str) -> None:
        del verify_tls  # selected when Home Assistant creates ``http``
        self._http, self._url, self._token = http, url, token
        self._ready_timeout = ready_timeout
        self._session_id = session_id
        self._satellite_entity_id, self._satellite_name = satellite_entity_id, satellite_name
        self._client: ExternalTransportClient | None = None
        self._connect_lock = asyncio.Lock()
        self._lock = asyncio.Lock()
        self._binding: _Binding | None = None
        self._generation = 0
        self._turn_id: str | None = None
        self._turn_kind: str | None = None
        self._response_id: str | None = None
        self._response_turn_id: str | None = None
        self._event_task: asyncio.Task[None] | None = None
        self._audio_task: asyncio.Task[None] | None = None
        self._closed = False

    @property
    def closed(self) -> bool:
        """Whether a terminal provider/network failure requires replacement."""
        return self._closed

    async def attach_audio(self, audio_queue: asyncio.Queue[bytes], connection: Any, msg_id: int, *, conversation_id: str | None, wake_word: str | None) -> None:
        binding = await self._attach(connection, msg_id, audio_queue)
        try:
            await self._ensure_connected(conversation_id, wake_word)
            async with self._lock:
                await self._end_open_turn_locked()
                await self._start_turn_locked("audio")
                self._audio_task = asyncio.create_task(self._forward_audio(binding), name="voice_satellite.external_audio")
            await binding.done
            if binding.terminal_reason is not None:
                await self.close(binding.terminal_reason)
        except Exception as err:
            self._fail_binding(binding, err)
            await self.close("audio_failure")
        finally:
            await self.detach(binding.generation, "audio_run_finished")

    async def attach_text(self, text: str, connection: Any, msg_id: int, *, conversation_id: str | None) -> None:
        binding = await self._attach(connection, msg_id, None)
        try:
            await self._ensure_connected(conversation_id, None)
            async with self._lock:
                await self._end_open_turn_locked()
                turn_id = await self._start_turn_locked("text")
                assert self._client is not None
                await self._client.write_text(turn_id, text)
                await self._client.end_turn(turn_id)
                self._turn_id = self._turn_kind = None
            await binding.done
            if binding.terminal_reason is not None:
                await self.close(binding.terminal_reason)
        except Exception as err:
            self._fail_binding(binding, err)
            await self.close("text_failure")
        finally:
            await self.detach(binding.generation, "text_run_finished")

    async def detach(self, generation: int, reason: str) -> None:
        async with self._lock:
            if self._binding is None or self._binding.generation != generation:
                return
            binding = self._binding
            self._binding = None
            if self._audio_task is not None:
                self._audio_task.cancel()
                self._audio_task = None
            await self._end_open_turn_locked()
            if self._response_id and self._client is not None:
                try:
                    await asyncio.wait_for(self._client.cancel_response(self._response_id, reason), _CLOSE_TIMEOUT)
                except (ProtocolError, TimeoutError, Exception):
                    pass
                self._response_id = self._response_turn_id = None
            if not binding.done.done():
                binding.done.set_result(None)

    async def close(self, reason: str) -> None:
        if self._closed:
            return
        self._closed = True
        async with self._lock:
            binding = self._binding
            self._binding = None
            if self._audio_task:
                self._audio_task.cancel()
            if self._client:
                try:
                    await asyncio.wait_for(self._client.cancel_session(reason), _CLOSE_TIMEOUT)
                except (TimeoutError, Exception):
                    pass
        if self._event_task:
            self._event_task.cancel()
            await asyncio.gather(self._event_task, return_exceptions=True)
        if self._audio_task:
            await asyncio.gather(self._audio_task, return_exceptions=True)
        if self._client:
            await self._client.close()
        if binding and not binding.done.done():
            binding.done.set_result(None)

    async def _attach(self, connection: Any, msg_id: int, audio_queue: asyncio.Queue[bytes] | None) -> _Binding:
        loop = asyncio.get_running_loop()
        async with self._lock:
            if self._closed:
                raise ProtocolError("external conversation is closed")
            old = self._binding
            self._generation += 1
            binding = _Binding(self._generation, connection, msg_id, audio_queue, loop.create_future())
            self._binding = binding
            if old is not None:
                try:
                    old.connection.send_event(old.msg_id, {"type": "displaced"})
                except Exception:  # stale HA WebSocket
                    pass
                if not old.done.done():
                    old.done.set_result(None)
            connection.send_event(
                msg_id,
                {"type": "run-start", "data": {"external": {"persistent": True}}},
            )
            return binding

    async def _ensure_connected(self, conversation_id: str | None, wake_word: str | None) -> None:
        async with self._connect_lock:
            if self._client is not None:
                return
            client = ExternalTransportClient(self._http, self._url, self._token, SessionStart(self._session_id, self._satellite_entity_id, self._satellite_name, conversation_id, wake_word), self._ready_timeout)
            capabilities = await client.connect()
            if not (capabilities.transcription and capabilities.text_input and capabilities.streaming_audio_url and capabilities.interruptions and capabilities.conversation_continuation):
                await client.close()
                raise ProtocolError("external transport lacks required persistent-turn capabilities")
            self._client = client
            self._event_task = asyncio.create_task(self._read_events(), name="voice_satellite.external_events")

    async def _start_turn_locked(self, kind: str) -> str:
        assert self._client is not None
        turn_id = str(uuid.uuid4())
        await self._client.start_turn(turn_id, kind)
        self._turn_id, self._turn_kind = turn_id, kind
        return turn_id

    async def _end_open_turn_locked(self) -> None:
        if self._turn_id is not None and self._client is not None:
            await self._client.end_turn(self._turn_id)
        self._turn_id = self._turn_kind = None

    async def _forward_audio(self, binding: _Binding) -> None:
        try:
            while True:
                chunk = await binding.audio_queue.get()  # terminal marker is guaranteed by ws unsubscribe
                async with self._lock:
                    if self._binding is not binding:
                        return
                    if not chunk:
                        await self._end_open_turn_locked()
                        # The frontend only enqueues this marker when its
                        # subscription stops (including Kiosk stop), so this
                        # is a terminal client action rather than turn-end.
                        binding.terminal_reason = "client_stopped"
                        if not binding.done.done():
                            binding.done.set_result(None)
                        return
                    if self._turn_id is None or self._turn_kind != "audio":
                        # A text run displaced capture; never leak PCM across modalities.
                        return
                    assert self._client is not None
                    await self._client.write_audio(self._turn_id, chunk)
        except asyncio.CancelledError:
            raise
        except Exception as err:  # provider boundary
            self._fail_binding(binding, err)

    async def _read_events(self) -> None:
        assert self._client is not None
        try:
            async for event in self._client.events():
                async with self._lock:
                    await self._handle_event_locked(event)
                if event["type"] in {"session.finished", "error"}:
                    self._closed = True
                    return
        except asyncio.CancelledError:
            raise
        except Exception as err:
            _LOGGER.warning("External transport event stream failed: %s", err)
            binding = self._binding
            if binding:
                self._fail_binding(binding, err)
            self._closed = True

    async def _handle_event_locked(self, event: dict[str, Any]) -> None:
        event_type = event["type"]
        if event_type == "assistant.response_started":
            self._response_id, self._response_turn_id = event["response_id"], event["turn_id"]
            # The server permits provider VAD to finish an audio turn. Create
            # the next capture turn atomically before forwarding more PCM.
            if self._turn_kind == "audio":
                await self._end_open_turn_locked()
                if self._binding and self._binding.audio_queue is not None:
                    await self._start_turn_locked("audio")
        response_id = event.get("response_id")
        if response_id and event_type != "assistant.response_started" and response_id != self._response_id:
            return  # fenced late event from an interrupted/replaced response
        pipeline_event = normalize_event(event)
        binding = self._binding
        if pipeline_event is not None and binding is not None:
            try:
                binding.connection.send_event(binding.msg_id, pipeline_event)
            except Exception:
                pass
        if event_type in {"assistant.interrupted", "assistant.response_finished"}:
            if response_id == self._response_id:
                self._response_id = self._response_turn_id = None
            # Response completion is not a transport/binding completion. Keep
            # forwarding native PCM through the speculative audio turn so the
            # provider can detect a follow-up or a real barge-in.
        elif event_type in {"session.finished", "error"} and binding and not binding.done.done():
            binding.done.set_result(None)

    def _fail_binding(self, binding: _Binding, err: Exception) -> None:
        _LOGGER.warning("External transport failed for binding: %s", err)
        if self._binding is binding:
            try:
                binding.connection.send_event(binding.msg_id, {"type": "error", "data": {"code": "external_transport_error", "message": "External transport is unavailable"}})
            except Exception:
                pass
        if not binding.done.done():
            binding.done.set_result(None)
