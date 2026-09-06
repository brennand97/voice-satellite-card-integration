# External Transport Persistent Turns — Low-Level Update Plan

## 1. Purpose and constraints

This plan updates Voice Satellite for the development-stage External Transport
Protocol v1 implemented by `pipecat-external-voice-transport` commit `f6de357`.
The server now keeps one provider conversation alive across correlated audio and
text turns.

The implementation must satisfy two equally important goals:

> Follow-up note: physical Kiosk testing exposed distinct provider-generation,
> local-playback, visualization, and terminal-state lifecycles. The focused
> failing-test-first correction is specified in
> `external-transport-client-lifecycle-plan.md` and supersedes this document's
> frontend lifecycle details where they differ.

1. Support persistent turns, client text, response correlation, and server-owned
   barge-in correctly.
2. Keep the fork inexpensive to synchronize with upstream Voice Satellite.

The second goal determines the implementation shape. External behavior belongs
under `custom_components/voice_satellite/external_transport/`; shared Python and
frontend files receive only small selection, dispatch, and teardown hooks. The
Home Assistant Assist path, browser PCM path, Kiosk Satellite native PCM/pre-roll
path, and existing frontend pipeline state machine must retain their current
behavior when External Transport is not selected.

## 2. Protocol contract to adopt

The canonical v1 document will be updated only when implementation and contract
tests land together. The client must support:

```json
{"type":"turn.start","turn_id":"<uuid>","input":"audio"}
{"type":"input.text","turn_id":"<uuid>","text":"Turn on the lights"}
{"type":"turn.end","turn_id":"<uuid>"}
{"type":"response.cancel","response_id":"<server-id>","reason":"playback_stopped"}
{"type":"session.cancel","reason":"client_closed"}
```

Every assistant response event has `turn_id` and `response_id`. Transcripts have
`turn_id` and `source`, where source is `provider_audio` or `client_text`.

The client does not decide whether input interrupts a response:

- Text is definite intent; submitting it causes the server to interrupt active
  output.
- Audio bytes alone do not interrupt. The server/provider VAD must emit genuine
  speech before output is cancelled.
- `response.cancel` is non-terminal. `session.cancel` is terminal.

## 3. Target architecture

### 3.1 Ownership

Add one long-lived `ExternalConversationRuntime` per `VoiceSatelliteEntity`.
The runtime owns exactly one external WebSocket/provider conversation and
survives individual `voice_satellite/run_pipeline` subscriptions.

```text
VoiceSatelliteEntity
  `-- ExternalConversationRuntime (only when External is selected)
        |-- ExternalTransportClient (one external WebSocket)
        |-- external event-reader task
        |-- serialized external-send lock
        |-- current frontend binding
        |-- current input turn
        |-- active response lease
        `-- bounded PCM sender / idle-close tasks
```

A frontend pipeline subscription remains a presentation/capture binding, not the
owner of the provider conversation. Unsubscribing a run detaches that binding;
it does not automatically discard provider context. Entity unload, integration
reload, transport-selection change, external failure, or session idle expiry
closes the runtime terminally.

### 3.2 Why this minimizes upstream conflicts

Do not make `PipelineManager` understand the external protocol. Do not add
OpenAI/Pipecat concepts to Voice Satellite. Do not fork the normal Assist
pipeline implementation.

The isolated runtime presents existing synthetic pipeline events to the card.
Only one new frontend event (`external-audio-start`) is required because mapping
a streaming external URL to existing `tts-end` would invoke
`PipelineManager.restart()` too early and tear down barge-in capture.

Expected shared-file edits:

- `custom_components/voice_satellite/assist_satellite.py`: lazy runtime accessor,
  two delegation methods, and one teardown call.
- `custom_components/voice_satellite/__init__.py`: route External text requests
  instead of rejecting them; retain the existing External audio branch.
