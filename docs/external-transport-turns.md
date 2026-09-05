# External Transport Persistent Turns (Development Design)

The current `external-transport-protocol.md` describes the released-in-code
single-turn draft. Before enabling persistent conversation sessions, Voice
Satellite and the external server will make one coordinated development-v1
update.

The target protocol introduces explicit audio/text turns (`turn.start`,
`input.text`, `turn.end`), non-terminal response cancellation
(`response.cancel`), and `turn_id`/`response_id` correlation on server events.
The server owns interruption: text is immediate intent, while audio interrupts
only after server/provider VAD identifies genuine speech. Clients must not send
an interruption decision flag.

Voice Satellite implementation work required with that protocol update:

1. Generate unique turn IDs and send explicit audio turns instead of relying on
   the implicit initial turn.
2. Add a caller-facing text-turn path that sends `input.text` and displays the
   returned `user.transcript.final` (`source: client_text`).
3. Keep the WebSocket session and event reader alive after
   `assistant.response_finished` so another turn can begin.
4. On `assistant.interrupted`, stop the native player for the matching response
   only; ignore late events/audio URLs from that response.
5. Use `response.cancel` for a user stop action and reserve `session.cancel`
   for terminal teardown.
6. Preserve the existing native PCM/pre-roll path and only route barge-in audio
   after its normal wake/VAD path; the server decides when it is genuine speech.

The full actor/lease design and acceptance criteria live in the server
repository's `docs/turn-session-architecture.md`. Do not implement a
server-only extension or alter the canonical v1 document until both sides can
ship the matching contract tests.
