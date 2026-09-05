"""Constants for the Voice Satellite integration."""

from typing import Final

DOMAIN: Final[str] = "voice_satellite"

# External Transport configuration. Values live in ConfigEntry.options and
# must never be surfaced as entity state attributes or frontend settings.
CONF_EXTERNAL_TRANSPORT_URL: Final[str] = "external_transport_url"
CONF_EXTERNAL_TRANSPORT_TOKEN: Final[str] = "external_transport_token"
CONF_EXTERNAL_TRANSPORT_VERIFY_TLS: Final[str] = "external_transport_verify_tls"
CONF_EXTERNAL_TRANSPORT_READY_TIMEOUT: Final[str] = "external_transport_ready_timeout"
CONVERSATION_TRANSPORT_HOME_ASSISTANT: Final[str] = "Home Assistant"
CONVERSATION_TRANSPORT_EXTERNAL: Final[str] = "External"

# Version - synced from package.json by scripts/sync-version.js
INTEGRATION_VERSION: str = "2026.9.2"

# Bus events fired for user automations
EVENT_TIMER: Final[str] = "voice_satellite_timer"

# Frontend serving
URL_BASE: Final[str] = "/voice_satellite"
JS_FILENAME: Final[str] = "voice-satellite-card.js"
