"""Constants for the Voice Satellite integration."""

from typing import Final

DOMAIN: Final[str] = "voice_satellite"
ENTRY_TYPE_SERVICE: Final[str] = "external_conversation_service"
CONF_ENTRY_TYPE: Final[str] = "entry_type"
CONF_PROFILE_NAME: Final[str] = "profile_name"
CONF_TOOL_PROFILE: Final[str] = "tool_profile"
CONF_REQUESTED_TOOLS: Final[str] = "requested_tools"
CONF_CONVERSATION_SERVICE_ENTRY_ID: Final[str] = "conversation_service_entry_id"
CONF_CONVERSATION_PROFILE_ID: Final[str] = "conversation_profile_id"
CONF_INITIAL_PROMPT: Final[str] = "initial_prompt"
CONF_INITIAL_VOICE: Final[str] = "initial_voice"

# Per-physical-Satellite nearby media guard. These are ConfigEntry options,
# never model/profile policy or entity state attributes.
CONF_MEDIA_GUARD_ENTITIES: Final[str] = "media_guard_entities"
CONF_MEDIA_GUARD_ACTION: Final[str] = "media_guard_action"
CONF_MEDIA_GUARD_VOLUME: Final[str] = "media_guard_volume"
CONF_MEDIA_GUARD_RESTORE_DELAY_MS: Final[str] = "media_guard_restore_delay_ms"
MEDIA_GUARD_OFF: Final[str] = "off"
MEDIA_GUARD_DUCK: Final[str] = "duck"
MEDIA_GUARD_PAUSE: Final[str] = "pause"

# External Transport configuration. Values live in ConfigEntry.options and
# must never be surfaced as entity state attributes or frontend settings.
CONF_EXTERNAL_TRANSPORT_URL: Final[str] = "external_transport_url"
CONF_EXTERNAL_TRANSPORT_TOKEN: Final[str] = "external_transport_token"
CONF_EXTERNAL_TRANSPORT_VERIFY_TLS: Final[str] = "external_transport_verify_tls"
CONF_EXTERNAL_TRANSPORT_READY_TIMEOUT: Final[str] = "external_transport_ready_timeout"
CONVERSATION_TRANSPORT_HOME_ASSISTANT: Final[str] = "Home Assistant"
CONVERSATION_TRANSPORT_EXTERNAL: Final[str] = "External"

# Version - synced from package.json by scripts/sync-version.js
INTEGRATION_VERSION: str = "2026.9.5-fork.28"

# Bus events fired for user automations
EVENT_TIMER: Final[str] = "voice_satellite_timer"

# Frontend serving
URL_BASE: Final[str] = "/voice_satellite"
JS_FILENAME: Final[str] = "voice-satellite-card.js"
