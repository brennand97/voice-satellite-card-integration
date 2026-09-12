# Media Interference Guard — low-level implementation plan

Status: design only; implementation has not started.

Audience: an implementation agent with no prior conversation context.

## 1. Goal

Prevent configured Home Assistant/Music Assistant players near a physical Voice Satellite from masking commands or falsely triggering barge-in. Preserve audible feedback when a command starts music by temporarily lowering volume rather than always pausing playback.

The feature is configured per physical Satellite because the relationship between a microphone and nearby speakers is a room/device property, not an agent-profile property.

## 2. Evidence and baseline

Recent External Transport debug audio showed:

- Session `892a3099-5e1a-49a7-84dc-5f4907ec1d57` transcribed the request later clarified as “play Sabrina Carpenter in the kitchen” as “Can you price of rent cover during the kitchen?”
- 36.7% of that session's 20 ms input windows exceeded -35 dBFS, versus roughly 19–24% in several earlier captures.
- Assistant audio began at `2026-09-12T18:26:37.698Z`; provider VAD declared new user speech and interrupted it at `18:26:38.797Z`, about 1.1 seconds later, without a final transcript. This is consistent with playback echo or nearby program audio causing false barge-in.

The debug capture proves interference, not which player produced it.

## 3. Required behavior

Each Satellite exposes these options:

| Option | Type | Default | Bounds/values |
|---|---|---:|---|
| Nearby media players | entity selector, multiple | empty | `media_player` only |
| Interference action | select | `duck` | `off`, `duck`, `pause` |
| Temporary volume | integer percent | 10 | 1–50 |
| Restore delay | integer ms | 350 | 0–2000 |

Behavior:

- `off`: no backend media action.
- `duck`: while a conversation lease is active, configured playing players are held at `min(intended_volume, temporary_volume)`.
- `pause`: configured playing players are paused and guardedly resumed later.
- Players that begin playing during a lease are handled immediately. This is essential for “play music” commands: playback starts quietly, remains audible as confirmation, then returns to the intended volume after the assistant finishes.
- A player already below the temporary volume is never raised.
- User/agent volume changes during a lease become the new intended post-session volume. The temporary ceiling is reapplied only if the new value exceeds it.
- A user stop, source change, queue replacement, unavailable transition, or unrelated new playback invalidates stale restoration.
- Overlapping Satellite conversations sharing a player do not restore it until the final lease ends.
- Every terminal/error/disconnect path releases its lease.

## 4. Non-goals and acoustic boundary

Do not call this remote-speaker echo cancellation. A `media_player` state contains no time-aligned PCM reference. It cannot compensate for decoder buffering, group synchronization, clock drift, room impulse response, or speaker delay.

Same-device playback continues to rely on existing platform AEC:

- browser capture requests `echoCancellation`;
- Kiosk Satellite uses Android `VOICE_COMMUNICATION` and `AcousticEchoCanceler` when available;
- the browser Satellite's own player already uses `mediaPlayer.interrupt()` and `resumeAfterInterrupt()`.

This feature controls separate or grouped nearby players. It does not make the initial wake word easier to detect before the guard activates.

## 5. Persisted schema

### 5.1 Constants

Add to `custom_components/voice_satellite/const.py`:

```python
CONF_MEDIA_GUARD_ENTITIES: Final[str] = "media_guard_entities"
CONF_MEDIA_GUARD_ACTION: Final[str] = "media_guard_action"
CONF_MEDIA_GUARD_VOLUME: Final[str] = "media_guard_volume"
CONF_MEDIA_GUARD_RESTORE_DELAY_MS: Final[str] = "media_guard_restore_delay_ms"

MEDIA_GUARD_OFF: Final[str] = "off"
MEDIA_GUARD_DUCK: Final[str] = "duck"
MEDIA_GUARD_PAUSE: Final[str] = "pause"
```

Store volume as an integer percentage in config-entry options; convert once to HA's 0.0–1.0 service value.

### 5.2 Options flow

Modify `custom_components/voice_satellite/config_flow.py`.

`VoiceSatelliteOptionsFlow` currently selects service then profile. Preserve those steps and add `async_step_media_guard` after a valid profile is selected:

1. Keep pending service/profile IDs on the flow instance.
2. Render:
   - `EntitySelector(EntitySelectorConfig(domain="media_player", multiple=True))`;
   - action `SelectSelector` or `vol.In` with `off`, `duck`, `pause`;
   - integer number selectors for volume and delay.
3. Validate entity IDs with `cv.entity_ids` semantics and deduplicate while preserving order.
4. Save assignment and guard keys in one `async_create_entry` result.
5. Preserve prior values when re-entering the options flow.
6. An empty entity list is valid and equivalent to no actions regardless of selected mode.

Add labels/descriptions/options to:

