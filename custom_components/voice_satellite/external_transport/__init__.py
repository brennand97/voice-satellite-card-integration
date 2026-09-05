"""Provider-neutral external conversation transport support."""

from .protocol import PROTOCOL_VERSION, ProtocolError, SessionStart

__all__ = ["PROTOCOL_VERSION", "ProtocolError", "SessionStart"]
