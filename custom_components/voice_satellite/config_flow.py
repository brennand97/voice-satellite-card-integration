"""Config and options flows for Voice Satellite integration."""

from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow

from .const import (
    CONF_EXTERNAL_TRANSPORT_READY_TIMEOUT,
    CONF_EXTERNAL_TRANSPORT_TOKEN,
    CONF_EXTERNAL_TRANSPORT_URL,
    CONF_EXTERNAL_TRANSPORT_VERIFY_TLS,
    DOMAIN,
)


class VoiceSatelliteConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Voice Satellite."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, str] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step - user enters a name for the satellite."""
        if user_input is not None:
            name = user_input["name"].strip()
            await self.async_set_unique_id(name.lower().replace(" ", "_"))
            self._abort_if_unique_id_configured()
            return self.async_create_entry(title=name, data={"name": name})

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required("name"): str}),
            errors={},
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