- `src/session/events.js`: one `external-audio-start` dispatch case.
- No behavioral edits to `src/audio/index.js`, `src/tts/index.js`,
  `src/pipeline/index.js`, or `src/pipeline/kiosk-transport.js` unless testing
  demonstrates an unavoidable integration seam.

## 4. New Python modules

### 4.1 `external_transport/models.py`

Define provider-neutral immutable records and enums:

```python
class InputKind(StrEnum):
    AUDIO = "audio"
    TEXT = "text"

class InputTurnState(StrEnum):
    OPEN = "open"
    ENDED = "ended"

@dataclass(frozen=True, slots=True)
class InputTurn:
    turn_id: str
    kind: InputKind
    state: InputTurnState
    speculative: bool = False

@dataclass(frozen=True, slots=True)
class ResponseLease:
    response_id: str
    turn_id: str

@dataclass(slots=True)
class FrontendBinding:
    generation: int
    connection: Any
    msg_id: int
    input_kind: InputKind
    audio_queue: asyncio.Queue[bytes] | None
```

`FrontendBinding` is the only object allowed to call `connection.send_event`.
Every send verifies that its generation is still current. A stale response may
be consumed for protocol cleanup but must not paint a newer frontend run.

### 4.2 `external_transport/runtime.py`

Implement `ExternalConversationRuntime` as the sole mutable owner. Constructor:

```python
ExternalConversationRuntime(
    *,
    http_session: ClientSession,
    url: str,
    token: str,
    verify_tls: bool,
    ready_timeout: float,
    session_id: str,
    satellite_entity_id: str,
    satellite_name: str,
    conversation_id: str | None,
    wake_word: str | None,
    idle_timeout: float,
)
```

Internal state:

```python
_client: ExternalTransportClient | None
_connect_lock: asyncio.Lock
_send_lock: asyncio.Lock
_binding_lock: asyncio.Lock
_binding: FrontendBinding | None
_binding_generation: int
_input_turn: InputTurn | None
_active_response: ResponseLease | None
_event_task: asyncio.Task | None
_audio_task: asyncio.Task | None
_idle_close_task: asyncio.Task | None
_closed: bool
_failure: Exception | None
```

All public operations have bounded waits. No queue get, task cancellation,
client close, or external send may block entity teardown indefinitely.

Public API:

```python
async def attach_audio(
    audio_queue, connection, msg_id, *, wake_word: str | None
) -> None

async def attach_text(
    text, connection, msg_id
) -> None

async def detach(generation: int, reason: str) -> None

async def close(reason: str) -> None
```

`attach_audio` and `attach_text` run for the lifetime of one frontend
subscription. They return after that binding receives terminal `run-end`/error
or is displaced, while the external runtime may remain connected.

### 4.3 `external_transport/client.py`

Keep this class as a low-level protocol connection, not a UI/session owner.
Replace its single-turn assumptions with these methods:

```python
async def connect() -> ServerCapabilities
async def start_turn(turn_id: str, kind: InputKind) -> None
async def write_audio(turn_id: str, pcm: bytes) -> None
async def write_text(turn_id: str, text: str) -> None
async def end_turn(turn_id: str) -> None
async def cancel_response(response_id: str, reason: str) -> None
async def cancel_session(reason: str) -> None
async def events() -> AsyncIterator[ServerEvent]
async def close() -> None
```

Implementation rules:

- Use one internal send lock so PCM, turn controls, and cancellation cannot
  interleave incorrectly on aiohttp's WebSocket.
- Track the open input turn locally and fail before sending a second open turn,
  mismatched modality, duplicate text, or wrong `turn.end`.
- Require `text_input`, `interruptions`, and `conversation_continuation`
  capability flags before using those features.
- `events()` has exactly one consumer: `ExternalConversationRuntime`.
- Closing the aiohttp object without `session.cancel` is reserved for broken
  network/error paths.

### 4.4 `external_transport/protocol.py`

Add typed validation rather than passing arbitrary dictionaries into runtime:

