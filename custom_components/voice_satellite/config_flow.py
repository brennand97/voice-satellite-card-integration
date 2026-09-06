"""Config and options flows for Voice Satellite integration."""

from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow

from .const import (
    CONF_EXTERNAL_TRANSPORT_READY_TIMEOUT,
    CONF_EXTERNAL_TRANSPORT_TOKEN,
    CONF_EXTERNAL_TRANSPORT_URL,
    CONF_EXTERNAL_TRANSPORT_VERIFY_TLS,
    CONF_ENTRY_TYPE,
    CONF_PROFILE_NAME,
    CONF_TOOL_PROFILE,
    DOMAIN,
    ENTRY_TYPE_SERVICE,
)


class VoiceSatelliteConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Voice Satellite."""

    VERSION = 1

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

    @staticmethod
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return integration options, including external transport settings."""
        return VoiceSatelliteOptionsFlow()


class VoiceSatelliteOptionsFlow(OptionsFlow):
    """Configure backend-only External Transport connection settings."""

    async def async_step_init(
        self, user_input: dict[str, object] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            url = str(user_input.get(CONF_EXTERNAL_TRANSPORT_URL, "")).strip()
            token = str(user_input.get(CONF_EXTERNAL_TRANSPORT_TOKEN, "")).strip()
            if bool(url) != bool(token):
                return self.async_show_form(
                    step_id="init",
                    data_schema=self._schema(),
                    errors={"base": "external_transport_credentials_incomplete"},
                )
            return self.async_create_entry(title="", data=dict(user_input))

        return self.async_show_form(step_id="init", data_schema=self._schema())

    def _schema(self) -> vol.Schema:
        options = self.config_entry.options
        return vol.Schema(
            {
                vol.Optional(
                    CONF_EXTERNAL_TRANSPORT_URL,
                    default=options.get(CONF_EXTERNAL_TRANSPORT_URL, ""),
                ): str,
                vol.Optional(
                    CONF_EXTERNAL_TRANSPORT_TOKEN,
                    default=options.get(CONF_EXTERNAL_TRANSPORT_TOKEN, ""),
                ): str,
                vol.Optional(
                    CONF_EXTERNAL_TRANSPORT_VERIFY_TLS,
                    default=options.get(CONF_EXTERNAL_TRANSPORT_VERIFY_TLS, True),
                ): bool,
                vol.Optional(
                    CONF_EXTERNAL_TRANSPORT_READY_TIMEOUT,
                    default=options.get(CONF_EXTERNAL_TRANSPORT_READY_TIMEOUT, 5),
                ): vol.All(vol.Coerce(int), vol.Range(min=1, max=30)),
            }
        )