- `custom_components/voice_satellite/strings.json`
- `custom_components/voice_satellite/translations/en.json`

Do not expose these values as entity state attributes.

## 6. Backend architecture

### 6.1 New module

Create `custom_components/voice_satellite/media_interference.py`.

Use a single integration-wide coordinator so two Satellites cannot independently restore the same player:

```python
@dataclass(frozen=True, slots=True)
class GuardPolicy:
    action: Literal["off", "duck", "pause"]
    volume: float
    restore_delay: float

@dataclass(frozen=True, slots=True)
class GuardLease:
    lease_id: str
    owner_entry_id: str
    entity_ids: tuple[str, ...]

@dataclass(slots=True)
class Holder:
    lease_id: str
    policy: GuardPolicy

@dataclass(slots=True)
class PlayerControl:
    entity_id: str
    holders: dict[str, Holder]
    intended_volume: float | None
    expected_media_token: str | None
    guard_context_ids: set[str]
    paused_by_guard: bool
    invalidated: bool
    pending_task: asyncio.Task | None
```

Coordinator public API:

```python
class MediaInterferenceCoordinator:
    async def acquire(owner_entry_id: str, entity_ids: tuple[str, ...], policy: GuardPolicy) -> GuardLease
    async def activate(lease: GuardLease) -> None
    async def release(lease: GuardLease) -> None
    async def close() -> None
```

`acquire` registers ownership but does not necessarily alter media. `activate` exists because server-side wake-word pipelines begin long before a wake word is detected.

All mutation of internal dictionaries occurs on the HA event loop. Use one `asyncio.Lock` only around transitions that contain awaited service calls; never hold it while waiting for a restore-delay timer.

### 6.2 Integration setup and teardown

In `custom_components/voice_satellite/__init__.py`:

- During `async_setup`, create one coordinator under an explicit key such as `hass.data[DOMAIN]["media_interference_coordinator"]`.
- Reuse it across entries.
- Register shutdown cleanup with `EVENT_HOMEASSISTANT_STOP`; `close()` cancels listeners/timers and performs best-effort guarded restoration.
- Never overwrite the existing per-entry objects stored in `hass.data[DOMAIN]`.

### 6.3 Media identity

Keep raw media identity only in memory. Build a comparison tuple from stable state attributes where available:

```text
(state.attributes.media_content_id,
 state.attributes.media_title,
 state.attributes.media_artist,
 state.attributes.source)
```

Do not write raw URLs/content IDs to logs. For debug logging, use the first 12 characters of SHA-256 over the tuple's canonical representation.

If no identity attributes exist, guarded volume restoration is still allowed while the player remains continuously `playing`; pause/resume restoration must be conservative and should be skipped after any ambiguous state transition.

### 6.4 Service calls and ownership

Use HA services, never call entity methods directly:

- `media_player.volume_set`
- `media_player.media_pause`
- `media_player.media_play`

Calls must be `blocking=True` and carry a fresh `homeassistant.core.Context`. Record the context ID before calling. State-change events caused by that context are guard-owned and must not update intended volume or invalidate restoration.

A failed/unavailable entity logs a warning and is skipped; it must not fail the voice pipeline.

### 6.5 Effective policy with overlapping leases

For each player:

1. If any active holder requests `pause`, effective action is `pause`.
2. Otherwise, effective action is `duck` using the minimum requested temporary volume.
3. Otherwise, effective action is `off`.

When one holder releases, recompute effective policy. Do not restore until no holder remains. If a `pause` holder releases but a `duck` holder remains, resume only if the guard paused it, then immediately apply the remaining duck ceiling.

### 6.6 State listener

Install one `async_track_state_change_event` listener per controlled entity while its first holder exists; remove it after final release.

On each event:

1. Ignore events whose `context.id` is in `guard_context_ids`, then remove the consumed ID.
2. If new state is unavailable/unknown, mark restoration invalid.
3. If media identity changed unexpectedly, mark old restoration invalid, treat the new playback as a fresh intended item, and capture its current volume.
4. If a configured player transitions to `playing` during an active lease:
   - capture current volume as intended;
   - capture identity token;
   - schedule policy application immediately.
5. If volume changes from a non-guard context:
   - update `intended_volume`;
   - if effective mode is duck and volume exceeds the ceiling, schedule one corrective duck;
   - if volume is at/below the ceiling, leave it unchanged and retain that value for release.
6. If player transitions to `idle`/`off` from a non-guard context, invalidate restoration and do not restart it.

Coalesce corrective tasks per entity so rapid MA state updates cannot create a service-call loop.

### 6.7 Group behavior

Version one targets exactly the configured HA entity IDs and deduplicates them. Do not infer undocumented Music Assistant internals. HA/MA is responsible for applying volume/pause semantics of a selected group entity.

