"""Shared, lease-based nearby-media guard for Voice Satellites.

This is media control, not acoustic echo cancellation: it has no playback PCM
reference.  It deliberately controls only explicitly configured player IDs.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Final, Literal
from uuid import uuid4

from homeassistant.components.media_player.const import ATTR_MEDIA_ARTIST, ATTR_MEDIA_CONTENT_ID, ATTR_MEDIA_TITLE
from homeassistant.const import ATTR_ENTITY_ID, STATE_OFF, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Context, Event, HomeAssistant, State, callback
from homeassistant.helpers.event import async_track_state_change_event

_ACTION_OFF: Final = "off"
_ACTION_DUCK: Final = "duck"
_ACTION_PAUSE: Final = "pause"
_PLAYING: Final = "playing"


@dataclass(frozen=True, slots=True)
class GuardPolicy:
    """Trusted per-Satellite nearby-player behavior."""

    action: Literal["off", "duck", "pause"]
    volume: float
    restore_delay: float


@dataclass(frozen=True, slots=True)
class GuardLease:
    """An acquired interaction lease."""

    lease_id: str
    owner_entry_id: str
    entity_ids: tuple[str, ...]
    policy: GuardPolicy


@dataclass(slots=True)
class _PlayerControl:
    holders: dict[str, GuardLease] = field(default_factory=dict)
    intended_volume: float | None = None
    token: tuple[object, ...] | None = None
    paused_by_guard: bool = False
    invalidated: bool = False
    contexts: set[str] = field(default_factory=set)
    unsub: callable | None = None
    task: asyncio.Task[None] | None = None
    restore_task: asyncio.Task[None] | None = None


def _volume(state: State | None) -> float | None:
    if state is None:
        return None
    value = state.attributes.get("volume_level")
    return float(value) if isinstance(value, (int, float)) and 0 <= value <= 1 else None


def _token(state: State | None) -> tuple[object, ...] | None:
    if state is None:
        return None
    result = tuple(state.attributes.get(key) for key in (ATTR_MEDIA_CONTENT_ID, ATTR_MEDIA_TITLE, ATTR_MEDIA_ARTIST, "source"))
    return result if any(value is not None for value in result) else None


class MediaInterferenceCoordinator:
    """Coordinate duck/pause ownership across all Voice Satellite entries."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._players: dict[str, _PlayerControl] = {}
        self._closed = False

    async def acquire(self, owner_entry_id: str, entity_ids: tuple[str, ...], policy: GuardPolicy) -> GuardLease:
        """Register an inactive lease. Calling activate performs media I/O."""
        entity_ids = tuple(dict.fromkeys(entity_ids))
        return GuardLease(uuid4().hex, owner_entry_id, entity_ids, policy)

    async def activate(self, lease: GuardLease) -> None:
        """Apply a lease to currently playing players and track future playback."""
        if self._closed or lease.policy.action == _ACTION_OFF:
            return
        for entity_id in lease.entity_ids:
            control = self._players.setdefault(entity_id, _PlayerControl())
            control.holders[lease.lease_id] = lease
            if control.restore_task:
                control.restore_task.cancel()
                control.restore_task = None
            if control.unsub is None:
                control.unsub = async_track_state_change_event(self.hass, [entity_id], self._on_state_change)
            state = self.hass.states.get(entity_id)
            if state and state.state == _PLAYING:
                self._capture_playing(control, state)
                self._schedule(entity_id, control)

    async def release(self, lease: GuardLease) -> None:
        """Release lease; restore only after the final holder leaves."""
        for entity_id in lease.entity_ids:
            control = self._players.get(entity_id)
            if control is None:
                continue
            control.holders.pop(lease.lease_id, None)
            if control.holders:
                self._schedule(entity_id, control)
                continue
            delay = max((holder.policy.restore_delay for holder in [lease]), default=0.0)
            control.restore_task = self.hass.async_create_task(self._restore_later(entity_id, control, delay))

    async def close(self) -> None:
        """Cancel timers/listeners and best-effort restore outstanding controls."""
        self._closed = True
        for entity_id, control in list(self._players.items()):
            if control.task:
                control.task.cancel()
            if control.restore_task:
                control.restore_task.cancel()
            if control.unsub:
                control.unsub()
            await self._restore(entity_id, control)
        self._players.clear()

    @callback
    def _on_state_change(self, event: Event) -> None:
        entity_id = event.data["entity_id"]
        control = self._players.get(entity_id)
        if control is None:
            return
        new_state: State | None = event.data.get("new_state")
        context_id = new_state.context.id if new_state else None
        if context_id in control.contexts:
            control.contexts.discard(context_id)
            return
        if new_state is None or new_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            control.invalidated = True
            return
        if new_state.state in (STATE_OFF, "idle"):
            control.invalidated = True
            control.paused_by_guard = False
            return
        if new_state.state == _PLAYING:
            current_token = _token(new_state)
            if control.token is not None and current_token is not None and current_token != control.token:
                # A new user/agent item must never receive a stale restoration.
                control.invalidated = True
                control.paused_by_guard = False
            self._capture_playing(control, new_state)
            self._schedule(entity_id, control)

    def _capture_playing(self, control: _PlayerControl, state: State) -> None:
        value = _volume(state)
        if value is not None:
            control.intended_volume = value
        control.token = _token(state)

    def _effective(self, control: _PlayerControl) -> tuple[str, float | None]:
        leases = tuple(control.holders.values())
        if any(lease.policy.action == _ACTION_PAUSE for lease in leases):
            return _ACTION_PAUSE, None
        ducks = [lease.policy.volume for lease in leases if lease.policy.action == _ACTION_DUCK]
        return (_ACTION_DUCK, min(ducks)) if ducks else (_ACTION_OFF, None)

    def _schedule(self, entity_id: str, control: _PlayerControl) -> None:
        if control.task is None or control.task.done():
            control.task = self.hass.async_create_task(self._apply(entity_id, control))

    async def _apply(self, entity_id: str, control: _PlayerControl) -> None:
        action, ceiling = self._effective(control)
        state = self.hass.states.get(entity_id)
        if state is None or state.state != _PLAYING:
            return
        if action == _ACTION_PAUSE:
            await self._call(entity_id, control, "media_pause")
            control.paused_by_guard = True
            return
        if action == _ACTION_DUCK and ceiling is not None:
            actual = _volume(state)
            if actual is not None and actual > ceiling:
                # Intended volume is only captured from non-guard state changes.
                await self._call(entity_id, control, "volume_set", {"volume_level": ceiling})

    async def _restore_later(self, entity_id: str, control: _PlayerControl, delay: float) -> None:
        try:
            if delay:
                await asyncio.sleep(delay)
            if control.holders:
                return
            await self._restore(entity_id, control)
        finally:
            if not control.holders:
                if control.unsub:
                    control.unsub()
                self._players.pop(entity_id, None)

    async def _restore(self, entity_id: str, control: _PlayerControl) -> None:
        if control.invalidated:
            return
        state = self.hass.states.get(entity_id)
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN, STATE_OFF, "idle"):
            return
        if control.paused_by_guard and state.state != _PLAYING:
            await self._call(entity_id, control, "media_play")
        if control.intended_volume is not None and _volume(state) != control.intended_volume:
            await self._call(entity_id, control, "volume_set", {"volume_level": control.intended_volume})

    async def _call(self, entity_id: str, control: _PlayerControl, service: str, data: dict | None = None) -> None:
        context = Context()
        control.contexts.add(context.id)
        try:
            await self.hass.services.async_call("media_player", service, {ATTR_ENTITY_ID: entity_id, **(data or {})}, blocking=True, context=context)
        except Exception:  # a player failure must not fail the voice pipeline
            control.contexts.discard(context.id)
