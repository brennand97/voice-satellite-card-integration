"""Config-subentry flow for External Transport conversation profiles."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigSubentryFlow, SubentryFlowResult

from .const import (
    CONF_INITIAL_PROMPT,
    CONF_INITIAL_VOICE,
    CONF_PROFILE_NAME,
    CONF_REQUESTED_TOOLS,
    CONF_TOOL_PROFILE,
)


class ExternalConversationProfileFlow(ConfigSubentryFlow):
    """Create and reconfigure an immutable-per-session conversation profile."""

    _options: dict[str, Any]

    @property
    def _is_new(self) -> bool:
        return self.source == "user"

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        self._options = {}
        return await self.async_step_init()

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        self._options = dict(self._get_reconfigure_subentry().data)
        return await self.async_step_init()

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                data = self._validate(user_input)
            except ValueError as err:
                errors["base"] = str(err)
            else:
                if self._is_new:
                    return self.async_create_entry(
                        title=data.pop(CONF_PROFILE_NAME), data=data
                    )
                return self.async_update_and_abort(
                    self._get_entry(), self._get_reconfigure_subentry(), data=data
                )
        return self.async_show_form(
            step_id="init", data_schema=self._schema(), errors=errors
        )

    def _schema(self) -> vol.Schema:
        data = self._options
        schema: dict[Any, Any] = {
            vol.Required(
                CONF_TOOL_PROFILE, default=data.get(CONF_TOOL_PROFILE, "")
            ): str,
            vol.Optional(
                CONF_INITIAL_PROMPT, default=data.get(CONF_INITIAL_PROMPT, "")
            ): str,
            vol.Optional(
                CONF_INITIAL_VOICE, default=data.get(CONF_INITIAL_VOICE, "")
            ): str,
            vol.Optional(
                CONF_REQUESTED_TOOLS,
                default=", ".join(data.get(CONF_REQUESTED_TOOLS, [])),
            ): str,
        }
        if self._is_new:
            schema = {
                vol.Required(CONF_PROFILE_NAME, default="External conversation"): str,
                **schema,
            }
        return vol.Schema(schema)

    @staticmethod
    def _validate(user_input: dict[str, Any]) -> dict[str, Any]:
        data = dict(user_input)
        profile_name = data.get(CONF_PROFILE_NAME)
        if profile_name is not None:
            if not isinstance(profile_name, str) or not profile_name.strip():
                raise ValueError("invalid_profile")
            data[CONF_PROFILE_NAME] = profile_name.strip()
        tool_profile = data.get(CONF_TOOL_PROFILE, "")
        if not isinstance(tool_profile, str) or len(tool_profile.encode()) > 128:
            raise ValueError("invalid_tool_profile")
        if tool_profile.strip():
            data[CONF_TOOL_PROFILE] = tool_profile.strip()
        else:
            data.pop(CONF_TOOL_PROFILE, None)
        for key, limit in ((CONF_INITIAL_PROMPT, 16_000), (CONF_INITIAL_VOICE, 128)):
            value = data.get(key, "")
            if not isinstance(value, str) or len(value.encode()) > limit:
                raise ValueError("invalid_profile")
            if value.strip():
                data[key] = value
            else:
                data.pop(key, None)
        requested = data.get(CONF_REQUESTED_TOOLS, "")
        if not isinstance(requested, str):
            raise ValueError("invalid_requested_tools")
        tools = [
            name.strip()
            for name in requested.replace("\n", ",").split(",")
            if name.strip()
        ]
        if (
            len(tools) > 128
            or not all("*" not in name for name in tools)
            or len(set(tools)) != len(tools)
        ):
            raise ValueError("invalid_requested_tools")
        if tools:
            data[CONF_REQUESTED_TOOLS] = tools
        else:
            data.pop(CONF_REQUESTED_TOOLS, None)
        return data
