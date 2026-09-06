"""Timer requests may only use a validated physical Satellite attachment."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from homeassistant.components.intent.const import TIMER_DATA

from custom_components.voice_satellite import async_start_timer_for_device
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