```python
@dataclass(frozen=True, slots=True)
class ServerCapabilities: ...
@dataclass(frozen=True, slots=True)
class ServerEvent:
    type: str
    session_id: str
    turn_id: str | None
    response_id: str | None
    text: str | None
    source: str | None
    url: str | None
    content_type: str | None
```

Validation requirements:

- Every event must match the connected `session_id`.
- Assistant lifecycle/text/audio events require non-empty `turn_id` and
  `response_id`.
- `assistant.audio` requires HTTPS in configured deployments, non-empty URL,
  and `audio/wav` for the current server implementation.
- Transcript events require `turn_id`, text, and a recognized source.
- `assistant.interrupted` and `assistant.response_finished` must identify the
  response they terminate.
- Unknown event types remain safely ignorable only if they cannot mutate local
  lifecycle. Unknown required lifecycle controls fail closed.

`normalize_event()` keeps correlation metadata in synthetic pipeline data:

```python
{"type": "stt-end", "data": {
    "stt_output": {"text": text},
    "external": {"turn_id": turn_id, "source": source},
}}

{"type": "intent-start", "data": {
    "external": {"turn_id": turn_id, "response_id": response_id},
}}

{"type": "external-audio-start", "data": {
    "url": url,
    "content_type": content_type,
    "external": {"turn_id": turn_id, "response_id": response_id},
}}

{"type": "external-interrupted", "data": {
    "external": {"turn_id": turn_id, "response_id": response_id},
}}

{"type": "run-end", "data": {
    "external": {"turn_id": turn_id, "response_id": response_id},
}}
```

Do not map `assistant.audio` to `tts-end`: `handleTtsEnd()` restarts the normal
pipeline immediately, which would close the current capture path while response
audio is still playing.

## 5. Runtime sequencing

### 5.1 Connection establishment

On the first External run:

1. Build `SessionStart` and connect once.
2. Validate `session.ready`, session ID, and required capabilities.
3. Start the single external event-reader task.
4. Install the frontend binding and emit synthetic `run-start`.
5. Start the requested turn.

Concurrent first calls join `_connect_lock`; they never open two external
WebSockets. Failed connection state is cleared before a bounded retry.

### 5.2 Initial audio turn

`attach_audio`:

1. Displace/detach any older frontend binding using the existing generation
   semantics.
2. Generate `turn_id = uuid.uuid4()` in Home Assistant.
3. Send `turn.start(input=audio)` before draining queued pre-roll.
4. Start one bounded audio-forwarder task.
5. Forward the queue byte-for-byte and in order. Preserve the existing 64-frame
   pre-ready bound from `ws_run_pipeline`.
6. Do not locally classify PCM as speech and do not issue an interruption flag.

Kiosk Satellite's native PCM/pre-roll therefore retains its exact current path:
app native capture -> HA binary handler -> bounded queue -> runtime -> external
WebSocket. No decoding, copying to JSON, resampling, or browser audio processing
is introduced.

### 5.3 Turn boundary and speculative barge-in turn

The server/provider VAD can start a response while the current audio turn is
still receiving PCM. When `assistant.response_started(turn N, response R)`
arrives:

1. Record `ResponseLease(R, N)`.
2. Send `turn.end(N)` if N is still open.
3. Immediately open a new speculative audio turn N+1 when the active binding is
   audio-capable.
4. Keep the same frontend binary handler and audio-forwarder running; subsequent
   PCM belongs to N+1.
5. Emit normalized `intent-start` for R.

This is the core barge-in behavior. It keeps capture live through playback but
does not treat playback echo or silence as interruption. Only the server's
`user.speech_started(N+1)` event advances the UI to STT and causes the server to
interrupt R.

Use `_send_lock` around steps 2–3 so no queued PCM is sent between `turn.end(N)`
and `turn.start(N+1)`. The audio sender waits for the new turn assignment before
continuing.

### 5.4 Streaming response audio

On `assistant.audio(R)`:

