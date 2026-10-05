"""Stateful player exercised through real HA media services and state events."""
from homeassistant.components.media_player import MediaPlayerEntity, MediaPlayerEntityFeature, MediaPlayerState

async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    async_add_entities([ScratchPlayer()])

class ScratchPlayer(MediaPlayerEntity):
    _attr_name = "Testbed Speaker"
    _attr_unique_id = "testbed_speaker"
    _attr_should_poll = False
    _attr_state = MediaPlayerState.PLAYING
    _attr_volume_level = 0.7
    _attr_media_content_id = "fixture-track-a"
    _attr_media_title = "Fixture music"
    _attr_supported_features = (MediaPlayerEntityFeature.VOLUME_SET | MediaPlayerEntityFeature.PAUSE | MediaPlayerEntityFeature.PLAY | MediaPlayerEntityFeature.STOP | MediaPlayerEntityFeature.PLAY_MEDIA)

    def __init__(self):
        self.history = []

    @property
    def extra_state_attributes(self):
        return {"service_history": self.history[-40:]}

    def changed(self, action, **data):
        self.history.append({"action": action, **data})
        self.async_write_ha_state()

    async def async_set_volume_level(self, volume):
        self._attr_volume_level = volume
        self.changed("volume_set", volume=volume)

    async def async_media_pause(self):
        self._attr_state = MediaPlayerState.PAUSED
        self.changed("media_pause")

    async def async_media_play(self):
        self._attr_state = MediaPlayerState.PLAYING
        self.changed("media_play")

    async def async_media_stop(self):
        self._attr_state = MediaPlayerState.IDLE
        self.changed("media_stop")

    async def async_play_media(self, media_type, media_id, **kwargs):
        self._attr_media_content_id = media_id
        self._attr_state = MediaPlayerState.PLAYING
        self.changed("play_media", media_id=media_id)
