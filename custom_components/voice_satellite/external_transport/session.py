"""Small, provider-independent session state model."""

from __future__ import annotations

from enum import StrEnum


class SessionState(StrEnum):
    NEW = "new"
    CONNECTING = "connecting"
    READY = "ready"
    LISTENING = "listening"
    RESPONDING = "responding"
    CANCELLING = "cancelling"
    FINISHED = "finished"
    FAILED = "failed"


_ALLOWED: dict[SessionState, frozenset[SessionState]] = {
    SessionState.NEW: frozenset({SessionState.CONNECTING, SessionState.FAILED}),
    SessionState.CONNECTING: frozenset({SessionState.READY, SessionState.CANCELLING, SessionState.FAILED}),
    SessionState.READY: frozenset({SessionState.LISTENING, SessionState.CANCELLING, SessionState.FAILED}),
    SessionState.LISTENING: frozenset({SessionState.RESPONDING, SessionState.CANCELLING, SessionState.FINISHED, SessionState.FAILED}),
    SessionState.RESPONDING: frozenset({SessionState.LISTENING, SessionState.CANCELLING, SessionState.FINISHED, SessionState.FAILED}),
    SessionState.CANCELLING: frozenset({SessionState.FINISHED, SessionState.FAILED}),
    SessionState.FINISHED: frozenset(),
    SessionState.FAILED: frozenset(),
}


class InvalidStateTransition(RuntimeError):
    """A session attempted an unsafe or nonsensical state change."""


class ExternalSessionState:
    """Validate lifecycle changes without tying them to a WebSocket library."""

    def __init__(self) -> None:
        self.current = SessionState.NEW

    def transition(self, target: SessionState) -> None:
        if target not in _ALLOWED[self.current]:
            raise InvalidStateTransition(f"cannot transition {self.current} to {target}")
        self.current = target