1. Verify R matches `_active_response`; otherwise ignore it as stale.
2. Emit `external-audio-start`, not `tts-end`.
3. Frontend dispatch sets `State.TTS` and calls `session.tts.play(url)`.
4. Existing `TTSManager` selects Kiosk native streaming playback when available
   and browser playback otherwise.
5. The PCM sender remains active during playback for provider-side barge-in.

Only the event name is new. The actual native/browser playback implementation,
volume handling, analyser integration, safety timers, and completion callback
remain unchanged.

### 5.5 Audio barge-in

On `user.speech_started(N+1)` while R is active:

1. The server has already made the interruption decision.
2. Normalize it to `stt-vad-start` for logging/state consistency if useful.
3. On `assistant.interrupted(R)`, verify R matches the active response.
4. Emit `external-interrupted` with R.
5. The existing frontend hook stops TTS and sets STT state.
6. Clear only R's response lease; keep N+1 and its PCM sender alive.
7. Ignore late text/audio/finished events for R by response ID.
8. When the provider starts the N+1 response, repeat the response-start
   boundary and open the next speculative input turn.

### 5.6 Text turn

`attach_text` must use the same runtime and provider conversation:

1. End any open speculative audio turn under `_send_lock`.
2. Generate a new text `turn_id`.
3. Send `turn.start(input=text)`, `input.text`, and `turn.end` in order.
4. The server interrupts any active response as policy; Voice Satellite sends no
   interruption flag.
5. Route the server's echoed `user.transcript.final(source=client_text)` through
   existing `stt-end` UI handling.
6. Route the new response to the text run's frontend binding.

In `custom_components/voice_satellite/__init__.py`, replace only the current
`external_transport_text_unsupported` branch with a background call to
`entity.async_run_external_transport_text(...)`. Keep the normal
`async_run_pipeline_text()` path untouched.

### 5.7 Response completion and playback completion

`assistant.response_finished(R)` means generation ended, not necessarily that
the tablet finished playing buffered WAV data.

- Emit `run-end` using current frontend semantics. Existing code already defers
  final UI cleanup while `tts.isPlaying`.
- Keep the speculative barge-in turn and PCM sender alive until actual TTS
  completion/unsubscribe.
- When the frontend eventually unsubscribes after playback completion, detach
  the binding and end a still-open speculative turn.
- If an active response still exists when a binding is explicitly stopped,
  issue `response.cancel(R, reason=playback_stopped)` before detaching.
- Do not send `session.cancel`; provider context remains available for the next
  run until runtime idle expiry.

### 5.8 Session lifetime

Use the satellite's existing Session Duration setting as the external runtime
idle-retention policy where practical, capped below the external server's
maximum session duration. A safe first implementation may use:

- close immediately on entity removal, integration reload, transport switch,
  authentication/protocol failure, or HA shutdown;
- retain after a normal turn only while a frontend binding or playback exists;
- otherwise arm a bounded idle-close timer;
- reconnect with a new `session_id` after server `session.finished`, transport
  loss, or idle expiry.

Never silently replay a failed External turn through Home Assistant Assist.

## 6. Integration points with minimal shared changes

### 6.1 `assist_satellite.py`

Add:

```python
self._external_runtime: ExternalConversationRuntime | None = None
self._external_runtime_lock = asyncio.Lock()
```

Add private `_async_get_external_runtime(...)` and public delegators:

```python
async_run_external_transport(...)
async_run_external_transport_text(...)
```

Move current connection/config setup out of
`async_run_external_transport()` into the runtime factory. The entity methods
should contain no protocol event loop.

In `async_will_remove_from_hass()`, close `_external_runtime` with a fixed timeout
and clear it. This is the only normal lifecycle edit outside the existing
External method region.

### 6.2 `__init__.py`

Keep the current audio queue and binary-handler implementation. Make two narrow
changes:

1. External text requests invoke `async_run_external_transport_text` instead of
   returning `external_transport_text_unsupported`.
2. External unsubscribe continues placing the empty-byte terminal marker. The
   runtime interprets this as binding/turn completion, not terminal provider
   session cancellation.

