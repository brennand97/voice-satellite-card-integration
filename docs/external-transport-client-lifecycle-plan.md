# External Transport Client Lifecycle — Low-Level Correction Plan

## 1. Scope

This plan corrects the remaining persistent-turn client behavior observed during
Kiosk Satellite testing of `v2026.9.5-fork.5`:

- Turn two reaches the provider, but microphone-driven bar animation does not
  resume after turn-one playback.
- Provider VAD detects barge-in, but already-buffered turn-one audio continues
  while turn-two audio starts, producing overlapping playback.
- External state is represented by loose booleans and generic HA pipeline states
  rather than a coherent client lifecycle.
- Earlier builds produced repeated External PCM queue-overflow warnings after
  the HA-side consumer detached.
- Terminal cleanup must still occur on explicit client stop, failure, or bounded
  post-playback inactivity.

This is an External-only correction. Normal Home Assistant Assist behavior,
Kiosk native PCM/pre-roll, TTS playback implementation, wake-word engines, and
the upstream pipeline lifecycle must remain unchanged.

No provider-specific concepts may cross the External Transport boundary.
`user.speech_started` is a provider-neutral, server-authoritative declaration of
genuine speech.

## 2. Confirmed current behavior

### 2.1 Lost microphone visualization

Kiosk delegated capture initially calls `AudioManager._startDelegatedMicrophone`,
which selects `analyser.attachExternalMic()`. Native TTS later calls
`analyser.attachExternal()`. At playback completion, `TtsManager._onComplete()`
calls `analyser.detachAudio()`, and `detachAudio()` also leaves external-level
mode.

The current External completion hook only sets the generic UI state to `STT`.
`UIManager.startReactive()` calls `analyser.reconnectMic()`, but delegated Kiosk
capture has no Web Audio mic analyser to reconnect. Native PCM continues to the
provider while the bar has no mic-level source.

### 2.2 Overlapping playback after barge-in

`assistant.response_finished` means provider generation completed; it does not
mean the Kiosk finished playing the signed WAV. The server clears its active
provider response when generation finishes. If genuine speech is detected while
the completed WAV is still playing, there may be no
`assistant.interrupted(response N)` because response N is no longer active at
the provider.

The server still emits `user.speech_started` for the new turn. The current
frontend treats that only as STT state and does not stop its local playback
lease. A later `assistant.audio(response N+1)` begins while response N is still
playing.

### 2.3 Incomplete state ownership

The frontend currently uses `_externalPersistentTurn` and
`_externalResponseId`. It conflates:

- provider generation ownership;
- local playback ownership;
- capture readiness;
- generic visual `STT` state.

The HA runtime has turn/response identifiers, but it also treats provider
response completion as the point from which to calculate inactivity. Only the
frontend knows when native/browser playback actually completes.

### 2.4 Queue-overflow evidence

Repeated HA warnings of the form:

```text
External transport audio queue overflow for 'Kitchen Tablet'
```

prove that a Kiosk binary sender remained active while no HA task drained its
bounded queue. This was reproduced against the pre-fix runtime: it returned from
`attach_audio()` on `assistant.response_finished`, ended the speculative turn,
and left the subscription binary handler receiving audio.

The new design must test both halves of this invariant: capture remains drained
while non-terminal, and terminal cleanup unregisters capture promptly.

## 3. Target ownership model

Use three deliberately separate lifecycles:

```text
ExternalConversationRuntime (HA/Python)
  owns external WebSocket, protocol turns, PCM draining, provider responses

ExternalSessionController (frontend/JavaScript)
  owns interaction state, provider response lease, playback lease,
  visualization handoff, follow-up inactivity, explicit stop

TtsManager / AudioManager / UIManager (existing shared components)
  remain mechanisms invoked by the controller; they do not learn the
  External protocol
```

The frontend controller projects its state onto existing UI states. Existing
`State.STT`, `State.INTENT`, and `State.TTS` remain presentation values, not the
source of truth for External lifecycle decisions.

## 4. Explicit frontend state machine

Create `src/pipeline/external-session.js`.

### 4.1 States

```javascript
export const ExternalState = Object.freeze({
  IDLE: 'idle',
  CAPTURING: 'capturing',
  PROCESSING: 'processing',
  PLAYING_AND_CAPTURING: 'playing_and_capturing',
  BARGE_IN: 'barge_in',
  FOLLOWUP_LISTENING: 'followup_listening',
  TERMINATING: 'terminating',
  TERMINATED: 'terminated',
  FAILED: 'failed',
});
```

State meaning:

- `IDLE`: no External run is attached.
- `CAPTURING`: initial/speculative audio turn is receiving PCM.
- `PROCESSING`: provider response generation started; capture remains available
  through the speculative next turn.
- `PLAYING_AND_CAPTURING`: one response owns local playback while PCM continues.
- `BARGE_IN`: server-confirmed speech stopped local playback; new speech is
  reaching the provider.
- `FOLLOWUP_LISTENING`: playback completed normally and the bounded follow-up
  window is active.
- `TERMINATING`: stop/timeout/failure teardown is in progress.
- `TERMINATED`: capture and subscription are closed.
- `FAILED`: visible terminal failure before cleanup completes.

### 4.2 Independent leases

The controller owns:

```javascript
_providerResponseId = null;
_providerTurnId = null;
_playbackResponseId = null;
_playbackTurnId = null;
_followupTimer = null;
_state = ExternalState.IDLE;
```

Rules:

- `assistant.response_started` acquires the provider lease.
- `assistant.response_finished` releases only the provider lease.
- `assistant.audio` acquires the playback lease.
- Actual `TtsManager` completion releases the playback lease.
- `user.speech_started` releases/stops the current playback lease when one
  exists, regardless of whether provider generation already finished.
- `assistant.interrupted` releases both matching leases when applicable.
- Stale response IDs cannot mutate either current lease.

### 4.3 Transition table

| Current state | Input | Required actions | Next state |
| --- | --- | --- | --- |
| `IDLE` | `run-start` | clear old leases/timers | `CAPTURING` |
| `CAPTURING` | response started R | acquire provider R | `PROCESSING` |
| `PROCESSING` | audio R | stop stale playback, play R, acquire playback R | `PLAYING_AND_CAPTURING` |
| `PROCESSING` | response finished R without audio | release provider R, restore capture visuals, arm follow-up timer | `FOLLOWUP_LISTENING` |
| `PLAYING_AND_CAPTURING` | response finished R | release provider R only | unchanged |
| `PLAYING_AND_CAPTURING` | genuine speech | stop playback R exactly once, restore mic visuals, clear follow-up timer | `BARGE_IN` |
| `PLAYING_AND_CAPTURING` | playback complete R | release playback R, restore mic visuals, arm follow-up timer | `FOLLOWUP_LISTENING` |
| `BARGE_IN` | response started N | acquire provider N | `PROCESSING` |
| `FOLLOWUP_LISTENING` | genuine speech | clear follow-up timer | `BARGE_IN` |
| any active | explicit stop | stop playback, stop pipeline/subscription | `TERMINATING` then `TERMINATED` |
| `FOLLOWUP_LISTENING` | inactivity timeout | same terminal cleanup; hide backdrop and bar | `TERMINATING` then `TERMINATED` |
| any active | protocol/network failure | stop playback and capture, show one error | `FAILED` then `TERMINATED` |

Invalid transitions are logged and ignored or failed closed; they must not be
silently treated as normal HA pipeline events.

## 5. File-by-file implementation plan

### 5.1 New: `src/pipeline/external-session.js`

Implement `ExternalSessionController` with injected mechanisms so it can be unit
tested without DOM, Audio, Home Assistant, or Kiosk globals.

Suggested constructor:

```javascript
new ExternalSessionController({
  log,
  playResponseAudio,
  stopResponseAudio,
  restoreCaptureVisualization,
  setPresentationState,
  showInteractionUi,
  hideInteractionUi,
  stopPipeline,
  schedule,
  cancelScheduled,
  followupTimeoutMs,
});
```

Public methods:

```javascript
onRunStart()
onResponseStarted(meta)
onResponseAudio(meta, url, contentType)
onResponseFinished(meta)
onSpeechStarted(meta)
onInterrupted(meta)
onPlaybackComplete(playbackFailed)
onExplicitStop(reason)
onFailure(error)
destroy()
```

All metadata methods require `{turn_id, response_id}` where applicable.
`onSpeechStarted` needs only the server-correlated turn; stopping current External
playback is safe because the event itself is the server's genuine-speech decision.

`playResponseAudio` must first stop a different existing playback lease. There
can be only one active External audio response per satellite.

### 5.2 `src/session/index.js` or the common session constructor

Instantiate one `ExternalSessionController` per page session. Keep it dormant
until an External event arrives. Do not initialize provider details or alter the
normal pipeline startup.

Destroy it during existing session teardown.

Remove `_externalPersistentTurn` and `_externalResponseId` after all event paths
are delegated to the controller.

### 5.3 `src/session/events.js`