UI help text must tell users to select the Music Assistant group/queue leader rather than every member when they want group-wide control. Add group-member auto-resolution only in a later change backed by captured MA state fixtures and tests.

### 6.8 Own Satellite player deduplication

The Satellite browser's own player is already synchronously interrupted in frontend paths. Before backend acquisition, find the integration-created media-player entity object stored under `f"{entry.entry_id}_media_player"` and remove its `entity_id` from backend targets. This prevents pause/volume races with `resumeAfterInterrupt()`.

## 7. Pipeline lifecycle integration

The active period must match a real interaction, not an always-listening wake pipeline.

### 7.1 Per-run handle

Add per-run guard bookkeeping to `VoiceSatelliteEntity` in `assist_satellite.py`, keyed by `_pipeline_gen`. Do not store one unversioned boolean.

Suggested helpers:

```python
async def _async_prepare_media_guard(self, generation: int) -> GuardLease | None
async def _async_activate_media_guard(self, generation: int) -> None
async def _async_release_media_guard(self, generation: int) -> None
```

### 7.2 Activation rules

- `start_stage == "stt"`: activate before consuming command PCM.
- `start_stage == "intent"` / text input: activate immediately so music started by tools is caught and assistant output remains audible.
- `start_stage == "wake_word"`: acquire an inactive lease, then activate only after a valid `wake_word-end` event. Never duck during continuous wake-word listening.
- Continuation/follow-up turns reuse or reacquire the lease without a restore/un-duck pulse between turns.

`on_pipeline_event` is a callback. On valid `wake_word-end`, schedule activation with `hass.async_create_task`; capture generation in the closure and no-op if it is stale.

### 7.3 Release rules

Wrap every created pipeline coroutine in a small async runner with `try/finally` and release the generation's lease in `finally`. This must cover:

- native HA pipeline completion;
- External Transport attachment completion;
- text runs;
- displacement by a new browser;
- unsubscribe/empty-audio stop;
- queue overflow;
- cancellation timeout;
- entity removal and HA shutdown.

The final release schedules restoration after the largest applicable `restore_delay`. A newly acquired holder during that delay cancels restoration.

Do not rely only on frontend `run-end`; server teardown must own cleanup.

## 8. Unit and integration tests

### 8.1 New HA test module

Create `tests/ha/test_media_interference.py` using `pytest-homeassistant-custom-component`.

Build fake `media_player` service handlers that update states with the supplied service-call context and append calls to a list. Use real HA state-change events and `async_block_till_done()`.

Required tests:

1. Duck a playing player from 0.70 to 0.10 and restore 0.70.
2. Do not raise a player already at 0.05.
3. Player starting at 0.65 during a lease is ducked and later restored.
4. Non-guard volume change to 0.40 while leased updates intent and re-ducks to 0.10.
5. Non-guard volume change to 0.05 remains 0.05 after release.
6. Guard-owned state updates do not recurse or replace intended volume.
7. User stop prevents restoration.
8. Source/media identity replacement prevents stale resume but allows the new item to be independently ducked.
9. Pause mode pauses/resumes only guard-owned playback.
10. Two leases on one player use the strictest policy and restore only after final release.
11. New lease during delayed restore cancels restore.
12. Service failure/unavailable entity does not raise into pipeline code.
13. Coordinator close cancels tasks/listeners and best-effort restores.
14. Own Satellite player is excluded from backend targets.
15. Wake-word pipeline remains unducked until valid `wake_word-end`.
16. STT, text, external terminal, cancellation, displacement, and overflow paths release.

Use controllable time (`async_fire_time_changed`) instead of sleeping for restore-delay tests.

### 8.2 Config-flow tests

Extend `tests/ha/test_config_flows.py`:

- entity selector is UI serializable and restricted to `media_player`;
- defaults are duck/10/350;
- bounds reject 0%, 51%, negative delay, and >2000 ms;
- entity IDs are deduplicated;
- existing service/profile assignment survives guard edits;
- old entries with no guard keys retain backward-compatible defaults.

### 8.3 Frontend regression

Extend `tests/js/external-session.test.mjs` only where needed to prove existing local `mediaPlayer.interrupt()` and `resumeAfterInterrupt()` still happen exactly once. The backend feature must not duplicate local player control.

### 8.4 Required local validation

```bash
cd /home/brennan/repos/voice-satellite-card-integration
npm test
npm run build
.venv/bin/python -m pytest -q
```

Also run workflow-equivalent HACS/hassfest checks before release.

## 9. Live harness from the dev box (`kratos`)

### 9.1 Harness deliverable

Add `scripts/live_media_interference_probe.py`. It is an observer/measurement tool, not a hidden production service.

Arguments:

