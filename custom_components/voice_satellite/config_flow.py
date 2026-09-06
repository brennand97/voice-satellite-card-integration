"""Config and options flows for Voice Satellite integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.core import callback

from .profile_flow import ExternalConversationProfileFlow
from .const import (
    CONF_CONVERSATION_PROFILE_ID,
    CONF_CONVERSATION_SERVICE_ENTRY_ID,
    CONF_EXTERNAL_TRANSPORT_READY_TIMEOUT,
    CONF_EXTERNAL_TRANSPORT_TOKEN,
    CONF_EXTERNAL_TRANSPORT_URL,
    CONF_EXTERNAL_TRANSPORT_VERIFY_TLS,
    CONF_ENTRY_TYPE,
    CONF_INITIAL_PROMPT,
    CONF_INITIAL_VOICE,
    CONF_PROFILE_NAME,
    CONF_REQUESTED_TOOLS,
    CONF_TOOL_PROFILE,
    DOMAIN,
    ENTRY_TYPE_SERVICE,
)


class VoiceSatelliteConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Voice Satellite."""

    VERSION = 2

    async def async_step_user(
        self, user_input: dict[str, str] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step - user enters a name for the satellite."""
        if user_input is not None:
            if user_input[CONF_ENTRY_TYPE] == ENTRY_TYPE_SERVICE:
                return await self.async_step_external_service()
            name = user_input["name"].strip()
            await self.async_set_unique_id(name.lower().replace(" ", "_"))
            self._abort_if_unique_id_configured()
            return self.async_create_entry(title=name, data={"name": name})

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required("name"): str,
                    vol.Required(CONF_ENTRY_TYPE, default="satellite"): vol.In(
                        {"satellite": "Voice Satellite", ENTRY_TYPE_SERVICE: "External Conversation Service"}
                    ),
                }
            ),
            errors={},
        )

    async def async_step_external_service(
        self, user_input: dict[str, object] | None = None
    ) -> ConfigFlowResult:
        """Create an entity-free connection with an initial conversation profile."""
        if user_input is not None:
            name = str(user_input["name"]).strip()
            url = str(user_input[CONF_EXTERNAL_TRANSPORT_URL]).strip()
            token = str(user_input[CONF_EXTERNAL_TRANSPORT_TOKEN]).strip()
            if not name or not url or not token:
                return self.async_show_form(step_id="external_service", data_schema=self._service_schema(), errors={"base": "external_transport_credentials_incomplete"})
            return self.async_create_entry(
                title=name,
                data={
                    CONF_ENTRY_TYPE: ENTRY_TYPE_SERVICE,
                    CONF_EXTERNAL_TRANSPORT_URL: url,
                    CONF_EXTERNAL_TRANSPORT_TOKEN: token,
                    CONF_EXTERNAL_TRANSPORT_VERIFY_TLS: bool(user_input[CONF_EXTERNAL_TRANSPORT_VERIFY_TLS]),
                    CONF_EXTERNAL_TRANSPORT_READY_TIMEOUT: int(user_input[CONF_EXTERNAL_TRANSPORT_READY_TIMEOUT]),
                },
                subentries=[
                    {
                        "subentry_type": "conversation",
                        "title": str(user_input[CONF_PROFILE_NAME]).strip() or name,
                        "unique_id": None,
                        "data": {CONF_TOOL_PROFILE: str(user_input[CONF_TOOL_PROFILE]).strip() or None},
                    }
                ],
            )
        return self.async_show_form(step_id="external_service", data_schema=self._service_schema())

    @staticmethod
    def _service_schema() -> vol.Schema:
        return vol.Schema(
            {
                vol.Required("name"): str,
                vol.Required(CONF_EXTERNAL_TRANSPORT_URL): str,
                vol.Required(CONF_EXTERNAL_TRANSPORT_TOKEN): str,
                vol.Optional(CONF_EXTERNAL_TRANSPORT_VERIFY_TLS, default=True): bool,
                vol.Optional(CONF_EXTERNAL_TRANSPORT_READY_TIMEOUT, default=5): vol.All(vol.Coerce(int), vol.Range(min=1, max=30)),
                vol.Required(CONF_PROFILE_NAME, default="External conversation"): str,
                vol.Optional(CONF_TOOL_PROFILE, default=""): str,
            }
        )

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Expose profiles only on entity-free service entries."""
        if config_entry.data.get(CONF_ENTRY_TYPE) != ENTRY_TYPE_SERVICE:
            return {}
        return {"conversation": ExternalConversationProfileFlow}

    @staticmethod
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return integration options, including external transport settings."""
        return VoiceSatelliteOptionsFlow()


class VoiceSatelliteOptionsFlow(OptionsFlow):
    """Assign a Satellite to an existing shared conversation profile."""

    async def async_step_init(
        self, user_input: dict[str, object] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            service_entry_id = str(user_input.get(CONF_CONVERSATION_SERVICE_ENTRY_ID, ""))
            profile_id = str(user_input.get(CONF_CONVERSATION_PROFILE_ID, ""))
            service = self.hass.config_entries.async_get_entry(service_entry_id)
            profile = service.subentries.get(profile_id) if service is not None else None
            if (
                service is None
                or service.data.get(CONF_ENTRY_TYPE) != ENTRY_TYPE_SERVICE
                or profile is None
                or profile.subentry_type != "conversation"
            ):
                return self.async_show_form(
                    step_id="init", data_schema=self._schema(), errors={"base": "invalid_profile_assignment"}
                )
            return self.async_create_entry(
                title="",
                data={
                    CONF_CONVERSATION_SERVICE_ENTRY_ID: service_entry_id,
                    CONF_CONVERSATION_PROFILE_ID: profile_id,
                },
            )
        return self.async_show_form(step_id="init", data_schema=self._schema())

    def _schema(self) -> vol.Schema:
        options = self.config_entry.options
        services = {
            entry.entry_id: entry.title
            for entry in self.hass.config_entries.async_entries(DOMAIN)
            if entry.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_SERVICE
        }
        profiles = {
            subentry.subentry_id: f"{entry.title}: {subentry.title}"
            for entry in self.hass.config_entries.async_entries(DOMAIN)
            if entry.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_SERVICE
            for subentry in entry.subentries.values()
            if subentry.subentry_type == "conversation"
        }
        return vol.Schema(
            {
                vol.Required(
                    CONF_CONVERSATION_SERVICE_ENTRY_ID,
                    default=options.get(CONF_CONVERSATION_SERVICE_ENTRY_ID),
                ): vol.In(services),
                vol.Required(
                    CONF_CONVERSATION_PROFILE_ID,
                    default=options.get(CONF_CONVERSATION_PROFILE_ID),
                ): vol.In(profiles),
            }
        )
