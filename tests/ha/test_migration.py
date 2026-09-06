"""Legacy External Transport migration success and fail-closed coverage."""

from __future__ import annotations

from types import MappingProxyType

import pytest

from homeassistant.config_entries import ConfigEntry, SOURCE_IMPORT

from custom_components.voice_satellite import async_migrate_entry
from custom_components.voice_satellite.const import (
    CONF_CONVERSATION_PROFILE_ID,
    CONF_CONVERSATION_SERVICE_ENTRY_ID,
    CONF_ENTRY_TYPE,
    CONF_EXTERNAL_TRANSPORT_TOKEN,
    CONF_EXTERNAL_TRANSPORT_URL,
    DOMAIN,
    ENTRY_TYPE_SERVICE,
)

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


def _legacy_entry(*, options: dict, version: int = 1) -> ConfigEntry:
    return ConfigEntry(
        data={"name": "Kitchen"},
        discovery_keys=MappingProxyType({}),
        domain=DOMAIN,
        minor_version=1,
        options=options,
        source=SOURCE_IMPORT,
        subentries_data=(),
        title="Kitchen",
        unique_id="kitchen",
        version=version,
    )


async def test_legacy_credentials_move_to_service_and_profile(hass) -> None:
    entry = _legacy_entry(
        options={
            CONF_EXTERNAL_TRANSPORT_URL: "wss://voice.example/transport/v1",
            CONF_EXTERNAL_TRANSPORT_TOKEN: "test-token",
        }
    )
    await hass.config_entries.async_add(entry)

    assert await async_migrate_entry(hass, entry)
    assert entry.version == 2
    assert CONF_EXTERNAL_TRANSPORT_URL not in entry.options
    assert CONF_EXTERNAL_TRANSPORT_TOKEN not in entry.options

    service = hass.config_entries.async_get_entry(
        entry.options[CONF_CONVERSATION_SERVICE_ENTRY_ID]
    )
    assert service is not None
    assert service.data[CONF_ENTRY_TYPE] == ENTRY_TYPE_SERVICE
    assert service.data[CONF_EXTERNAL_TRANSPORT_TOKEN] == "test-token"
    profile = service.subentries[entry.options[CONF_CONVERSATION_PROFILE_ID]]
    assert profile.subentry_type == "conversation"


async def test_legacy_entry_without_complete_credentials_is_upgraded_but_unassigned(hass) -> None:
    entry = _legacy_entry(options={CONF_EXTERNAL_TRANSPORT_URL: "wss://voice.example"})
    await hass.config_entries.async_add(entry)

    assert await async_migrate_entry(hass, entry)
    assert entry.version == 2
    assert CONF_CONVERSATION_SERVICE_ENTRY_ID not in entry.options
    assert CONF_CONVERSATION_PROFILE_ID not in entry.options
    assert hass.config_entries.async_entries(DOMAIN) == [entry]


async def test_current_entries_are_not_migrated_again(hass) -> None:
    entry = _legacy_entry(
        options={CONF_EXTERNAL_TRANSPORT_TOKEN: "legacy-token"}, version=2
    )
    await hass.config_entries.async_add(entry)

    assert await async_migrate_entry(hass, entry)
    assert entry.options[CONF_EXTERNAL_TRANSPORT_TOKEN] == "legacy-token"
