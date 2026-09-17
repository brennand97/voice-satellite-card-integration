"""Lease ownership tests for the nearby media interference guard."""

from __future__ import annotations

import pytest
from homeassistant.core import Context

from custom_components.voice_satellite import ws_run_pipeline
from custom_components.voice_satellite.const import DOMAIN
from custom_components.voice_satellite.media_interference import (
    GuardPolicy,
    MediaInterferenceCoordinator,
)

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


async def test_pipeline_websocket_routes_past_integration_wide_coordinator(hass) -> None:
    """Reproduce fork.30: coordinator was visited before the Satellite entity."""
    completed = False

    class Satellite:
        entity_id = "assist_satellite.kitchen"
        pipeline_audio_queue = None
        pipeline_task = None
        uses_external_transport = False
        satellite_name = "Kitchen"

        async def async_run_pipeline_text(self, *_args, **_kwargs) -> None:
            nonlocal completed
            completed = True

    class Connection:
        def __init__(self) -> None:
            self.results = []
            self.events = []
            self.subscriptions = {}

        def send_result(self, msg_id) -> None:
            self.results.append(msg_id)

        def send_event(self, msg_id, event) -> None:
            self.events.append((msg_id, event))

    entity = Satellite()
    connection = Connection()
    # In production async_setup inserts this coordinator before platforms add
    # their entities. The old lookup accessed coordinator.entity_id directly
    # and raised AttributeError before creating the pipeline task.
    hass.data[DOMAIN] = {
        "media_interference_coordinator": object(),
        "entry": entity,
    }
    ws_run_pipeline(
        hass,
        connection,
        {
            "id": 7,
            "entity_id": entity.entity_id,
            "start_stage": "intent",
            "end_stage": "tts",
            "sample_rate": 16000,
            "intent_input": "hello",
        },
    )
    await hass.async_block_till_done()

    assert completed is True
    assert connection.results == [7]
    assert connection.events == [(7, {"type": "init", "handler_id": None})]


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


async def test_overlapping_duck_leases_restore_original_volume_only_after_final_release(hass) -> None:
    entity_id = "media_player.kitchen"
    calls: list[str] = []
    hass.states.async_set(entity_id, "playing", {"volume_level": 0.7, "media_title": "Test"})
    await _register_player_services(hass, entity_id, calls)
    coordinator = MediaInterferenceCoordinator(hass)

    first = await coordinator.acquire("kitchen", (entity_id,), GuardPolicy("duck", 0.1, 0))
    second = await coordinator.acquire("hall", (entity_id,), GuardPolicy("duck", 0.2, 0))
    await coordinator.activate(first)
    await hass.async_block_till_done()
    await coordinator.activate(second)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).attributes["volume_level"] == 0.1

    await coordinator.release(first)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).attributes["volume_level"] == 0.2
    await coordinator.release(second)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).attributes["volume_level"] == 0.7


async def test_user_volume_change_becomes_new_intended_volume(hass) -> None:
    entity_id = "media_player.kitchen"
    calls: list[str] = []
    hass.states.async_set(entity_id, "playing", {"volume_level": 0.7, "media_title": "Test"})
    await _register_player_services(hass, entity_id, calls)
    coordinator = MediaInterferenceCoordinator(hass)
    lease = await coordinator.acquire("kitchen", (entity_id,), GuardPolicy("duck", 0.1, 0))
    await coordinator.activate(lease)
    await hass.async_block_till_done()

    hass.states.async_set(entity_id, "playing", {"volume_level": 0.4, "media_title": "Test"}, context=Context())
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).attributes["volume_level"] == 0.1
    await coordinator.release(lease)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).attributes["volume_level"] == 0.4


async def test_new_playback_during_lease_is_ducked_and_restored_independently(hass) -> None:
    entity_id = "media_player.kitchen"
    calls: list[str] = []
    hass.states.async_set(entity_id, "playing", {"volume_level": 0.7, "media_title": "Old"})
    await _register_player_services(hass, entity_id, calls)
    coordinator = MediaInterferenceCoordinator(hass)
    lease = await coordinator.acquire("kitchen", (entity_id,), GuardPolicy("duck", 0.1, 0))
    await coordinator.activate(lease)
    await hass.async_block_till_done()

    hass.states.async_set(entity_id, "playing", {"volume_level": 0.65, "media_title": "New"}, context=Context())
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).attributes["volume_level"] == 0.1
    await coordinator.release(lease)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).attributes["volume_level"] == 0.65


async def test_pause_holder_release_resumes_into_remaining_duck(hass) -> None:
    entity_id = "media_player.kitchen"
    calls: list[str] = []
    hass.states.async_set(entity_id, "playing", {"volume_level": 0.7, "media_title": "Test"})
    await _register_player_services(hass, entity_id, calls)
    coordinator = MediaInterferenceCoordinator(hass)
    duck = await coordinator.acquire("kitchen", (entity_id,), GuardPolicy("duck", 0.1, 0))
    pause = await coordinator.acquire("hall", (entity_id,), GuardPolicy("pause", 0.1, 0))
    await coordinator.activate(duck)
    await hass.async_block_till_done()
    await coordinator.activate(pause)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "paused"

    await coordinator.release(pause)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "playing"
    assert hass.states.get(entity_id).attributes["volume_level"] == 0.1
    await coordinator.release(duck)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).attributes["volume_level"] == 0.7


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