```text
--ha-url https://homeassistant.lan.brennandouglas.com
--satellite assist_satellite.<test_satellite>
--player media_player.<configured_ma_player>  (repeatable)
--duration 120
--output /tmp/media-guard-probe.json
--confirm-live-impact
```

Credential input:

- Read `HOMEASSISTANT_TOKEN` from the process environment only.
- Never accept a token on the command line.
- Never print headers, tokens, signed media URLs, or full media content IDs.
- The operator loads the appropriate credential according to the local non-secret credential inventory.

Safety:

- Without `--confirm-live-impact`, connect, validate entities, and print a redacted readiness report only.
- The harness does not directly alter volume or playback. It observes actual feature behavior while an operator starts a real Satellite conversation.
- Install SIGINT/SIGTERM handlers, close subscriptions, and write partial results atomically.

### 9.2 HA WebSocket behavior

The harness connects to `<ha-url>/api/websocket`, authenticates, then subscribes to `state_changed`. Filter locally to:

- selected Satellite;
- configured players;
- integration-created Satellite media player.

Record monotonic and HA timestamps for:

- Satellite/listening state transitions;
- player state and volume transitions;
- media identity hash changes;
- restoration.

Redacted JSON output:

```json
{
  "satellite": "assist_satellite.kitchen",
  "players": {
    "media_player.kitchen": {
      "initial_volume": 0.65,
      "minimum_volume": 0.10,
      "duck_latency_ms": 180,
      "restore_latency_ms": 420,
      "started_during_session": true,
      "restored": true,
      "unexpected_transitions": []
    }
  }
}
```

Do not include titles, URLs, content IDs, tokens, or transcripts.

### 9.3 Route the Satellite to Pipecat on the dev box

For end-to-end External Transport testing without deploying experimental Pipecat to Loki:

1. Run the candidate Pipecat server on `kratos`, bound to `0.0.0.0` on a temporary LAN port, with a fresh test bearer token supplied only through environment variables.
2. Create a temporary HA External Conversation Service whose URL is `ws://10.1.0.40:<port>/transport/v1` (or the current dev-box LAN address).
3. Create a dedicated non-destructive profile and assign only the test Satellite.
4. Confirm HA can reach `/transport/v1`; do not expose the port outside the trusted LAN.
5. Restore the Satellite's original service/profile and delete the temporary service after testing.

This routing tests experimental server behavior while the Media Interference Guard itself runs in the candidate custom component deployed to HA.

### 9.4 Deploy candidate Satellite code

Use the existing development workflow only after unit tests pass:

```bash
cd /home/brennan/repos/voice-satellite-card-integration
npm run build
HA_DEPLOY_TARGET=/mnt/hassio/config node scripts/deploy-ha.js
```

Reload/restart the custom integration as required. Record the pre-test installed version and restore the released build if acceptance fails. Never overwrite production configuration without an explicit backup and operator confirmation.

### 9.5 Live scenarios

Run each scenario three times and retain probe JSON plus the matching Pipecat session ID:

A. Existing music at 60–70%, duck mode 10%, ask a state-only question.

B. No music initially; ask the agent to start a known test track on the configured player. Confirm it becomes audible at ≤10%, assistant confirmation remains intelligible, and intended launch volume is restored.

C. While leased, manually set volume above the ceiling. Confirm it re-ducks and restores to the manually selected value.

D. While leased, manually stop or change source. Confirm no stale restoration/restart.

E. Pause mode equivalent.

F. Force transport cancellation/network loss. Confirm restoration still occurs.

G. Two overlapping test Satellite sessions targeting the same player. Confirm no early restore.

### 9.6 Success metrics

Must pass before release:

- Existing-player duck begins within 300 ms of active command capture at p95.
- Newly started player is brought to the temporary ceiling within 500 ms at p95.
- No volume exceeds the configured ceiling for more than 500 ms while a lease is active, excluding unavailable service calls.
- Final restoration occurs within `restore_delay + 500 ms` after final lease release.
- 100% of user stop/source-change cases avoid stale resume or stale volume restoration.
- No player remains ducked/paused after cancellation, disconnect, component unload, or HA shutdown test.
- In at least ten controlled music-background turns, false assistant interruption rate is lower than the recorded baseline and no worse than silence-control turns.

## 10. Implementation sequence

Use fail-first TDD and separate commits:

1. Config constants/options-flow tests and translations.
2. Coordinator state machine tests, then implementation.
3. Overlapping lease/context ownership tests.
4. Pipeline lifecycle tests and hooks.
5. Local-player deduplication regression.
6. Read-only live probe script and tests for redaction/argument safety.
7. Local validation.
8. Candidate deployment and live scenarios.
9. Record measurements in the PR; release only if all acceptance criteria pass.

Do not combine this work with Pipecat ML enhancement. Each feature must be measurable and reversible independently.