Reduce current External handling to narrow dispatch hooks:

```javascript
case 'external-run-start':
case 'external-vad-start':
case 'external-response-started':
case 'external-audio-start':
case 'external-response-finished':
case 'external-interrupted':
case 'external-error':
```

Alternatively retain `intent-start`, `stt-end`, and text-progress normalization
for existing chat rendering, while calling the controller before the normal
presentation handler. Lifecycle decisions must not depend on generic `State`.

`onTTSComplete()` receives one early External hook:

```javascript
if (session.externalSession?.ownsPlayback()) {
  session.externalSession.onPlaybackComplete(playbackFailed);
  return;
}
```

This replaces the broad `_externalPersistentTurn` boolean branch. A stale or
normal HA TTS completion cannot affect External state.

The existing generic VAD watchdog remains available as a secondary failure
bound, but the controller follow-up timer owns normal inactivity.

### 5.4 `src/audio/index.js`

Add one additive mechanism method:

```javascript
restoreCaptureVisualization()
```

Behavior:

- If capture is delegated to Kiosk, call `analyser.attachExternalMic()`.
- If Kiosk chunk capture is active, also select external mic mode.
- If browser MediaStream capture is active, reconnect/select the Web Audio mic
  analyser.
- Do nothing when no microphone is active.

The External controller calls this after interruption and playback completion.
No existing Home Assistant Assist caller changes.

Do not restart the mic, reopen Kiosk capture, clear pre-roll, or start a second
PCM sender merely to restore visuals.

### 5.5 `src/tts/index.js`

Prefer no protocol-aware changes. The controller calls the existing `play()` and
`stop()` mechanisms.

Add a minimal defensive behavior only if failing tests demonstrate it is needed:
when a new native playback starts while `_nativeSound` exists, stop and release
the old handle before accepting the new one. This is generally correct for all
callers because TTSManager supports only one playback at a time.

Ensure async native acceptance remains fenced:

- interrupted pending playback must call `tracked.stop()` if acceptance resolves
  late;
- old `done` callbacks must not invoke `onTTSComplete()` for newer playback;
- stopping is idempotent and occurs exactly once per handle.

An optional opaque playback owner token may be added to `play`/`stop` only if the
existing generation guard cannot prove these invariants. Do not expose External
turn IDs throughout generic TTS code unnecessarily.

### 5.6 `custom_components/voice_satellite/external_transport/protocol.py`

Keep strict server correlation. Normalize lifecycle events with their IDs intact.

`user.speech_started` maps to `external-vad-start`. Its semantics must be
explicitly documented: this is server-confirmed genuine speech and authorizes
local interruption of the current External playback lease.

`assistant.response_finished` remains `external-response-finished`, never
`run-end`.

Only `session.finished`, explicit terminal teardown, protocol failure, or
frontend inactivity expiry produces terminal `run-end`.

### 5.7 `custom_components/voice_satellite/external_transport/runtime.py`

Make the HA runtime state explicit and independent of frontend presentation:

```python
class RuntimeState(StrEnum):
    NEW = "new"
    CONNECTING = "connecting"
    CAPTURING = "capturing"
    RESPONDING = "responding"
    READY_FOR_FOLLOWUP = "ready_for_followup"
    CLOSING = "closing"
    CLOSED = "closed"
    FAILED = "failed"
```

Retain:

- one external event reader;
- one PCM drain task;
- one open audio turn;
- one provider response lease;
- one frontend binding generation.

Correct lifecycle:

- Response start closes the prior input turn and atomically opens a speculative
  next audio turn.
- Response finish clears provider response state but does not resolve the binding
  and does not cancel PCM draining.
- Provider speech keeps/reuses the speculative turn and is forwarded to the
  frontend.
- Empty PCM marker from actual subscription stop is terminal.
- Entity unload, transport switch, network failure, and `session.finished` are
  terminal.

Do not calculate the normal follow-up timeout from
`assistant.response_finished`; HA cannot know playback completion. Keep only a
longer server-side safety ceiling if needed. The frontend inactivity timer calls
existing pipeline stop/unsubscribe, producing the terminal PCM marker.

### 5.8 `custom_components/voice_satellite/__init__.py`

Keep changes minimal because this is an upstream-sensitive file.

For External queue overflow:

1. Log once per run, not once per incoming frame.
2. Insert the terminal marker, dropping one stale frame if required.
3. Notify the runtime/binding of `external_audio_overflow`.
4. Unregister or reject further binary frames for that run.
5. Present one visible terminal error.

Normal Home Assistant queues and binary handlers remain unchanged.