Do not alter schemas or behavior for normal Home Assistant pipeline requests.

### 6.3 `src/session/events.js`

Add one case:

```javascript
case 'external-audio-start':
  if (!session.pipeline.acceptExternalResponse(eventData.external)) break;
  setState(session, State.TTS);
  session.tts.play(eventData.url);
  break;
```

Correlation can be isolated in a tiny `src/pipeline/external-state.js` helper or
inside `PipelineManager` with three fields and methods:

```javascript
_externalTurnId
_externalResponseId
acceptExternalResponse(meta)
clearExternalResponse(meta)
```

Prefer the helper to minimize churn in upstream `pipeline/index.js`. Existing
`external-interrupted` must check response correlation before stopping playback;
a stale interruption cannot stop a newer response.

No changes to `TTSManager` are planned. If testing proves correlation must live
with playback ownership, add only an optional opaque owner token to `play()` and
`stop(owner)`, leaving all callers without a token unchanged.

### 6.4 Kiosk Satellite delegated transport

Do not change Kiosk APIs in the first implementation. A delegated pipeline run
already keeps one run ID, binary handler, native microphone sender, and event
subscription alive until unsubscribe. Avoiding the premature `tts-end` mapping
allows that existing run to remain active through playback.

Verify explicitly that `pipelineStopSending()` is not called at external
response generation time. It should occur only on actual frontend run cleanup,
visibility pause, mute, displacement, or failure.

## 7. Failure and race handling

### 7.1 Required invariants

- Exactly one external event reader per runtime.
- Exactly one open input turn at a time.
- At most one active response lease.
- At most one current frontend binding; generation checks fence stale sends.
- Every output event is accepted only when `(turn_id, response_id)` matches.
- PCM is sent only while an audio turn is open.
- Text is sent exactly once and never through the PCM queue.
- Terminal close releases the client, event task, audio task, idle timer, and
  binding.

### 7.2 Displacement

A newer frontend run increments binding generation and sends the existing
`displaced` event to the old subscriber. It does not open a second external
WebSocket. If the new run is text, end the speculative audio turn before sending
text. If it is audio, either adopt the existing speculative turn or close it and
open a fresh turn deterministically; do not send two `turn.start` controls.

### 7.3 External disconnect

The event reader records one failure, emits a single normalized error/run-end to
the current binding, closes all tasks, and clears the entity's runtime reference.
The next user interaction creates a new session. No automatic Assist fallback is
allowed after any input was sent.

### 7.4 Queue pressure

- Retain the current 64-frame pre-ready audio queue.
- Runtime forwarding must await external WebSocket backpressure with a timeout.
- Queue overflow remains a visible run error, not only a warning; otherwise a
  clipped command could execute incorrectly.
- Terminal markers take precedence over queued PCM by dropping one stale chunk
  when necessary, matching current behavior.

### 7.5 Visibility, mute, and teardown

Visibility pause, mute, entity removal, and integration reload must end any open
turn, cancel an active response if present, and close or idle the runtime according
to whether the operation is terminal. Entity removal/reload is always terminal.
All waits are bounded and cancellation exceptions are consumed.

## 8. Test plan

### 8.1 Protocol unit tests

Expand `tests/test_external_transport_protocol.py` to cover:

- readiness capability validation;
- explicit audio and text control shapes;
- session/turn/response ID requirements;
- transcript source validation;
- text byte limits and duplicate text rejection;
- `assistant.audio` URL/content type validation;
- preservation of correlation metadata through normalization;
- unknown and stale lifecycle events failing closed.

### 8.2 Client unit tests

Using the existing fake aiohttp WebSocket:

- connect once and execute two sequential turns;
- verify exact JSON/binary ordering;
- prove `turn.end` precedes the next `turn.start`;
- prove text sends start/text/end without binary data;
- prove `response.cancel` does not close the WebSocket;
- prove terminal cancellation does;
- reject duplicate/open/mismatched turns locally;
- bound connect, send, receive, and close behavior.

