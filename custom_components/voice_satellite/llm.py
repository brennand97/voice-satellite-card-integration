"""Expose validated Voice Satellite timer operations to the Assist LLM API."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components import llm
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.llm import LLMContext, ToolInput

from . import async_start_timer_for_device


class StartTimer(llm.Tool):
    """Start a timer on a validated Voice Satellite device."""

    name = "StartTimer"
    description = "Start a named timer on the current Voice Satellite."
    parameters = vol.Schema(
        {
            # Pipecat removes this context-owned provider argument from its
            # model schema and injects it only for physical attachments.
            vol.Required("device_id"): str,
            vol.Required("name"): vol.All(str, vol.Length(min=1, max=256)),
            vol.Optional("hours", default=0): vol.All(
                vol.Coerce(int), vol.Range(min=0, max=23)
            ),
            vol.Optional("minutes", default=0): vol.All(
                vol.Coerce(int), vol.Range(min=0, max=59)
            ),
            vol.Optional("seconds", default=0): vol.All(
                vol.Coerce(int), vol.Range(min=0, max=59)
            ),
        }
    )

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: ToolInput,
        llm_context: LLMContext,
    ) -> dict[str, Any]:
        """Create a timer through the shared validated timer primitive."""
        del llm_context
        arguments = tool_input.tool_args
        success = await async_start_timer_for_device(
            hass,
            device_id=arguments["device_id"],
            name=arguments["name"],
            hours=arguments.get("hours", 0),
            minutes=arguments.get("minutes", 0),
            seconds=arguments.get("seconds", 0),
        )
        if not success:
            raise HomeAssistantError("Unable to start a timer on this Voice Satellite")
        return {
            "success": True,
            "name": arguments["name"],
            "duration": {
                "hours": arguments.get("hours", 0),
                "minutes": arguments.get("minutes", 0),
                "seconds": arguments.get("seconds", 0),
            },
        }


def async_get_tools(
    hass: HomeAssistant, llm_context: LLMContext, api_id: str
) -> llm.LLMTools | None:
    """Contribute the tool to Assist, including the standard HA MCP server."""
    del hass, llm_context
    if api_id != "assist":
        return None
    return llm.LLMTools(
        tools=[StartTimer()],
        prompt=(
            "Use the Voice Satellite timer tool only when the user explicitly asks "
            "to start a timer."
        ),
    )