The unsubscribe callback remains the canonical explicit browser/Kiosk stop path.
It must always get a terminal marker into the bounded queue.

### 5.9 Documentation

Update:

- `docs/external-transport-protocol.md`: distinguish provider generation,
  playback ownership, speech-authorized local cancellation, and terminal session
  events.
- `docs/external-transport-turns.md`: link to this lifecycle correction.
- `README.md`: document the bounded follow-up-listening window and explicit stop.

No OpenAI or Pipecat names belong in Voice Satellite protocol documentation.

## 6. TDD implementation sequence

Every production change begins with a bounded failing test demonstrating the
observed behavior. Do not implement several lifecycle fixes behind one broad
end-to-end test.

### 6.1 Frontend state-machine tests first

Add `tests/js/external-session.test.mjs` using Node's built-in test runner. Add:

```json
"test:js": "node --test tests/js/*.test.mjs"
```

Make the controller a pure dependency-injected module so fake timers and call
recorders are sufficient.

#### Test 1: response finish is not playback finish

Sequence:

```text
run-start -> response-started R1 -> audio R1 -> response-finished R1
```

Assertions:

- State remains `PLAYING_AND_CAPTURING`.
- Playback R1 was not stopped.
- Provider lease is clear.
- Playback lease remains R1.
- No terminal timer starts yet.
- Pipeline stop was not called.

This test must fail against the boolean-based implementation.

#### Test 2: server-confirmed speech stops completed-response playback

Continue Test 1 with `user.speech_started(turn 2)`.

Assertions:

- `stopResponseAudio(R1)` called exactly once.
- Playback lease cleared.
- Capture visualization restored.
- State becomes `BARGE_IN`.
- PCM/pipeline stop not called.

This reproduces the reported semantic-barge-in/audio-overlap defect.

#### Test 3: a new response can never overlap old playback

Sequence:

```text
audio R1 -> response-finished R1 -> response-started R2 -> audio R2
```

without a prior speech/interruption event.

Assertions:

- R1 is stopped before `playResponseAudio(R2)`.
- Only R2 owns playback.
- Late completion/interruption for R1 is ignored.

#### Test 4: playback completion restores Kiosk mic visualization

With delegated capture active, invoke `onPlaybackComplete(false)`.

Assertions:

- `restoreCaptureVisualization()` called once.
- Presentation becomes STT/listening.
- State becomes `FOLLOWUP_LISTENING`.
- Follow-up timer starts only now.

#### Test 5: stale interruption cannot stop current playback

Start R2 playback, then deliver `assistant.interrupted(R1)`.

Assertions:

- R2 continues.
- No lease changes.
- Stale event is logged.

#### Test 6: explicit stop is terminal

From every active state, invoke `onExplicitStop("kiosk_stop")`.

Assertions:

- Current playback stopped once if present.
- Timer cleared.
- Pipeline stop/unsubscribe called once.
- UI backdrop and bar both hidden.
- Final state is `TERMINATED`.

#### Test 7: inactivity is measured after playback

Use fake timers:

- Advance beyond timeout while response is still playing: no teardown.
- Complete playback and advance to just before timeout: no teardown.
- Advance through timeout: one terminal teardown.
- Deliver genuine speech before timeout: timer clears and no teardown.

#### Test 8: playback failure still restores capture or terminates deliberately

Decide policy explicitly. Recommended for testing: restore follow-up capture but
show the existing playback warning; do not leave an invisible open session.

### 6.2 Audio/TTS mechanism tests

Add isolated tests or a small harness around injected fake Kiosk handles:

1. Native TTS completion followed by `restoreCaptureVisualization()` selects
   external mic mode, not playback external mode.
2. Starting R2 while R1 handle exists calls R1 stop before R2 play.
3. Stopping while native acceptance is pending causes the late handle to stop
   itself and never install callbacks.
4. Old `done` callback cannot complete newer playback.

If these mechanisms cannot be imported under Node without browser globals,
extract only the ownership/generation logic into a dependency-free helper rather
than introducing a full browser test framework.

### 6.3 Python protocol/runtime tests

Expand `tests/test_external_transport_protocol.py` or split
`tests/test_external_transport_runtime.py`.

#### Runtime Test 1: response completion retains binding

- Attach fake Kiosk audio queue.
- Emit response start and response finish.
- Assert `attach_audio()` task is still pending.
- Assert speculative turn remains open.
- Put another PCM frame and assert it reaches the same fake client.

#### Runtime Test 2: genuine speech after response finish is forwarded

