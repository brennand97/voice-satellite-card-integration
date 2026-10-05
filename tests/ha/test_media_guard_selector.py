"""Regression: the actual HA selector validates whole numbers as floats."""
import pytest
from custom_components.voice_satellite.config_flow import VoiceSatelliteOptionsFlow

class GuardFlow(VoiceSatelliteOptionsFlow):
    @property
    def config_entry(self):
        return type("Entry", (), {"options": {}})()

@pytest.mark.parametrize("volume,delay", [(10,350),(10.0,350.0),(1,0),(50,2000)])
async def test_real_selector_coercion_saves_normalized_integer_options(volume, delay):
    flow = GuardFlow()
    flow._service_entry_id = "service"
    flow._profile_id = "profile"
    payload = flow._media_guard_schema()({"media_guard_entities": ["media_player.test"], "media_guard_action": "duck", "media_guard_volume": volume, "media_guard_restore_delay_ms": delay})
    assert isinstance(payload["media_guard_volume"], float)
    result = await flow.async_step_media_guard(payload)
    assert result["type"] == "create_entry"
    assert type(result["data"]["media_guard_volume"]) is int
    assert type(result["data"]["media_guard_restore_delay_ms"]) is int

@pytest.mark.parametrize("volume,delay", [(True,350),(10,False),(10.5,350),(10,350.5),(float("nan"),350),(float("inf"),350),(0,350),(51,350),(10,-1),(10,2001)])
async def test_guard_rejects_non_integral_or_out_of_bounds_numbers(volume, delay):
    flow = GuardFlow()
    result = await flow.async_step_media_guard({"media_guard_entities": [], "media_guard_action": "duck", "media_guard_volume": volume, "media_guard_restore_delay_ms": delay})
    assert result["errors"] == {"base": "invalid_media_guard"}
