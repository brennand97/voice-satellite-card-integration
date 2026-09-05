# External Transport Protocol v1

This protocol connects Voice Satellite to a provider-neutral external conversation service.

- Connection: authenticated WebSocket.
- Client control/events: JSON text frames.
- Input audio: binary PCM16 little-endian, 16 kHz mono.
- The first message is `session.start` with `protocol_version: 1`.
- The server must reply `session.ready` with the same `session_id` before audio is sent.

## Start

```json
{"type":"session.start","protocol_version":1,"session_id":"...","satellite":{"entity_id":"assist_satellite.kitchen","name":"Kitchen"},"audio":{"encoding":"pcm_s16le","sample_rate":16000,"channels":1},"conversation":{"id":null,"wake_word":"Okay Nabu"}}
```

## Client controls

```json
{"type":"input.end"}
{"type":"session.cancel","reason":"user_cancelled"}
```

## Server events

Supported v1 events are `user.transcript.partial`, `user.transcript.final`, `assistant.response_started`, `assistant.text.delta`, `assistant.text.final`, `assistant.audio`, `assistant.interrupted`, `assistant.response_finished`, `session.finished`, and `error`.

`assistant.audio` must supply a short-lived URL and content type. The URL must not contain a reusable credential.