- Finish R1 generation.
- Emit `user.speech_started` for speculative turn two.
- Assert `external-vad-start` reaches the same binding.
- Assert runtime remains non-terminal.

#### Runtime Test 3: explicit stop marker is terminal

- Put `b""` in the audio queue.
- Assert open turn ends.
- Assert `session.cancel(reason=client_stopped)` is sent.
- Assert event/audio tasks complete within test timeout.

#### Runtime Test 4: no normal timeout starts at provider finish

- Emit response finish while fake playback is conceptually active.
- Advance runtime time beyond the frontend follow-up timeout.
- Assert HA runtime remains attached unless its longer safety ceiling expires.

#### Runtime Test 5: queue overflow fails once

At the WebSocket-handler boundary:

- Fill 64-frame External queue.
- Send additional frames.
- Assert one overflow error, one terminal marker, and one unregister action.
- Assert warning count is one rather than proportional to incoming PCM.

#### Runtime Test 6: stale response fencing

- Start R2 after R1 interruption.
- Emit late R1 audio/finish.
- Assert no frontend send mutates R2.

All async tests retain repository-wide timeout bounds.

### 6.4 Cross-layer local Kiosk harness

Create an ephemeral test harness, not production code, with:

- actual `ExternalConversationRuntime`;
- fake HA `ActiveConnection` recording events;
- bounded PCM queue;
- fake Kiosk level stream;
- dependency-injected `ExternalSessionController` mechanisms;
- either fake server or live WSS endpoint.

Run this sequence:

```text
PCM turn 1
-> transcript 1
-> response/audio R1
-> response finished while fake playback remains active
-> PCM/level turn 2
-> server user.speech_started
-> assert R1 stop
-> response/audio R2
-> assert only R2 playing
-> complete R2 playback
-> follow-up timer
-> terminal unsubscribe
```

Assertions include event order, state transitions, exact stop counts, no queue
overflow, continued level animation after playback, and bounded teardown.

Secrets for a live endpoint are loaded only inside the bounded harness process
and never printed or persisted.

### 6.5 Real-device acceptance trace

Add temporary debug-level state transition logs without secrets or signed URLs:

```text
external state old -> new, turn_id, response_id
playback acquire/release, response_id, reason
capture visualization source: delegated/browser/none
follow-up timer arm/cancel/fire
runtime queue depth high-water mark
```

On Kiosk Satellite verify:

1. Turn-one voice moves the bar.
2. Turn-one response plays alone.
3. After playback, turn-two voice moves the bar again.
4. Speaking during response one stops it promptly.
5. Response two is the only audio playing afterward.
6. Silence ends the interaction after the configured follow-up period.
7. Kiosk stop ends it immediately.
8. Backdrop and bar disappear together on every terminal path.
9. No HA queue-overflow warnings occur.

Remove or reduce temporary verbose logs before release.

## 7. Implementation commit sequence

1. `test: reproduce external playback and visualization lifecycle`
   - Add failing pure-controller tests and Python binding tests.
2. `feat: add isolated external client state controller`
   - Add controller only; narrow session construction/dispatch hooks.
3. `fix: restore capture visualization after external playback`
   - Add AudioManager mechanism and pass its failing tests.
4. `fix: stop completed external playback on provider speech`
   - Split provider/playback leases; enforce single playback.
5. `fix: make external inactivity and stop terminal`
   - Frontend post-playback timer plus explicit stop teardown.
6. `fix: fail external PCM overflow once`
   - Bounded HA handler failure without normal Assist changes.
7. `test: validate full external kiosk lifecycle harness`
   - Cross-layer fake and optional live endpoint run.
8. `docs: describe external client lifecycle`
   - Canonical behavior and operational diagnostics.
9. Release a new immutable fork tag only after CI and hardware acceptance.

## 8. Acceptance criteria

The correction is complete when:

- External lifecycle decisions come from `ExternalSessionController`, not loose
  session booleans or generic UI state.
- Provider generation and local playback have separate correlated leases.
- Server-confirmed speech stops an already-generated response still playing
  locally.
- At most one native/browser response clip can play at once.
- Delegated Kiosk microphone levels resume after every playback/interruption.
- PCM continues draining throughout response generation and playback.
- Provider response finish is non-terminal.
- Explicit stop, failure, entity teardown, and bounded post-playback inactivity
  are terminal.
- Backdrop and listening bar always terminate together.
- Stale events cannot stop or replace current playback.
- No repeated queue-overflow warning storm occurs.
- Python tests, Node tests, production Webpack build, local cross-layer harness,
  live server smoke test, and physical Kiosk acceptance all pass.
