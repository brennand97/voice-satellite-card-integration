# External Transport Protocol v1

This development-stage protocol connects Voice Satellite to a provider-neutral
persistent conversation service.

- Connection: authenticated WebSocket.
- Controls/events: JSON text frames.
- Audio: binary PCM16 little-endian, 16 kHz mono.
- The first message is `session.start` with `protocol_version: 1`.
- The server replies `session.ready` with the same `session_id` and capabilities
  before a turn starts.

## Start

```json
{"type":"session.start","protocol_version":1,"session_id":"...","satellite":{"entity_id":"assist_satellite.kitchen","name":"Kitchen"},"audio":{"encoding":"pcm_s16le","sample_rate":16000,"channels":1},"conversation":{"id":null,"wake_word":"Okay Nabu"}}
```

`session.ready.capabilities` must include true values for `transcription`,
`text_input`, `streaming_audio_url`, `interruptions`, and
`conversation_continuation`.

## Turns and cancellation

Every turn has a client-generated unique `turn_id`:

```json
{"type":"turn.start","turn_id":"...","input":"audio"}
{"type":"turn.end","turn_id":"..."}
{"type":"turn.start","turn_id":"...","input":"text"}
{"type":"input.text","turn_id":"...","text":"Turn on the lights"}
{"type":"turn.end","turn_id":"..."}
{"type":"response.cancel","response_id":"...","reason":"playback_stopped"}
{"type":"session.cancel","reason":"client_closed"}
```

`response.cancel` is non-terminal; `session.cancel` closes the provider
conversation. The server decides whether audio input interrupts an active
response after provider/VAD speech detection. Text is definite input and may
interrupt immediately.

## Server events

All assistant lifecycle/audio events include `session_id`, `turn_id`, and
`response_id`. Transcript events include `session_id`, `turn_id`, `text`, and
`source` (`provider_audio` or `client_text`). Supported events are:

- `user.speech_started`, `user.transcript.partial`, `user.transcript.final`
- `assistant.response_started`, `assistant.text.delta`, `assistant.text.final`
- `assistant.audio`, `assistant.interrupted`, `assistant.response_finished`
- `session.finished`, `error`

`assistant.audio` supplies a short-lived `audio/wav` URL. It must not contain a
reusable credential. Clients fence late events whose response ID is no longer
active.
