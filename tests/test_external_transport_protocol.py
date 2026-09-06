"""Unit tests for isolated External Transport modules.

The client test stubs aiohttp because Home Assistant supplies it at runtime;
these tests deliberately do not import Home Assistant or provider libraries.
"""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
import sys
import types
import unittest

ROOT = Path(__file__).parents[1]
PACKAGE = ROOT / "custom_components" / "voice_satellite" / "external_transport"
PACKAGE_NAME = "external_transport_test"

package = types.ModuleType(PACKAGE_NAME)
package.__path__ = [str(PACKAGE)]
sys.modules[PACKAGE_NAME] = package

# Minimal surface consumed by client.py. The real aiohttp package is supplied
# by Home Assistant; this keeps the unit test focused on our client behavior.
aiohttp = types.ModuleType("aiohttp")
aiohttp.ClientSession = object


class FakeWSMsgType:
    TEXT = "text"


aiohttp.WSMsgType = FakeWSMsgType
sys.modules["aiohttp"] = aiohttp


def load_module(name: str):
    qualified_name = f"{PACKAGE_NAME}.{name}"
    spec = importlib.util.spec_from_file_location(qualified_name, PACKAGE / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[qualified_name] = module
    spec.loader.exec_module(module)
    return module


protocol = load_module("protocol")
session = load_module("session")
client_module = load_module("client")
runtime_module = load_module("runtime")


class ProtocolTests(unittest.TestCase):
    def test_start_message_has_fixed_native_audio_format(self) -> None:
        message = protocol.SessionStart("id", "assist_satellite.kitchen", "Kitchen", "conversation", "Okay Nabu").as_message()
        self.assertEqual(message["protocol_version"], 1)
        self.assertEqual(message["audio"], {"encoding": "pcm_s16le", "sample_rate": 16000, "channels": 1})
        self.assertEqual(message["conversation"]["id"], "conversation")

    def test_generic_conversation_start_has_no_satellite_or_audio(self) -> None:
        message = protocol.SessionStart(
            "id", "conversation.reginold", "Reginold", "conversation", None,
            client_kind="ha_conversation", tool_profile="home-read-only",
            input_modalities=("text",), output_modalities=("text",),
        ).as_message()
        self.assertNotIn("satellite", message)
        self.assertEqual(message["client"]["kind"], "ha_conversation")
        self.assertIsNone(message["conversation"]["device_id"])
        self.assertEqual(message["conversation"]["output_modalities"], ["text"])

    def test_ready_requires_matching_session(self) -> None:
        with self.assertRaises(protocol.ProtocolError):
            protocol.validate_ready({"type": "session.ready", "session_id": "other"}, "id")

    def test_validates_persistent_event_correlation(self) -> None:
        event = protocol.validate_event(
            {"type": "assistant.audio", "session_id": "s1", "turn_id": "t1", "response_id": "r1", "url": "https://voice.example/audio", "content_type": "audio/wav"},
            "s1",
        )
        self.assertEqual(protocol.normalize_event(event)["type"], "external-audio-start")
        with self.assertRaises(protocol.ProtocolError):
            protocol.validate_event({"type": "assistant.interrupted", "session_id": "s1", "turn_id": "t1"}, "s1")

    def test_normalizes_transcript_and_response(self) -> None:
        stt = protocol.normalize_event({"type": "user.transcript.final", "text": "lights off"})
        self.assertEqual(stt, {"type": "stt-end", "data": {"stt_output": {"text": "lights off"}}})
        delta = protocol.normalize_event({"type": "assistant.text.delta", "text": "Done"})
        self.assertEqual(delta["data"]["chat_log_delta"]["content"], "Done")
        final = protocol.normalize_event({"type": "assistant.text.final", "text": "Done"})
        self.assertEqual(final["type"], "intent-end")

    def test_tool_events_show_only_the_name_and_preserve_unrendered_payloads(self) -> None:
        started = protocol.normalize_event(protocol.validate_event({
            "type": "assistant.tool_call_started", "session_id": "s1", "turn_id": "t1", "response_id": "r1",
            "tool_call_id": "call-1", "tool_name": "homeassistant__GetLiveContext", "arguments": {"area": "Kitchen"},
        }, "s1"))
        self.assertEqual(started["type"], "intent-progress")
        self.assertEqual(started["data"]["chat_log_delta"]["tool_calls"][0]["tool_name"], "homeassistant__GetLiveContext")
        self.assertEqual(started["data"]["external"]["arguments"], {"area": "Kitchen"})
        self.assertEqual(started["data"]["external"]["tool_call_id"], "call-1")
        finished = protocol.normalize_event(protocol.validate_event({
            "type": "assistant.tool_call_finished", "session_id": "s1", "turn_id": "t1", "response_id": "r1",
            "tool_call_id": "call-1", "tool_name": "homeassistant__GetLiveContext", "arguments": {},
            "result": [{"type": "text", "text": "private result"}], "is_error": False,
        }, "s1"))
        self.assertEqual(finished["type"], "external-tool-finished")
        self.assertEqual(finished["data"]["external"]["result"][0]["text"], "private result")

    def test_interruption_maps_to_a_provider_neutral_card_event(self) -> None:
        event = protocol.normalize_event({"type": "assistant.interrupted", "audio_id": "a1"})
        self.assertEqual(event, {"type": "external-interrupted", "data": {}})

    def test_response_finish_keeps_persistent_capture_open(self) -> None:
        event = protocol.normalize_event(
            {"type": "assistant.response_finished", "turn_id": "t1", "response_id": "r1"}
        )
        self.assertEqual(event["type"], "external-response-finished")
        self.assertNotEqual(event["type"], "run-end")

    def test_external_speech_arms_a_distinct_watchdog_event(self) -> None:
        event = protocol.normalize_event({"type": "user.speech_started", "turn_id": "t1"})
        self.assertEqual(event["type"], "external-vad-start")

    def test_audio_requires_url(self) -> None:
        with self.assertRaises(protocol.ProtocolError):
            protocol.normalize_event({"type": "assistant.audio"})

    def test_protocol_error_maps_to_card_error(self) -> None:
        event = protocol.normalize_event({"type": "error", "code": "bad_gateway", "message": "Unavailable"})
        self.assertEqual(event["type"], "error")
        self.assertEqual(event["data"]["code"], "bad_gateway")


class SessionStateTests(unittest.TestCase):
    def test_happy_path(self) -> None:
        state = session.ExternalSessionState()
        for target in (session.SessionState.CONNECTING, session.SessionState.READY, session.SessionState.LISTENING, session.SessionState.RESPONDING, session.SessionState.FINISHED):
            state.transition(target)
        self.assertEqual(state.current, session.SessionState.FINISHED)

    def test_invalid_transition_is_rejected(self) -> None:
        state = session.ExternalSessionState()
        with self.assertRaises(session.InvalidStateTransition):
            state.transition(session.SessionState.RESPONDING)


class FakeMessage:
    def __init__(self, payload: dict[str, object]) -> None:
        self.type = FakeWSMsgType.TEXT
        self._payload = payload

    def json(self) -> dict[str, object]:
        return self._payload


class FakeWebSocket:
    def __init__(self, messages: list[FakeMessage]) -> None:
        self.messages = messages
        self.sent_json: list[dict[str, object]] = []
        self.sent_bytes: list[bytes] = []
        self.closed = False

    async def send_json(self, message: dict[str, object]) -> None:
        self.sent_json.append(message)

    async def send_bytes(self, payload: bytes) -> None:
        self.sent_bytes.append(payload)

    async def receive(self) -> FakeMessage:
        return self.messages.pop(0)

    def __aiter__(self):
        return self

    async def __anext__(self) -> FakeMessage:
        if not self.messages:
            raise StopAsyncIteration
        return self.messages.pop(0)

    async def close(self) -> None:
        self.closed = True


class FakeHttpSession:
    def __init__(self, websocket: FakeWebSocket) -> None:
        self.websocket = websocket
        self.url = None
        self.headers = None

    async def ws_connect(self, url: str, *, headers: dict[str, str], heartbeat: int) -> FakeWebSocket:
        self.url = url
        self.headers = headers
        self.heartbeat = heartbeat
        return self.websocket


class FakeRuntimeConnection:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    def send_event(self, _msg_id: int, event: dict[str, object]) -> None:
        self.events.append(event)


class FakeRuntimeClient:
    instances: list["FakeRuntimeClient"] = []

    def __init__(self, *_args) -> None:
        self.events_queue: asyncio.Queue[dict[str, object] | None] = asyncio.Queue()
        self.started: list[tuple[str, str]] = []
        self.ended: list[str] = []
        self.cancelled: list[str] = []
        self.closed = False
        self.__class__.instances.append(self)

    async def connect(self):
        return protocol.ServerCapabilities(True, True, True, True, True)

    async def start_turn(self, turn_id: str, kind: str) -> None:
        self.started.append((turn_id, kind))

    async def write_audio(self, _turn_id: str, _pcm: bytes) -> None:
        return None

    async def write_text(self, _turn_id: str, _text: str) -> None:
        return None

    async def end_turn(self, turn_id: str) -> None:
        self.ended.append(turn_id)

    async def cancel_response(self, _response_id: str, _reason: str) -> None:
        return None

    async def cancel_session(self, reason: str) -> None:
        self.cancelled.append(reason)

    async def close(self) -> None:
        self.closed = True

    async def events(self):
        while event := await self.events_queue.get():
            yield event


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_speech_after_response_finish_keeps_capture_and_forwards_vad(self) -> None:
        original_client = runtime_module.ExternalTransportClient
        runtime_module.ExternalTransportClient = FakeRuntimeClient
        FakeRuntimeClient.instances.clear()
        try:
            connection = FakeRuntimeConnection()
            queue: asyncio.Queue[bytes] = asyncio.Queue()
            runtime = runtime_module.ExternalConversationRuntime(
                http=object(), url="wss://voice.example/transport/v1", token="test",
                verify_tls=True, ready_timeout=1, session_id="s1",
                satellite_entity_id="assist_satellite.kitchen", satellite_name="Kitchen",
            )
            task = asyncio.create_task(
                runtime.attach_audio(queue, connection, 1, conversation_id=None, wake_word=None)
            )
            while not FakeRuntimeClient.instances:
                await asyncio.sleep(0)
            client = FakeRuntimeClient.instances[0]
            await client.events_queue.put({"type": "assistant.response_started", "turn_id": "t1", "response_id": "r1"})
            await client.events_queue.put({"type": "assistant.response_finished", "turn_id": "t1", "response_id": "r1"})
            await client.events_queue.put({"type": "user.speech_started", "turn_id": "t2"})
            await asyncio.sleep(0)
            self.assertFalse(task.done())
            self.assertIn({"type": "external-vad-start", "data": {"external": {"turn_id": "t2"}}}, connection.events)
            await queue.put(b"")
            await asyncio.wait_for(task, 1)
        finally:
            runtime_module.ExternalTransportClient = original_client

    async def test_tool_lifecycle_after_preamble_finish_is_not_response_fenced(self) -> None:
        original_client = runtime_module.ExternalTransportClient
        runtime_module.ExternalTransportClient = FakeRuntimeClient
        FakeRuntimeClient.instances.clear()
        try:
            connection = FakeRuntimeConnection()
            runtime = runtime_module.ExternalConversationRuntime(
                http=object(), url="wss://voice.example/transport/v1", token="test",
                verify_tls=True, ready_timeout=1, session_id="s1",
                satellite_entity_id="assist_satellite.kitchen", satellite_name="Kitchen",
            )
            task = asyncio.create_task(
                runtime.attach_text("check home", connection, 1, conversation_id=None)
            )
            while not FakeRuntimeClient.instances:
                await asyncio.sleep(0)
            client = FakeRuntimeClient.instances[0]
            await client.events_queue.put({"type": "assistant.response_started", "turn_id": "t1", "response_id": "r1"})
            await client.events_queue.put({"type": "assistant.response_finished", "turn_id": "t1", "response_id": "r1"})
            await client.events_queue.put({"type": "assistant.tool_call_started", "turn_id": "t1", "response_id": "r1", "tool_call_id": "call-1", "tool_name": "home_context", "arguments": {}})
            await client.events_queue.put({"type": "assistant.tool_call_finished", "turn_id": "t1", "response_id": "r1", "tool_call_id": "call-1", "tool_name": "home_context", "arguments": {}, "result": [], "is_error": False})
            await asyncio.sleep(0)
            events = [event["type"] for event in connection.events]
            self.assertIn("intent-progress", events)
            self.assertIn("external-tool-finished", events)
            await runtime.close("test_finished")
            await asyncio.wait_for(task, 1)
        finally:
            runtime_module.ExternalTransportClient = original_client

    async def test_response_finish_does_not_start_ha_idle_timer(self) -> None:
        original_client = runtime_module.ExternalTransportClient
        runtime_module.ExternalTransportClient = FakeRuntimeClient
        FakeRuntimeClient.instances.clear()
        try:
            connection = FakeRuntimeConnection()
            queue: asyncio.Queue[bytes] = asyncio.Queue()
            runtime = runtime_module.ExternalConversationRuntime(
                http=object(), url="wss://voice.example/transport/v1", token="test",
                verify_tls=True, ready_timeout=1, session_id="s1",
                satellite_entity_id="assist_satellite.kitchen", satellite_name="Kitchen",
            )
            task = asyncio.create_task(
                runtime.attach_audio(queue, connection, 1, conversation_id=None, wake_word=None)
            )
            while not FakeRuntimeClient.instances:
                await asyncio.sleep(0)
            client = FakeRuntimeClient.instances[0]
            await client.events_queue.put({"type": "assistant.response_started", "turn_id": "t1", "response_id": "r1"})
            await client.events_queue.put({"type": "assistant.response_finished", "turn_id": "t1", "response_id": "r1"})
            await asyncio.sleep(0)
            self.assertFalse(task.done())
            self.assertEqual(len(client.started), 2)  # initial + speculative turn
            # Playback completion is only known by the frontend, which sends
            # the terminal queue marker after its follow-up timeout.
            await asyncio.sleep(0.03)
            self.assertFalse(task.done())
            await queue.put(b"")
            await asyncio.wait_for(task, 1)
            self.assertIn("client_stopped", client.cancelled)
        finally:
            runtime_module.ExternalTransportClient = original_client


class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_connect_forwards_audio_events_and_closes(self) -> None:
        websocket = FakeWebSocket([
            FakeMessage({"type": "session.ready", "session_id": "s1", "capabilities": {"transcription": True, "text_input": True, "streaming_audio_url": True, "interruptions": True, "conversation_continuation": True}}),
            FakeMessage({"type": "assistant.response_started", "session_id": "s1", "turn_id": "t1", "response_id": "r1"}),
            FakeMessage({"type": "session.finished", "session_id": "s1"}),
        ])
        http = FakeHttpSession(websocket)
        client = client_module.ExternalTransportClient(
            http,
            "wss://voice.example/transport/v1",
            "secret",
            protocol.SessionStart("s1", "assist_satellite.kitchen", "Kitchen"),
            1,
        )

        await client.connect()
        await client.start_turn("t1", "audio")
        await client.write_audio("t1", b"\x00\x00")
        await client.end_turn("t1")
        events = [event async for event in client.events()]
        await client.close()

        self.assertEqual(http.headers, {"Authorization": "Bearer secret"})
        self.assertEqual(websocket.sent_json[0]["type"], "session.start")
        self.assertEqual(websocket.sent_bytes, [b"\x00\x00"])
        self.assertEqual(websocket.sent_json[-1], {"type": "turn.end", "turn_id": "t1"})
        self.assertEqual([event["type"] for event in events], ["assistant.response_started", "session.finished"])
        self.assertTrue(websocket.closed)
        self.assertEqual(client.state.current, session.SessionState.FINISHED)

    async def test_text_turn_and_response_cancel_keep_session_open(self) -> None:
        websocket = FakeWebSocket([
            FakeMessage({"type": "session.ready", "session_id": "s1", "capabilities": {"transcription": True, "text_input": True, "streaming_audio_url": True, "interruptions": True, "conversation_continuation": True}}),
        ])
        client = client_module.ExternalTransportClient(FakeHttpSession(websocket), "wss://voice.example/transport/v1", "secret", protocol.SessionStart("s1", "assist_satellite.kitchen", "Kitchen"), 1)
        await client.connect()
        await client.start_turn("text-1", "text")
        await client.write_text("text-1", "Turn on lights")
        await client.end_turn("text-1")
        await client.cancel_response("r1", "playback_stopped")
        self.assertEqual([message["type"] for message in websocket.sent_json[1:]], ["turn.start", "input.text", "turn.end", "response.cancel"])
        self.assertFalse(websocket.closed)


if __name__ == "__main__":
    unittest.main()
