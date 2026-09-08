"""Expose validated Voice Satellite timer operations to the Assist LLM API."""

from __future__ import annotations

import time
from typing import Any

import voluptuous as vol

from homeassistant.components import llm
from homeassistant.components.intent import TimerManager
from homeassistant.components.intent.const import TIMER_DATA
from homeassistant.components.intent.timers import TimerEventType, TimerInfo
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.llm import LLMContext, ToolInput

from . import async_start_timer_for_device


def _timer_manager(hass: HomeAssistant) -> TimerManager:
    manager: TimerManager | None = hass.data.get(TIMER_DATA)
    if manager is None:
        raise HomeAssistantError("Timer service is unavailable.")
    return manager


def _select_timer(
    manager: TimerManager, device_id: str, name: str | None
) -> TimerInfo:
    timers = [timer for timer in manager.timers.values() if timer.device_id == device_id]
    if name is not None:
        timers = [timer for timer in timers if (timer.name or "").casefold() == name.casefold()]
    if not timers:
        target = f" named '{name}'" if name else ""
        raise HomeAssistantError(f"No active timer{target} exists on this Voice Satellite.")
    if len(timers) > 1:
        raise HomeAssistantError("Multiple active timers match; specify the timer name.")
    return timers[0]


def _duration(arguments: dict[str, Any]) -> int:
    seconds = (
        int(arguments.get("hours", 0)) * 3600
        + int(arguments.get("minutes", 0)) * 60
        + int(arguments.get("seconds", 0))
    )
    if seconds <= 0:
        raise HomeAssistantError("Provide a relative duration of at least one second.")
    return seconds


def _status(timer: TimerInfo) -> dict[str, Any]:
    return {
        "id": timer.id,
        "name": timer.name or "Timer",
        "state": "running" if timer.is_active else "paused",
        "seconds_remaining": timer.seconds_left,
    }


class _DeviceTimerTool(llm.Tool):
    """Base schema for operations whose device context is server-owned."""

    _device = {vol.Required("device_id"): str}


class StartTimer(_DeviceTimerTool):
    name = "StartTimer"
    description = "Start a named timer on the current Voice Satellite."
    parameters = vol.Schema(
        {
            **_DeviceTimerTool._device,
            vol.Required("name"): vol.All(str, vol.Length(min=1, max=256)),
            vol.Optional("hours", default=0): vol.All(vol.Coerce(int), vol.Range(min=0, max=23)),
            vol.Optional("minutes", default=0): vol.All(vol.Coerce(int), vol.Range(min=0, max=59)),
            vol.Optional("seconds", default=0): vol.All(vol.Coerce(int), vol.Range(min=0, max=59)),
        }
    )

    async def async_call(self, hass: HomeAssistant, tool_input: ToolInput, llm_context: LLMContext) -> dict[str, Any]:
        del llm_context
        arguments = tool_input.tool_args
        success = await async_start_timer_for_device(hass, device_id=arguments["device_id"], name=arguments["name"], hours=arguments.get("hours", 0), minutes=arguments.get("minutes", 0), seconds=arguments.get("seconds", 0))
        if not success:
            raise HomeAssistantError("Timer was not started on this Voice Satellite.")
        return {"success": True, "name": arguments["name"], "duration": {key: arguments.get(key, 0) for key in ("hours", "minutes", "seconds")}}


class StopTimer(_DeviceTimerTool):
    name = "voice_satellite__StopTimer"
    description = "Stop and cancel a timer on the current Voice Satellite."
    parameters = vol.Schema({**_DeviceTimerTool._device, vol.Optional("name"): str})

    async def async_call(self, hass: HomeAssistant, tool_input: ToolInput, llm_context: LLMContext) -> dict[str, Any]:
        del llm_context
        manager = _timer_manager(hass); timer = _select_timer(manager, tool_input.tool_args["device_id"], tool_input.tool_args.get("name"))
        manager.cancel_timer(timer.id)
        return {"success": True, "stopped": _status(timer)}


class RenameTimer(_DeviceTimerTool):
    name = "voice_satellite__RenameTimer"
    description = "Rename a timer on the current Voice Satellite."
    parameters = vol.Schema({**_DeviceTimerTool._device, vol.Required("name"): str, vol.Required("new_name"): vol.All(str, vol.Length(min=1, max=256))})

    async def async_call(self, hass: HomeAssistant, tool_input: ToolInput, llm_context: LLMContext) -> dict[str, Any]:
        del llm_context
        manager = _timer_manager(hass); arguments = tool_input.tool_args; timer = _select_timer(manager, arguments["device_id"], arguments["name"])
        timer.name = arguments["new_name"]; timer.updated_at = time.monotonic_ns()
        if timer.device_id in manager.handlers:
            manager.handlers[timer.device_id](TimerEventType.UPDATED, timer)
        return {"success": True, "timer": _status(timer)}


class _AdjustTimer(_DeviceTimerTool):
    direction = 1
    parameters = vol.Schema({**_DeviceTimerTool._device, vol.Optional("name"): str, vol.Optional("hours", default=0): vol.All(vol.Coerce(int), vol.Range(min=0, max=23)), vol.Optional("minutes", default=0): vol.All(vol.Coerce(int), vol.Range(min=0, max=59)), vol.Optional("seconds", default=0): vol.All(vol.Coerce(int), vol.Range(min=0, max=59))})

    async def async_call(self, hass: HomeAssistant, tool_input: ToolInput, llm_context: LLMContext) -> dict[str, Any]:
        del llm_context
        manager = _timer_manager(hass); arguments = tool_input.tool_args; timer = _select_timer(manager, arguments["device_id"], arguments.get("name")); delta = _duration(arguments)
        if self.direction < 0 and delta >= timer.seconds_left:
            raise HomeAssistantError("Timer was not shortened because the requested reduction would leave no time remaining.")
        manager.add_time(timer.id, self.direction * delta)
        return {"success": True, "relative_seconds": self.direction * delta, "timer": _status(timer)}


class ExtendTimer(_AdjustTimer):
    name = "voice_satellite__ExtendTimer"
    description = "Extend a timer by a relative duration on the current Voice Satellite."
    direction = 1


class ShortenTimer(_AdjustTimer):
    name = "voice_satellite__ShortenTimer"
    description = "Shorten a timer by a relative duration on the current Voice Satellite."
    direction = -1


class GetTimerStatus(_DeviceTimerTool):
    name = "voice_satellite__GetTimerStatus"
    description = "Read one timer, or all active timers, on the current Voice Satellite."
    parameters = vol.Schema({**_DeviceTimerTool._device, vol.Optional("name"): str})

    async def async_call(self, hass: HomeAssistant, tool_input: ToolInput, llm_context: LLMContext) -> dict[str, Any]:
        del llm_context
        manager = _timer_manager(hass); arguments = tool_input.tool_args
        if arguments.get("name"):
            return {"timers": [_status(_select_timer(manager, arguments["device_id"], arguments["name"]))]}
        return {"timers": [_status(timer) for timer in manager.timers.values() if timer.device_id == arguments["device_id"]]}


def async_get_tools(hass: HomeAssistant, llm_context: LLMContext, api_id: str) -> llm.LLMTools | None:
    del hass, llm_context
    if api_id != "assist":
        return None
    return llm.LLMTools(tools=[StartTimer(), StopTimer(), RenameTimer(), ExtendTimer(), ShortenTimer(), GetTimerStatus()], prompt="Use Voice Satellite timer tools only for timers on the current Voice Satellite. Stop cancels a timer. Extend and shorten are relative changes, never absolute durations. Request a timer name whenever multiple timers could be ambiguous.")
