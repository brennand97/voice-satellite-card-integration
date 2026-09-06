"""Text-only Home Assistant conversation entities backed by External Transport."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any, Literal
from uuid import uuid4

from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.const import MATCH_ALL
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    CONF_EXTERNAL_TRANSPORT_READY_TIMEOUT,
    CONF_EXTERNAL_TRANSPORT_TOKEN,
    CONF_EXTERNAL_TRANSPORT_URL,
    CONF_EXTERNAL_TRANSPORT_VERIFY_TLS,
    CONF_PROFILE_NAME,
    CONF_REQUESTED_TOOLS,
    CONF_TOOL_PROFILE,
)
from .external_transport.client import ExternalTransportClient
from .external_transport.protocol import SessionStart


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create one native HA conversation entity per profile subentry."""
    entities = [
        ExternalTransportConversationEntity(entry, subentry)
        for subentry in entry.subentries.values()
        if subentry.subentry_type == "conversation"
    ]
    if entities:
        async_add_entities(entities)


class ExternalTransportConversationEntity(conversation.ConversationEntity):
    """A device-less, text-only attachment to an External conversation profile."""

    _attr_should_poll = False
    _attr_supports_streaming = True

    def __init__(self, entry: ConfigEntry, subentry: ConfigSubentry) -> None:
        self._entry = entry
        self._subentry = subentry
        self._attr_name = subentry.title
        self._attr_unique_id = f"{entry.entry_id}_{subentry.subentry_id}"

    @property
    def supported_languages(self) -> list[str] | Literal["*"]:
        return MATCH_ALL

    async def _async_handle_message(
        self,
        user_input: conversation.ConversationInput,
        chat_log: conversation.ChatLog,
    ) -> conversation.ConversationResult:
        """Submit one bounded text turn and stream it into HA's ChatLog."""
        async for _content in chat_log.async_add_delta_content_stream(
            self.entity_id,
            self._event_deltas(user_input),
        ):
            pass
        return conversation.async_get_result_from_chat_log(user_input, chat_log)

    async def _event_deltas(
        self, user_input: conversation.ConversationInput
    ) -> AsyncIterator[conversation.AssistantContentDeltaDict]:
        options = self._subentry.data
        url = self._entry.data.get(CONF_EXTERNAL_TRANSPORT_URL, "")
        token = self._entry.data.get(CONF_EXTERNAL_TRANSPORT_TOKEN, "")
        if not isinstance(url, str) or not isinstance(token, str) or not url or not token:
            raise conversation.ConverseError("External Conversation Service is not configured")
        try:
            ready_timeout = float(
                self._entry.data.get(CONF_EXTERNAL_TRANSPORT_READY_TIMEOUT, 5)
            )
        except (TypeError, ValueError):
            ready_timeout = 5.0
        requested = options.get(CONF_REQUESTED_TOOLS)
        requested_tools = (
            tuple(requested)
            if isinstance(requested, list) and all(isinstance(name, str) for name in requested)
            else None
        )
        client = ExternalTransportClient(
            async_get_clientsession(
                self.hass,
                verify_ssl=bool(
                    self._entry.data.get(CONF_EXTERNAL_TRANSPORT_VERIFY_TLS, True)
                ),
            ),
            url,
            token,
            SessionStart(
                str(uuid4()),
                self.entity_id,
                self.name or self.entity_id,
                user_input.conversation_id,
                None,
                client_kind="ha_conversation",
                tool_profile=options.get(CONF_TOOL_PROFILE),
                requested_tools=requested_tools,
                input_modalities=("text",),
                output_modalities=("text",),
            ),
            ready_timeout,
        )
        try:
            await client.connect()
            turn_id = str(uuid4())
            await client.start_turn(turn_id, "text")
            await client.write_text(turn_id, user_input.text)
            await client.end_turn(turn_id)
            async with asyncio.timeout(60):
                async for event in client.events():
                    if event["type"] == "assistant.text.delta":
                        yield {"content": event["text"]}
                    elif event["type"] == "assistant.text.final":
                        yield {"role": "assistant", "content": event["text"]}
                    elif event["type"] == "assistant.response_finished":
                        return
                    elif event["type"] == "error":
                        raise conversation.ConverseError("External conversation failed")
        finally:
            await client.cancel_session("conversation_turn_finished")
            await client.close()
