"""Success and error coverage for user-visible External Conversation flows."""

from __future__ import annotations

import pytest
from homeassistant.data_entry_flow import FlowResultType

from custom_components.voice_satellite.config_flow import VoiceSatelliteOptionsFlow
from custom_components.voice_satellite.const import (
    CONF_ENTRY_TYPE,
    CONF_EXTERNAL_TRANSPORT_READY_TIMEOUT,
    CONF_EXTERNAL_TRANSPORT_TOKEN,
    CONF_EXTERNAL_TRANSPORT_URL,
    CONF_EXTERNAL_TRANSPORT_VERIFY_TLS,
    ENTRY_TYPE_SERVICE,
)
from custom_components.voice_satellite.profile_flow import ExternalConversationProfileFlow

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


def _assert_schema_is_ui_serializable(schema) -> None:
    """Reject raw list validators, which HA config-flow UIs cannot serialize."""
    assert all(validator != [str] for validator in schema.schema.values())


async def _start_user_flow(hass):
    result = await hass.config_entries.flow.async_init(
        "voice_satellite", context={"source": "user"}
    )
    assert result["type"] is FlowResultType.FORM
    _assert_schema_is_ui_serializable(result["data_schema"])
    return result


async def test_satellite_creation_rejects_blank_and_duplicate_names(hass) -> None:
    result = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ENTRY_TYPE: "satellite"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "satellite"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "   "}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_name"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "Kitchen"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Kitchen"

    result = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ENTRY_TYPE: "satellite"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": " kitchen "}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_service_creation_flow_schemas_serialize_and_create_entry(hass) -> None:
    result = await _start_user_flow(hass)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_ENTRY_TYPE: ENTRY_TYPE_SERVICE},
    )
    assert result["type"] is FlowResultType.FORM
    _assert_schema_is_ui_serializable(result["data_schema"])

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "name": "Primary external service",
            CONF_EXTERNAL_TRANSPORT_URL: "wss://voice.example/transport/v1",
            CONF_EXTERNAL_TRANSPORT_TOKEN: "test-token",
            CONF_EXTERNAL_TRANSPORT_VERIFY_TLS: True,
            CONF_EXTERNAL_TRANSPORT_READY_TIMEOUT: 5,
            "profile_name": "Default profile",
            "tool_profile": "home-generic",
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_ENTRY_TYPE] == ENTRY_TYPE_SERVICE
    assert result["data"][CONF_EXTERNAL_TRANSPORT_TOKEN] == "test-token"


async def test_service_creation_rejects_incomplete_credentials(hass) -> None:
    result = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ENTRY_TYPE: ENTRY_TYPE_SERVICE}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "external_service"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "name": "External",
            CONF_EXTERNAL_TRANSPORT_URL: "wss://voice.example/transport/v1",
            CONF_EXTERNAL_TRANSPORT_TOKEN: "",
            CONF_EXTERNAL_TRANSPORT_VERIFY_TLS: True,
            CONF_EXTERNAL_TRANSPORT_READY_TIMEOUT: 5,
            "profile_name": "Default profile",
            "tool_profile": "home-generic",
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "external_transport_credentials_incomplete"}


@pytest.mark.parametrize(
    ("requested_tools", "error"),
    [
        ("homeassistant__GetLiveContext, intent__HassTurnOn", None),
        ("homeassistant__GetLiveContext\nhomeassistant__GetLiveContext", "invalid_requested_tools"),
        ("intent__Hass*", "invalid_requested_tools"),
    ],
)
def test_profile_prompt_uses_a_multiline_text_selector() -> None:
    from homeassistant.helpers.selector import TextSelector

    flow = object.__new__(ExternalConversationProfileFlow)
    flow.source = "reconfigure"
    flow._options = {"initial_prompt": "A detailed instruction."}
    schema = flow._schema().schema
    selector = next(
        value
        for key, value in schema.items()
        if getattr(key, "schema", None) == "initial_prompt"
    )
    assert isinstance(selector, TextSelector)
    assert selector.config.multiline is True


def test_profile_input_parses_exact_tools_or_fails_closed(requested_tools, error) -> None:
    data = {
        "profile_name": "Home",
        "tool_profile": "home-generic",
        "initial_prompt": "",
        "initial_voice": "",
        "requested_tools": requested_tools,
    }
    if error is None:
        parsed = ExternalConversationProfileFlow._validate(data)
        assert parsed["requested_tools"] == [
            "homeassistant__GetLiveContext",
            "intent__HassTurnOn",
        ]
    else:
        with pytest.raises(ValueError, match=error):
            ExternalConversationProfileFlow._validate(data)


def test_satellite_assignment_schemas_are_service_first_and_filter_profiles() -> None:
    service = type(
        "Service",
        (),
        {
            "entry_id": "service-id",
            "title": "Loki1 Pipecat",
            "data": {"entry_type": ENTRY_TYPE_SERVICE},
            "subentries": {
                "profile-id": type(
                    "Profile",
                    (),
                    {"subentry_type": "conversation", "title": "Default"},
                )(),
                "other-id": type(
                    "Other",
                    (),
                    {"subentry_type": "not-a-conversation", "title": "Ignore"},
                )(),
            },
        },
    )()
    flow = object.__new__(VoiceSatelliteOptionsFlow)
    flow.config_entry = type("Satellite", (), {"options": {}})()
    flow.hass = type(
        "Hass",
        (),
        {"config_entries": type("Entries", (), {"async_entries": lambda *_: [service]})()},
    )()

    service_schema = flow._service_schema().schema
    profile_schema = flow._profile_schema(service).schema
    assert len(service_schema) == 1
    assert len(profile_schema) == 1
    assert "profile-id" in next(iter(profile_schema.values())).container
    assert "other-id" not in next(iter(profile_schema.values())).container


def test_profile_schema_uses_serializable_text_requested_tools_field() -> None:
    class ReconfigureFlow(ExternalConversationProfileFlow):
        @property
        def _is_new(self) -> bool:
            return False

    flow = object.__new__(ReconfigureFlow)
    flow._options = {"requested_tools": ["homeassistant__GetLiveContext"]}
    _assert_schema_is_ui_serializable(flow._schema())
