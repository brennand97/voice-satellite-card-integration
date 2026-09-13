"""Lease ownership tests for the nearby media interference guard."""

from __future__ import annotations

import pytest
from homeassistant.core import Context

from custom_components.voice_satellite.media_interference import (
    GuardPolicy,
    MediaInterferenceCoordinator,
)

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


async def _register_player_services(hass, entity_id: str, calls: list[str]) -> None:
    async def handler(call) -> None:
        calls.append(call.service)
        old = hass.states.get(entity_id)
        attrs = dict(old.attributes)
        state = old.state
        if call.service == "volume_set":
            attrs["volume_level"] = call.data["volume_level"]
        elif call.service == "media_pause":
            state = "paused"
        elif call.service == "media_play":
            state = "playing"
        hass.states.async_set(entity_id, state, attrs, context=call.context)

    for service in ("volume_set", "media_pause", "media_play"):
        hass.services.async_register("media_player", service, handler)


async def test_duck_restores_intended_volume_after_final_lease(hass) -> None:
    entity_id = "media_player.kitchen"
    calls: list[str] = []
    hass.states.async_set(entity_id, "playing", {"volume_level": 0.7, "media_title": "Test"})
    await _register_player_services(hass, entity_id, calls)
    coordinator = MediaInterferenceCoordinator(hass)

    lease = await coordinator.acquire("kitchen", (entity_id,), GuardPolicy("duck", 0.1, 0))
    await coordinator.activate(lease)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).attributes["volume_level"] == 0.1

    await coordinator.release(lease)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).attributes["volume_level"] == 0.7
    assert calls == ["volume_set", "volume_set"]


async def test_pause_is_not_restored_after_user_stop(hass) -> None:
    entity_id = "media_player.kitchen"
    calls: list[str] = []
    hass.states.async_set(entity_id, "playing", {"volume_level": 0.7, "media_title": "Test"})
    await _register_player_services(hass, entity_id, calls)
    coordinator = MediaInterferenceCoordinator(hass)

    lease = await coordinator.acquire("kitchen", (entity_id,), GuardPolicy("pause", 0.1, 0))
    await coordinator.activate(lease)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "paused"

    # This is deliberately not a guard context: never revive a user stop.
    hass.states.async_set(entity_id, "idle", {}, context=Context())
    await hass.async_block_till_done()
    await coordinator.release(lease)
    await hass.async_block_till_done()
    assert "media_play" not in calls
