"""Home Assistant fixture configuration for Voice Satellite integration tests."""

import pytest

@pytest.fixture(autouse=True)
async def loaded_manifest_dependencies(hass):
    """Avoid booting unrelated core UI integrations in config-flow unit tests."""
    for domain in ("assist_pipeline", "assist_satellite", "frontend", "http", "intent", "lovelace"):
        hass.config.components.add(domain)
