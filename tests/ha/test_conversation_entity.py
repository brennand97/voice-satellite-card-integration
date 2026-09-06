"""External Conversation entity session-policy and lifecycle tests."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from homeassistant.components import conversation

from custom_components.voice_satellite.const import (
    CONF_EXTERNAL_TRANSPORT_TOKEN,
    CONF_EXTERNAL_TRANSPORT_URL,
    CONF_REQUESTED_TOOLS,
    CONF_TOOL_PROFILE,
)
from custom_components.voice_satellite.conversation import (
    ExternalTransportConversationEntity,
)

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


class FakeClient:
    """Deterministic client stand-in; no network or provider calls."""

    def __init__(self, capabilities, events=()):
        self.capabilities = capabilities
        self._events = events
        self.calls = []

    async def connect(self):
        self.calls.append("connect")
        return self.capabilities

    async def start_turn(self, turn_id, modality):
        self.calls.append(("start_turn", modality))

    async def write_text(self, turn_id, text):
        self.calls.append(("write_text", text))

    async def end_turn(self, turn_id):
        self.calls.append(("end_turn", turn_id))

    async def cancel_session(self, reason):
        self.calls.append(("cancel_session", reason))

    async def close(self):
        self.calls.append("close")

    async def events(self):
        for event in self._events:
            yield event


def _entity(hass, *, requested_tools=None):
    entry = SimpleNamespace(
        entry_id="service-entry",
        data={
            CONF_EXTERNAL_TRANSPORT_URL: "wss://voice.example/transport/v1",
            CONF_EXTERNAL_TRANSPORT_TOKEN: "test-token",
        },
    )
    subentry_data = {CONF_TOOL_PROFILE: "home-generic"}
    if requested_tools is not None:
        subentry_data[CONF_REQUESTED_TOOLS] = requested_tools
    subentry = SimpleNamespace(
        subentry_id="profile-entry", title="Home", data=subentry_data
    )
    entity = ExternalTransportConversationEntity(entry, subentry)
    entity.hass = hass
    entity.entity_id = "conversation.external_home"
    return entity


async def test_text_conversation_is_text_only_and_uses_exact_requested_tools(hass) -> None:
    client = FakeClient(
        SimpleNamespace(
            effective_profile="home-generic",
            effective_tools=("homeassistant__GetLiveContext",),
        ),
        events=(
            {"type": "assistant.text.delta", "text": "Hello"},
            {"type": "assistant.response_finished"},
        ),
    )
    factory_calls = []

    def factory(*args):
        factory_calls.append(args)
        return client

    with (
        patch(
            "custom_components.voice_satellite.conversation.async_get_clientsession",
            return_value=object(),
        ),
        patch(
            "custom_components.voice_satellite.conversation.ExternalTransportClient",
            side_effect=factory,
        ),
    ):
        deltas = [
            delta
            async for delta in _entity(
                hass, requested_tools=["homeassistant__GetLiveContext"]
            )._event_deltas(SimpleNamespace(conversation_id="conversation-id", text="Hi"))
        ]

    session_start = factory_calls[0][3]
    assert session_start.client_kind == "ha_conversation"
    assert session_start.device_id is None
    assert session_start.input_modalities == ("text",)
    assert session_start.output_modalities == ("text",)
    assert session_start.requested_tools == ("homeassistant__GetLiveContext",)
    assert deltas == [{"content": "Hello"}]
    assert ("write_text", "Hi") in client.calls
    assert ("cancel_session", "conversation_turn_finished") in client.calls
    assert "close" in client.calls


async def test_text_conversation_rejects_server_policy_widening_and_cleans_up(hass) -> None:
    client = FakeClient(
        SimpleNamespace(
            effective_profile="home-generic",
            effective_tools=("homeassistant__GetLiveContext", "intent__HassTurnOn"),
        )
    )
    with (
        patch(
            "custom_components.voice_satellite.conversation.async_get_clientsession",
            return_value=object(),
        ),
        patch(
            "custom_components.voice_satellite.conversation.ExternalTransportClient",
            return_value=client,
        ),
        pytest.raises(conversation.ConverseError, match="policy was widened"),
    ):
        async for _ in _entity(
            hass, requested_tools=["homeassistant__GetLiveContext"]
        )._event_deltas(SimpleNamespace(conversation_id="conversation-id", text="Hi")):
            pass

    assert client.calls == [
        "connect",
        ("cancel_session", "conversation_turn_finished"),
        "close",
    ]


async def test_text_conversation_rejects_missing_service_credentials(hass) -> None:
    entity = _entity(hass)
    entity._entry.data[CONF_EXTERNAL_TRANSPORT_TOKEN] = ""
    with pytest.raises(conversation.ConverseError, match="not configured"):
        async for _ in entity._event_deltas(
            SimpleNamespace(conversation_id="conversation-id", text="Hi")
        ):
            pass
