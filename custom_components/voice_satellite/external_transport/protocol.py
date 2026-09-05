"""Provider-neutral External Transport Protocol v1 helpers.

This module intentionally has no Home Assistant or provider dependency so its
message validation and event normalization can be tested in isolation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

PROTOCOL_VERSION: Final = 1
PCM16LE: Final = "pcm_s16le"
SAMPLE_RATE: Final = 16_000
CHANNELS: Final = 1


class ProtocolError(ValueError):
    """An external transport peer sent an invalid protocol message."""


@dataclass(frozen=True, slots=True)
class SessionStart:
    """Validated metadata sent before binary audio frames."""

    session_id: str
    satellite_entity_id: str
    satellite_name: str
    conversation_id: str | None = None
    wake_word: str | None = None

    def as_message(self) -> dict[str, Any]:
        return {
            "type": "session.start",
            "protocol_version": PROTOCOL_VERSION,
            "session_id": self.session_id,
            "satellite": {
                "entity_id": self.satellite_entity_id,
                "name": self.satellite_name,
            },
            "audio": {
                "encoding": PCM16LE,
                "sample_rate": SAMPLE_RATE,
                "channels": CHANNELS,
            },
            "conversation": {
                "id": self.conversation_id,
                "wake_word": self.wake_word,
            },
        }


def validate_server_message(message: object) -> dict[str, Any]:
    """Validate the common shape of a server JSON event."""
    if not isinstance(message, dict):
        raise ProtocolError("external transport message must be an object")
    event_type = message.get("type")
    if not isinstance(event_type, str) or not event_type:
        raise ProtocolError("external transport message requires a non-empty type")
    return message


def validate_ready(message: object, session_id: str) -> dict[str, Any]:
    """Validate the required readiness response for a session."""
    message = validate_server_message(message)
    if message["type"] != "session.ready":
        raise ProtocolError("expected session.ready")
    if message.get("session_id") != session_id:
        raise ProtocolError("session.ready has a mismatched session_id")
    return message


def normalize_event(message: object) -> dict[str, Any] | None:
    """Map protocol events onto existing Voice Satellite pipeline events.

    Returning ``None`` means the event is informational and does not need to
    reach the existing card pipeline state machine.
    """
    event = validate_server_message(message)
    event_type = event["type"]

    if event_type == "user.transcript.partial":
        # The existing UI consumes final STT events. Partial text remains an
        # optional protocol capability until a dedicated card event exists.
        return None
    if event_type == "user.transcript.final":
        return {"type": "stt-end", "data": {"stt_output": {"text": _text(event)}}}
    if event_type == "assistant.response_started":
        return {"type": "intent-start", "data": {}}
    if event_type == "assistant.text.delta":
        return {
            "type": "intent-progress",
            "data": {"chat_log_delta": {"content": _text(event)}},
        }
    if event_type == "assistant.text.final":
        return {
            "type": "intent-end",
            "data": {
                "intent_output": {
                    "response": {
                        "response_type": "action_done",
                        "speech": {"plain": {"speech": _text(event)}},
                    },
                    "conversation_id": event.get("conversation_id"),
                    "continue_conversation": bool(event.get("continue_conversation")),
                }
            },
        }
    if event_type == "assistant.interrupted":
        return {"type": "external-interrupted", "data": {}}
    if event_type == "assistant.audio":
        url = event.get("url")
        if not isinstance(url, str) or not url:
            raise ProtocolError("assistant.audio requires a URL")
        return {"type": "tts-end", "data": {"tts_output": {"url": url}}}
    if event_type == "session.finished":
        return {"type": "run-end", "data": {}}
    if event_type == "error":
        return {
            "type": "error",
            "data": {
                "code": event.get("code", "external_transport_error"),
                "message": event.get("message", "External transport failed"),
            },
        }
    # Speech lifecycle, audio completion, and interruption are handled by the
    # external session/client lifecycle or future card capabilities.
    return None


def _text(event: dict[str, Any]) -> str:
    text = event.get("text")
    if not isinstance(text, str):
        raise ProtocolError(f"{event['type']} requires text")
    return text
