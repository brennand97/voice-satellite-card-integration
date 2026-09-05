# Voice Satellite External Transport Fork — Implementation Plan

## 1. Purpose

Maintain a downstream fork of [`jxlarrea/voice-satellite-card-integration`](https://github.com/jxlarrea/voice-satellite-card-integration) that adds a provider-neutral **External Transport** while preserving the original Home Assistant Assist behavior.

The fork will continue to receive and merge upstream changes. Its downstream implementation must use generic external-transport terminology and must not couple Voice Satellite to Pipecat, OpenAI, or any other specific agent framework.

## 2. Goals

- Preserve upstream Voice Satellite behavior and compatibility.
- Add a per-satellite choice between Home Assistant Assist and an external conversation transport.
- Retain Kiosk Satellite's native wake-word detection, native microphone capture, binary PCM upload, and pre-roll.
- Keep continuous audio processing out of the tablet WebView.
- Normalize external events into Voice Satellite's existing UI and pipeline event model.
- Keep external endpoint credentials in the Home Assistant backend.
- Make the downstream patch small enough to merge upstream changes regularly.
- Automatically check upstream daily or weekly, validate changes, and safely merge passing updates.
- Notify the repository owner through GitHub when upstream synchronization fails.

## 3. Non-goals

- Modifying or redistributing Kiosk Satellite.
- Embedding Pipecat-specific behavior in this repository.
- Running an external voice model inside Home Assistant.
- Replacing the existing Home Assistant Assist transport.
- Automatically replaying potentially executed commands after an uncertain failure.

## 4. Architecture

```text
Kiosk Satellite
  native wake word + native pre-roll
        |
        | voice_satellite/run_pipeline
        | binary 16 kHz PCM16
        v
Voice Satellite fork
  +-- HomeAssistantConversationTransport
  `-- ExternalConversationTransport
        |
        | External Transport Protocol v1
        v
External voice transport server
```

Kiosk Satellite will continue calling the existing `voice_satellite/run_pipeline` Home Assistant WebSocket command. The fork will select the transport internally based on the satellite's configuration.

## 5. Repository and branch model

The eventual GitHub repository should retain GitHub's fork relationship to the upstream repository.

Recommended remotes:

```text
origin    -> the owner's GitHub fork
upstream  -> https://github.com/jxlarrea/voice-satellite-card-integration.git
```

Recommended branches:

- `main`: deployable downstream fork, protected.
- `automation/upstream-sync`: bot-managed branch used for upstream synchronization PRs.
- Feature branches: normal development branches for the external transport.

Use release tags that identify both the upstream base and downstream revision, for example:

```text
v<upstream-version>-external.<revision>
```

## 6. Proposed source organization

Adapt the final layout to the upstream repository while keeping new behavior isolated:

```text
custom_components/voice_satellite/
  external_transport/
    __init__.py
    client.py
    exceptions.py
    protocol.py
    session.py
  assist_satellite.py
  config_flow.py
  ...

src/
  external-transport/
    events.js
    index.js
    state.js
  ...

docs/
  external-transport.md
  external-transport-protocol.md
```

Do not use provider-specific names such as `pipecat_client.py`, `run_pipecat()`, or `PIPECAT_URL`. Use names such as `ExternalTransportClient`, `run_external_transport()`, and `EXTERNAL_TRANSPORT_URL`.

## 7. Configuration model

### 7.1 Integration-level options

Store these in the Home Assistant config entry/options:

- External transport WebSocket URL
- External transport authentication token
- TLS certificate verification setting
- Connection timeout
- Session-ready timeout
- Maximum buffered input duration
- Optional health-check interval

The URL and authentication token must not appear in entity states, frontend configuration, normal logs, or diagnostics exports.

### 7.2 Per-satellite option

Add a conversation transport selection:

```text
Home Assistant
External
```

A select entity may expose this as:

```text
select.<satellite>_conversation_transport
```

The default must remain `Home Assistant`, preserving existing installations.

## 8. Transport abstraction

Define a narrow internal interface rather than adding provider checks throughout the current pipeline:

```python
class ConversationTransport(Protocol):
    async def start(self, context: SessionContext) -> None: ...
    async def write_audio(self, pcm: bytes) -> None: ...
    async def end_input(self) -> None: ...
    async def cancel(self, reason: str) -> None: ...
    def events(self) -> AsyncIterator[TransportEvent]: ...
```

Implementations:

- `HomeAssistantConversationTransport`: adapter around current behavior.
- `ExternalConversationTransport`: protocol client for the configured external server.

Initially, avoid refactoring stable upstream code more than necessary. It may be safer to wrap existing behavior and introduce a single dispatch point:

```python
if entity.conversation_transport == "external":
    await entity.async_run_external_transport(...)
else:
    await entity.async_run_pipeline(...)
```

## 9. Native audio path

The existing HA binary-handler path remains authoritative:

1. Receive `voice_satellite/run_pipeline` from Kiosk Satellite.
2. Register the Home Assistant WebSocket binary handler.
3. Immediately accept and queue native PCM frames, including Kiosk Satellite pre-roll.
4. Connect to the external transport concurrently.
5. Send `session.start` and wait for `session.ready`.
6. Drain queued audio in original order.
7. Continue forwarding live binary audio with backpressure.
8. Stop and clean up on unsubscribe, timeout, disconnect, or cancellation.

Expected input format:

```text
Encoding: pcm_s16le
Sample rate: 16000 Hz
Channels: 1
Raw rate: 32000 bytes/second
```

Bound the pre-ready queue. Start with five seconds:

```text
5 seconds * 32000 bytes/second = 160000 bytes
```

Fail cleanly if the external service does not become ready before the queue reaches its limit.

## 10. External Transport Protocol v1

The canonical protocol specification belongs in `docs/external-transport-protocol.md` in this repository. External server implementations must conform to it.

### 10.1 Connection

- WebSocket over TLS in production.
- Bearer authentication in the connection request.
- JSON text messages for control and events.
- Binary messages from HA to the server for input PCM.
- Explicit protocol version on every session.

### 10.2 Session start

```json
{
  "type": "session.start",
  "protocol_version": 1,
  "session_id": "01J...",
  "satellite": {
    "entity_id": "assist_satellite.kitchen",
    "name": "Kitchen"
  },
  "audio": {
    "encoding": "pcm_s16le",
    "sample_rate": 16000,
    "channels": 1
  },
  "conversation": {
    "id": null,
    "wake_word": "Okay Nabu"
  }
}
```

### 10.3 Ready response

```json
{
  "type": "session.ready",
  "session_id": "01J...",
  "capabilities": {
    "transcription": true,
    "streaming_audio_url": true,
    "interruptions": true,
    "conversation_continuation": true
  }
}
```

Do not drain audio until readiness is confirmed.

### 10.4 Client control messages

```json
{"type":"input.end"}
{"type":"input.pause"}
{"type":"input.resume"}
{"type":"session.cancel","reason":"user_cancelled"}
```

### 10.5 Server events

```json
{"type":"user.speech_started"}
{"type":"user.speech_stopped"}
{"type":"user.transcript.partial","text":"turn off the"}
{"type":"user.transcript.final","text":"turn off the kitchen lights"}
{"type":"assistant.response_started"}
{"type":"assistant.text.delta","text":"Turning"}
{"type":"assistant.text.final","text":"Turning off the kitchen lights."}
{
  "type":"assistant.audio",
  "id":"audio-123",
  "url":"https://voice-agent.example/audio/audio-123?token=...",
  "content_type":"audio/ogg"
}
{"type":"assistant.interrupted","audio_id":"audio-123"}
{"type":"assistant.response_finished"}
{"type":"session.finished","reason":"completed"}
```

Error shape:

```json
{
  "type": "error",
  "code": "provider_unavailable",
  "message": "The external voice service is unavailable",
  "retryable": true
}
```

## 11. Event normalization

Translate external events into the closest existing Voice Satellite pipeline events before sending them to the frontend.

| External event | Existing behavior to drive |
|---|---|
| `user.speech_started` | Listening/STT state |
| `user.transcript.partial` | STT partial |
| `user.transcript.final` | STT final |
| `assistant.response_started` | Processing/responding state |
| `assistant.text.delta` | Intent progress |
| `assistant.text.final` | Final response text |
| `assistant.audio` | Streaming TTS/native playback |
| `assistant.interrupted` | Stop active sound |
| `session.finished` | Pipeline run end |
| `error` | Existing pipeline error UI |

Provider-specific event shapes must never reach Voice Satellite frontend code.

## 12. Audio output and interruptions

On `assistant.audio`, the frontend should hand the signed stream URL to Kiosk Satellite using its native playback API with streaming enabled. Track the returned sound ID.

On `assistant.interrupted`, cancellation, or session failure:

- Stop the current Kiosk Satellite sound.
- Cancel remaining external output.
- Keep or reopen input according to the session state.
- Return cleanly to listening or idle.

When the entire session ends:

- Stop audio upload.
- Unregister the binary handler.
- Unsubscribe the run.
- Release frontend interaction state.
- Re-arm native wake-word detection.

## 13. Failure and fallback policy

Fallback must avoid duplicate device actions.

Automatic fallback to Home Assistant Assist is safe only before all of the following:

- `session.ready` has been received.
- Input audio has been sent externally.
- An external tool could have executed.

After that boundary, report the failure and end the turn. Do not replay the utterance automatically.

Expose useful diagnostics states:

- Connected
- Connecting
- Unavailable
- Authentication failed
- Protocol mismatch
- Timed out
- Session failed

## 14. Security requirements

- Keep external credentials backend-only.
- Require `wss://` by default; make insecure `ws://` an explicit advanced/LAN option.
- Redact bearer tokens, signed URLs, and sensitive headers from logs.
- Validate all inbound protocol messages and enforce size limits.
- Bound audio queues and session duration.
- Do not log or persist raw PCM by default.
- Use constant-time token handling where applicable on the server side.
- Preserve Home Assistant's normal permissions and entity exposure behavior.

## 15. Test plan

### 15.1 Backend unit tests

- Protocol serialization and validation
- Authentication headers
- TLS verification behavior
- Secret redaction
- Binary PCM ordering
- Pre-roll ordering
- Queue backpressure and overflow
- Connection and readiness timeouts
- Unsubscribe and cancellation
- External disconnect
- Protocol mismatch
- Existing HA pipeline behavior remains unchanged

### 15.2 Frontend tests

- External session state transitions
- Partial and final transcript rendering
- Streaming sound start and stop
- Barge-in/interruption
- Error presentation
- Cleanup after navigation
- Wake-word re-arming after every terminal state

### 15.3 Contract tests

Build a fake External Transport v1 server covering:

- Successful turn
- Slow readiness
- Partial transcripts
- Streaming response audio
- Interruption
- Authentication rejection
- Protocol mismatch
- Mid-session disconnect
- Cancellation while connecting

### 15.4 Hardware acceptance tests

On the target tablet:

- Seamless wake phrase preserves post-wake speech.
- Dashboard remains responsive during input and output.
- Screen-off/background wake works.
- Echo cancellation is adequate.
- Barge-in stops output quickly.
- Wake detection resumes after success, error, timeout, HA restart, and external-server restart.

## 16. Safe upstream synchronization

### 16.1 Desired behavior

- Check upstream on a configurable daily or weekly cadence.
- Support manual forced synchronization.
- Create/update a PR rather than pushing directly to `main`.
- Run fetched upstream code only in unprivileged CI.
- Auto-merge only after required checks pass.
- Require manual review for sensitive paths.
- Notify the repository owner when any synchronization stage fails.

### 16.2 Repository variables

```text
UPSTREAM_SYNC_CADENCE=daily|weekly|disabled
UPSTREAM_SYNC_WEEKDAY=1
UPSTREAM_SYNC_NOTIFY_LOGIN=<github-login>
```

Use ISO weekdays (`1` Monday through `7` Sunday), evaluated in UTC.

### 16.3 Scheduler

Run one scheduler daily and gate execution according to the variables:

```yaml
on:
  schedule:
    - cron: "17 6 * * *"
  workflow_dispatch:
    inputs:
      force:
        description: Ignore cadence and run now
        type: boolean
        default: false
```

An uncommon minute reduces GitHub's top-of-hour scheduler congestion.

### 16.4 Three-stage safety model

#### Stage A: trusted sync controller

- Runs from the current trusted default-branch workflow definition.
- Uses least-privilege `GITHUB_TOKEN` permissions needed to write the automation branch and PR.
- Fetches upstream over read-only HTTPS.
- Determines whether new commits exist.
- Attempts a merge into `automation/upstream-sync`.
- Does **not** install dependencies or execute fetched upstream code.
- Pushes the automation branch and creates/updates a PR.
- Aborts and reports merge conflicts.

#### Stage B: unprivileged PR validation

- Triggered with `pull_request`, not `pull_request_target`.
- Has read-only repository permissions.
- Receives no repository or environment secrets.
- Runs backend tests, frontend tests, contract tests, linting, and build checks.
- Checks changed paths and blocks unattended merging of sensitive files.

Sensitive paths should include at least:

```text
.github/**
scripts/**
package.json
package-lock.json
custom_components/voice_satellite/manifest.json
```

Any `.github/**` change must require manual review because merged workflows may later receive elevated permissions.

#### Stage C: protected merge

- Enable GitHub auto-merge on the generated PR only when no sensitive-path rule blocks it.
- Require all branch-protection checks.
- Never bypass branch protection.
- Never force-push `main`.

### 16.5 Workflow hardening

- Pin third-party actions to complete commit SHAs.
- Use Dependabot to propose action updates.
- Avoid `pull_request_target`.
- Do not use a personal access token unless a documented GitHub limitation requires one.
- Grant permissions per job, not broadly for the entire workflow.
- Use concurrency:

```yaml
concurrency:
  group: upstream-sync
  cancel-in-progress: false
```

- Add explicit timeouts to every job.
- Retain logs and test artifacts for failed runs.
- Verify the PR head repository, branch, actor, and SHA before enabling auto-merge.

### 16.6 Branch protection

Protect `main` with:

- Pull requests required
- Required backend tests
- Required frontend tests
- Required contract tests
- Required build
- Required sensitive-path check
- Force pushes disabled
- Branch deletion disabled
- Auto-merge allowed only after required checks

## 17. Failure notifications

### 17.1 Native GitHub Actions email

The repository owner must configure GitHub notification settings to send workflow-failure notifications to their chosen email address.

### 17.2 Persistent failure issue

A trusted final job should run on failed synchronization and create or reopen one issue:

```text
Title: Automated upstream synchronization is failing
Labels: automation, upstream-sync, needs-attention
Assignee: UPSTREAM_SYNC_NOTIFY_LOGIN
```

Further failures should add comments containing:

- Failed stage
- Upstream commit
- Workflow run URL
- Whether a merge conflict occurred
- Recommended next action

A later successful synchronization should close the issue automatically. Assignment and subscription cause GitHub to send email according to the owner's GitHub notification settings, without SMTP credentials.

A workflow cannot guarantee notification if GitHub Actions never starts, GitHub itself is unavailable, or invalid YAML prevents workflow loading. An external heartbeat monitor would be needed to cover those cases.

## 18. GitHub CI and releases

Pull-request checks:

- Python formatting/linting/type checking
- JavaScript formatting/linting/tests
- Backend unit tests
- Frontend tests
- Protocol contract tests
- Production frontend build
- HACS validation
- Sensitive-path classification

Release workflow:

- Run the complete test suite.
- Build the frontend and integration artifact.
- Record upstream commit and protocol version.
- Publish a GitHub release.
- Preserve a known-good rollback artifact.

## 19. Delivery phases

### Phase 1: Fork baseline

- Import upstream history.
- Configure remotes and branch protection.
- Verify unmodified build/tests.
- Add downstream release naming.

### Phase 2: Protocol and fake server

- Write External Transport Protocol v1.
- Implement protocol models and validation.
- Build a fake server and contract tests.

### Phase 3: External input transport

- Add configuration and transport selection.
- Register native binary input.
- Add buffering, readiness, forwarding, and cancellation.

### Phase 4: Event/UI integration

- Normalize transcripts and response events.
- Integrate existing Voice Satellite states and overlay.
- Add native streaming playback and interruption.

### Phase 5: Resilience and security

- Add limits, redaction, diagnostics, timeouts, and safe fallback.
- Complete failure-mode and hardware testing.

### Phase 6: Upstream automation

- Add the sync controller.
- Add unprivileged PR validation.
- Configure protected auto-merge.
- Add issue-based failure notification and documentation.

## 20. Acceptance criteria

- Existing Home Assistant Assist mode remains functional.
- External mode sends Kiosk Satellite native PCM and pre-roll without routing audio through JavaScript.
- Provider-specific concepts do not appear in the fork's transport API.
- External events drive the existing Voice Satellite UI correctly.
- Native wake detection resumes after every success and failure path.
- Secrets do not appear in state, diagnostics, logs, or Git history.
- Upstream changes are checked daily or weekly according to one repository variable.
- Passing non-sensitive updates can merge through a protected PR automatically.
- Conflicts, failed checks, and blocked updates create/reopen an assigned GitHub issue.
- The owner receives GitHub email notifications after configuring repository notification delivery.
