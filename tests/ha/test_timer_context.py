"""Timer requests may only use a validated physical Satellite attachment."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from homeassistant.components import intent
from homeassistant.components.intent.const import TIMER_DATA

from custom_components.voice_satellite import async_start_timer_for_device
from custom_components.voice_satellite.assist_satellite import VoiceSatelliteEntity
from custom_components.voice_satellite.const import DOMAIN

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


class FakeTimerManager:
    def __init__(self) -> None:
        self.calls = []

    def start_timer(self, **kwargs) -> None:
        self.calls.append(kwargs)


async def test_timer_requires_registered_satellite_device_context(hass) -> None:
    timers = FakeTimerManager()
    hass.data[TIMER_DATA] = timers
    hass.data[DOMAIN] = {
        "satellite": SimpleNamespace(
            device_entry=SimpleNamespace(id="satellite-device-id")
        )
    }

    assert not await async_start_timer_for_device(
        hass, device_id="unknown-device", name="Tea", minutes=5
    )
    assert not await async_start_timer_for_device(
        hass, device_id="satellite-device-id", name="Tea"
    )
    assert timers.calls == []

    assert await async_start_timer_for_device(
        hass, device_id="satellite-device-id", name="Tea", minutes=5
    )
    assert timers.calls == [
        {
            "device_id": "satellite-device-id",
            "hours": None,
            "minutes": 5,
            "seconds": None,
            "language": hass.config.language or "en",
            "name": "Tea",
        }
    ]


def test_timer_updated_event_projects_renamed_label_to_satellite() -> None:
    """A TimerManager rename must replace, not retain, the card-facing label."""
    timer = SimpleNamespace(
        id="timer-id",
        name="Egg timer",
        seconds_left=42,
        start_hours=0,
        start_minutes=1,
        start_seconds=0,
        area_id=None,
        area_name=None,
        is_active=True,
    )
    events = []
    entity = SimpleNamespace(
        _active_timers=[
            {
                "id": "timer-id",
                "name": "1-minute timer",
                "total_seconds": 60,
                "started_at": 0,
                "start_hours": 0,
                "start_minutes": 1,
                "start_seconds": 0,
            }
        ],
        _satellite_name="Kitchen Tablet",
        async_write_ha_state=lambda: None,
        hass=SimpleNamespace(bus=SimpleNamespace(async_fire=lambda *args: events.append(args))),
        entity_id="assist_satellite.kitchen_tablet",
        registry_entry=SimpleNamespace(device_id="satellite-device-id"),
    )

    VoiceSatelliteEntity._handle_timer_event(entity, intent.TimerEventType.UPDATED, timer)

    assert entity._active_timers[0]["name"] == "Egg timer"
    assert entity._active_timers[0]["total_seconds"] == 42
    assert events[0][1]["name"] == "Egg timer"


async def test_timer_manager_failure_is_not_exposed_to_tool_callers(hass) -> None:
    class FailingTimerManager:
        def start_timer(self, **kwargs) -> None:
            raise RuntimeError("timer unavailable")

    hass.data[TIMER_DATA] = FailingTimerManager()
    hass.data[DOMAIN] = {
        "satellite": SimpleNamespace(
            device_entry=SimpleNamespace(id="satellite-device-id")
        )
    }
    assert not await async_start_timer_for_device(
        hass, device_id="satellite-device-id", name="Tea", seconds=1
    )