### 8.3 Runtime unit tests

Create `tests/test_external_transport_runtime.py` with an injected fake client:

1. Initial audio opens before pre-roll is drained.
2. PCM ordering is byte-for-byte stable.
3. Response start ends turn N and opens speculative N+1 atomically.
4. PCM during playback is assigned to N+1.
5. Audio bytes do not interrupt a response.
6. Server speech/interruption events stop only matching response R.
7. Late audio/text/finish for R is ignored after response R+1 begins.
8. Text ends speculative audio, sends text, and receives echoed transcript.
9. `response.cancel` keeps the external session reusable.
10. Frontend unsubscribe ends the turn but retains provider context.
11. Displacement fences the old binding.
12. External disconnect emits one error and releases every task.
13. Full queues and blocked client calls terminate within test timeouts.

### 8.4 WebSocket-handler integration tests

Test `ws_run_pipeline` with fake entity/runtime boundaries:

- External audio still receives the same bounded binary handler and `init` event.
- External text routes to the runtime and has `handler_id: null`.
- Home Assistant transport uses the original code paths unchanged.
- Unsubscribe sends a terminal queue marker without terminally closing the
  persistent external runtime.

### 8.5 Frontend tests

Add Node's built-in test runner rather than a new framework:

```json
"test": "node --test tests/js/*.test.mjs"
```

Test the isolated external response-correlation helper:

- matching audio starts playback;
- stale audio is ignored;
- matching interruption stops playback;
- stale interruption cannot stop a newer response;
- response finish clears only its own lease.

Production Webpack build remains a required validation step.

### 8.6 Cross-repository contract fixtures

Copy immutable JSON fixture sequences from the server repository with a recorded
source commit and checksum. Cover:

- two sequential audio turns;
- audio response plus speculative barge turn;
- text interruption;
- non-terminal response cancellation;
- stale event sequence;
- terminal session cancellation.

Do not add a runtime dependency between repositories.

### 8.7 Hardware acceptance

On Kiosk Satellite:

- Native pre-roll remains first and unclipped.
- Signed WAV begins playing before generation completes.
- Native microphone upload continues during playback.
- Speaker echo and silence do not interrupt.
- Genuine speech stops native playback promptly and produces a new response.
- Two or more turns retain provider context.
- Text input interrupts and displays its echoed transcript.
- Screen-off wake, screensaver suppression, volume, mute, stop word, and wake
  re-arming retain current behavior.

## 9. Implementation sequence

Implement in reviewable commits that keep non-External behavior passing:

1. **Typed protocol/client:** capability and correlation validation plus
   persistent low-level client tests.
2. **External runtime:** isolated session/binding/turn actor with fake-client
   tests.
3. **Entity integration:** delegate existing External audio method and add the
   External text method; add bounded teardown.
4. **WebSocket routing:** replace text rejection and preserve existing audio
   handler behavior.
5. **Minimal frontend hook:** `external-audio-start` and response-correlation
   guard; production build and Node tests.
6. **Contract validation:** copied server fixtures and full Python/JS build.
7. **Canonical protocol update:** replace the old single-turn v1 documentation
   only after both code paths pass.
8. **Hardware validation and release:** Kiosk Satellite acceptance, HACS ZIP,
   diagnostics check, and fork release notes.

## 10. Completion criteria

The update is complete when:

- one external WebSocket survives at least two audio/text turns;
- the normal Home Assistant transport has no changed behavior;
- Kiosk native PCM/pre-roll remains unmodified;
- response audio no longer triggers premature pipeline restart;
- audio barge-in is decided only by the server/provider;
- text interrupts immediately without a client policy flag;
- stale response events cannot affect current playback or UI;
- all queues/tasks/sessions close within bounded time on every tested terminal
  path;
- Python tests, Node tests, production Webpack build, copied protocol fixtures,
  HACS validation, and target-device acceptance all pass.
