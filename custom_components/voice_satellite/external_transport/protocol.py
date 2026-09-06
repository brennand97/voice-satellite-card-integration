"""Provider-neutral External Transport Protocol v1 helpers.

No Home Assistant/provider imports belong here: this is the strict wire-format
boundary shared by the low-level client and the persistent runtime.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final
from urllib.parse import urlparse

PROTOCOL_VERSION: Final = 1
PCM16LE: Final = "pcm_s16le"
SAMPLE_RATE: Final = 16_000
CHANNELS: Final = 1


class ProtocolError(ValueError):
    """An external transport peer sent an invalid protocol message."""


@dataclass(frozen=True, slots=True)
class SessionStart:
    session_id: str
    satellite_entity_id: str
    satellite_name: str
    conversation_id: str | None = None
    wake_word: str | None = None
    client_kind: str = "satellite"
    tool_profile: str | None = None
    requested_tools: tuple[str, ...] | None = None
    initial_prompt: str | None = None
    initial_voice: str | None = None
    device_id: str | None = None
    input_modalities: tuple[str, ...] = ("audio", "text")
    output_modalities: tuple[str, ...] = ("audio", "text")

    def as_message(self) -> dict[str, Any]:
        message: dict[str, Any] = {
            "type": "session.start", "protocol_version": PROTOCOL_VERSION,
            "session_id": self.session_id,
            "audio": {"encoding": PCM16LE, "sample_rate": SAMPLE_RATE, "channels": CHANNELS},
            "conversation": {
                "id": self.conversation_id, "wake_word": self.wake_word,
                "profile": self.tool_profile, "requested_tools": list(self.requested_tools) if self.requested_tools else None,
                "initial_prompt": self.initial_prompt, "initial_voice": self.initial_voice,
                "device_id": self.device_id, "input_modalities": list(self.input_modalities),
                "output_modalities": list(self.output_modalities),
            },
        }
        if self.client_kind == "satellite":
            message["satellite"] = {"entity_id": self.satellite_entity_id, "name": self.satellite_name}
        else:
            message["client"] = {"id": self.satellite_entity_id, "kind": self.client_kind}
        return message


@dataclass(frozen=True, slots=True)
class ServerCapabilities:
    transcription: bool
    text_input: bool
    streaming_audio_url: bool
    interruptions: bool
    conversation_continuation: bool


def validate_server_message(message: object) -> dict[str, Any]:
    if not isinstance(message, dict):
        raise ProtocolError("external transport message must be an object")
    event_type = message.get("type")
    if not isinstance(event_type, str) or not event_type:
        raise ProtocolError("external transport message requires a non-empty type")
    return message


def validate_ready(message: object, session_id: str) -> ServerCapabilities:
    message = validate_server_message(message)
    if message["type"] != "session.ready":
        raise ProtocolError("expected session.ready")
    if message.get("session_id") != session_id:
        raise ProtocolError("session.ready has a mismatched session_id")
    raw = message.get("capabilities")
    if not isinstance(raw, dict):
        raise ProtocolError("session.ready requires capabilities")
    required = (
        "transcription", "text_input", "streaming_audio_url",
        "interruptions", "conversation_continuation",
    )
    if any(not isinstance(raw.get(key), bool) for key in required):
        raise ProtocolError("session.ready has invalid capabilities")
    return ServerCapabilities(**{key: raw[key] for key in required})


def validate_event(message: object, session_id: str) -> dict[str, Any]:
    """Validate event correlation before it can affect a card binding."""
    event = validate_server_message(message)
    if event["type"] != "error" and event.get("session_id") != session_id:
        raise ProtocolError("external transport event has a mismatched session_id")
    correlated = {
        "user.speech_started", "user.transcript.partial", "user.transcript.final",
        "assistant.response_started", "assistant.text.delta", "assistant.text.final",
        "assistant.audio", "assistant.interrupted", "assistant.response_finished",
        "assistant.tool_call_started", "assistant.tool_call_finished",
    }
    if event["type"] in correlated:
        _required(event, "turn_id")
    if event["type"].startswith("assistant."):
        _required(event, "response_id")
    if event["type"].startswith("user.transcript"):
        _required(event, "text")
        if event.get("source") not in {"provider_audio", "client_text"}:
            raise ProtocolError("transcript has an invalid source")
    if event["type"] in {"assistant.tool_call_started", "assistant.tool_call_finished"}:
        _required(event, "tool_call_id")
        _required(event, "tool_name")
        if not isinstance(event.get("arguments"), dict):
            raise ProtocolError(f"{event['type']} requires object arguments")
        if event.get("arguments_truncated", False) is not False and not isinstance(event.get("arguments_truncated"), bool):
            raise ProtocolError("tool arguments_truncated must be boolean")
        if event["type"] == "assistant.tool_call_finished":
            if not isinstance(event.get("result"), list):
                raise ProtocolError("assistant.tool_call_finished requires list result")
            if event.get("result_truncated", False) is not False and not isinstance(event.get("result_truncated"), bool):
                raise ProtocolError("tool result_truncated must be boolean")
            if not isinstance(event.get("is_error"), bool):
                raise ProtocolError("assistant.tool_call_finished requires boolean is_error")
    if event["type"] == "assistant.audio":
        url = _required(event, "url")
        if urlparse(url).scheme not in {"https", "http"}:
            raise ProtocolError("assistant.audio URL must be HTTP(S)")
        if event.get("content_type") != "audio/wav":
            raise ProtocolError("assistant.audio must be audio/wav")
    return event


def normalize_event(event: dict[str, Any]) -> dict[str, Any] | None:
    """Map validated protocol events onto existing card pipeline events."""
    event_type = event["type"]
    meta = {key: event[key] for key in ("turn_id", "response_id", "tool_call_id") if key in event}
    if event_type == "user.speech_started":
        # Keep this separate from HA's stt-vad-start: the external provider
        # has no preceding HA stt-start event to arm the stuck-turn watchdog.
        return {"type": "external-vad-start", "data": {"external": meta}}
    if event_type == "user.transcript.partial":
        return None
    if event_type == "user.transcript.final":
        source = event.get("source")
        external = {**meta, **({"source": source} if isinstance(source, str) else {})}
        data: dict[str, Any] = {"stt_output": {"text": _required(event, "text")}}
        if external:
            data["external"] = external
        return {"type": "stt-end", "data": data}
    if event_type == "assistant.response_started":
        return {"type": "intent-start", "data": {"external": meta}}
    if event_type == "assistant.text.delta":
        return {"type": "intent-progress", "data": {"chat_log_delta": {"content": _required(event, "text")}, "external": meta}}
    if event_type == "assistant.text.final":
        return {"type": "intent-end", "data": {"intent_output": {"response": {"response_type": "action_done", "speech": {"plain": {"speech": _required(event, "text")}}}}, "external": meta}}
    if event_type == "assistant.tool_call_started":
        # Reuse the existing tool-call indicator. Arguments remain in external
        # metadata for future consumers but are deliberately not rendered.
        external = {**meta, "arguments": event["arguments"], "arguments_truncated": event.get("arguments_truncated", False)}
        return {"type": "intent-progress", "data": {"chat_log_delta": {"tool_calls": [{"tool_name": _required(event, "tool_name")}]}, "external": external}}
    if event_type == "assistant.tool_call_finished":
        # Preserve provider-neutral result metadata without routing it through
        # chat_log_delta.tool_result, whose existing handlers may render it.
        external = {**meta, "tool_name": _required(event, "tool_name"), "arguments": event["arguments"], "result": event["result"], "arguments_truncated": event.get("arguments_truncated", False), "result_truncated": event.get("result_truncated", False), "is_error": event["is_error"]}
        return {"type": "external-tool-finished", "data": {"external": external}}
    if event_type == "assistant.audio":
        return {"type": "external-audio-start", "data": {"url": _required(event, "url"), "content_type": _required(event, "content_type"), "external": meta}}
    if event_type == "assistant.interrupted":
        return {"type": "external-interrupted", "data": {"external": meta} if meta else {}}
    if event_type == "assistant.response_finished":
        # A persistent external conversation remains capture-ready after the
        # provider finishes generation. ``run-end`` would unsubscribe the
        # native Kiosk PCM sender and make barge-in/follow-up impossible.
        return {"type": "external-response-finished", "data": {"external": meta}}
    if event_type == "session.finished":
        return {"type": "run-end", "data": {}}
    if event_type == "error":
        return {"type": "error", "data": {"code": event.get("code", "external_transport_error"), "message": event.get("message", "External transport failed")}}
    return None


def _required(message: dict[str, Any], key: str) -> str:
    value = message.get(key)
    if not isinstance(value, str) or not value:
        raise ProtocolError(f"{message['type']} requires {key}")
    return value
