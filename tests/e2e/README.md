# Local browser integration acceptance

## Boundaries

- **`../ha-integration-testbed`**: reusable HA/HACS lifecycle, isolated networking/ingress, auth, browser helpers, generic core tests and cleanup. Separate shareable checkout; not a production homelab deployment.
- **This repository**: candidate build/copy, synthetic dashboard/light, satellite config flows/options/settings, real component selectors and browser acceptance assertions.
- **`../pipecat-external-voice-transport/tests/e2e`**: test-only deterministic provider image, streamed audio/tool scenarios, provider tests and real-network transport smoke. Its production image/entrypoint are unchanged.
- **`homelab.wiki`**: architectural plan and validation report, not executable test fixtures.

Clone/check out the repositories as siblings. Follow the shared README to install its venv/browser dependency package. Set `HA_TESTBED_ROOT` for a nonstandard shared checkout and `VOICE_TRANSPORT_REPO` for a different transport path; the npm `file:` dependency in this directory must also point at the shared `browser/` package.

## Quick start

```sh
export HA_TESTBED_DOCKER='sudo -n docker' # only if ordinary docker is unavailable
npm run e2e:prepare
npm run e2e:up
# Use the printed run ID:
npm run e2e:verify -- <run-id>         # independent transport/audio/tool acceptance
npm run e2e:test -- <run-id> --core --keep  # generic harness smoke
npm run e2e:test -- <run-id> --keep    # satellite tests; broken product stays RED
npm run e2e:open -- <run-id> --path /voice-satellite
npm run e2e:logs -- <run-id>
npm run e2e:down -- <run-id>
```

The direct shared `ha-testbed test` command only runs Playwright; it does **not** rebuild or copy changed frontend code. After product edits, explicitly run a build and stage the new assets into an owned scratch config, or use `prepare` followed by a fresh `up`. Prefer a fresh run for full acceptance after failed timer tests, whose live HA state may intentionally survive browser teardown.

Browser contexts block service workers to prevent HA's initial `controllerchange` reload from destroying an in-flight scenario. This does not mock HA or application networking; offline cache/update behavior is outside this suite.

`prepare` runs `npm ci` + **build only**, and builds the small transport E2E image. It never runs `npm run dev` / `deploy-ha.js`. Local candidate assets are copied into a new scratch config on every `up`; nothing is mounted into production. No code fixes are applied by the testbed.

`up` succeeds when the generic HA runtime is ready even if a satellite config flow fails. It prints **Adapter setup FAILED**, saves the failure and preserves partial fixtures for debugging. The dedicated options-flow test fails on that condition. Other browser tests still attempt actual component behavior, capturing failures. This is deliberate: an unhealthy product is not an unhealthy testbed, and a failed configuration is not silently bypassed with `.storage` edits.

`test` tears down by default; use `--keep` while debugging. `up` + manual `open` stays up until `down`. `open --headless` saves a screenshot without a graphical display. Normal `open` uses ordinary permission/autoplay rules; automated tests use fake capture/autoplay flags.

## Coverage

- Core HA frontend/WebSocket/auth, synthetic state, real Chromium microphone and visual baseline are independently verified by the shared suite.
- Satellite suite: actual sidebar/assets/persistence; a genuine Lovelace action button sends a **service-origin text turn** (`voice_satellite.show`); microphone capture triggered with `voice_satellite.wake`; decoded/playback progress of the provider's signed audio response; timer render/expiry/dismissal; diagnostics.
- Transport verification: rejects invalid credentials, sequential text/audio turns in one persistent session, nonzero input PCM, valid streamed WAV and session cleanup. When the test MCP fixture is ready, a scripted provider invokes the **real** tool registry and the test HA MCP server to turn on only `light.testbed_light`.
- The backend tone is deterministic synthetic audio, not provider-generated speech. The current external path hands provider URLs to browser playback directly (unlike the separate HA media proxy); the fixture therefore advertises the loopback transport ingress URL, not a Docker-only hostname. Tunnel the printed transport port too if browsing from another workstation. The prerecorded WAV proves browser audio capture, not STT accuracy. Triggered recording is not wake-word recognition.

The fixture does not mock HA/frontend/WebSockets, invoke private pipeline JS as its acceptance path, or report green just because a screenshot looks right. Its test-only provider injection is the only fake model boundary; production actor/protocol/proxy/tool code stays real.

## HACS release gate

HACS files are present even without GitHub authorization. Local mode tests unpublished working-tree code; it is **not** a HACS download test.

Use the shared README's **core online run** to personally authorize test HACS and export a private seed with no satellite entry. Then:

```sh
npm run e2e:up -- --online --seed "$HOME/.local/state/agents/ha-integration-testbed/seeds/hacs" --release <published-tag>
npm run e2e:verify -- <run-id>
npm run e2e:test -- <run-id>
```

The adapter registers this fork through HACS's actual API, downloads the explicit tag, verifies installed tag and packaged frontend, records the candidate hash/version and restarts HA before setup. It refuses unauthenticated/offline release mode. No release is published by these commands. This path needs human GitHub OAuth; without that seed its runtime verification remains pending, not simulated.

## Initial-delivery regressions (subsequently patched during live debugging)

See `homelab.wiki/Voice-Kiosk-Testbed-Validation.md` for the measured run. In the inspected revision, the real satellite options flow rejects valid media-guard values with `invalid_media_guard`. NumberSelector coercion appears inconsistent with the flow's strict integer checks. The fixture records this and **does not patch the product or bypass the flow**; external-profile assignment and dependent voice tests consequently fail. This is now reproducible locally.

The expanded debugging request authorized selector normalization, the missing session import, terminal-session cleanup, idle-restart generation fences and replacement-show handling. Current live results and remaining failures supersede the initial report above; this is not certification of all features.

For changes: fix/debug in the product, `npm run build`, `npm run e2e:up`, then rerun the same assertions. If transport source changes, rerun `e2e:prepare` to rebuild the image. Logs, runtime manifest and screenshots identify the actual failure boundary.

## Synthetic kitchen acoustics

Generate fixed public synthetic phrases using only the inventoried billable test key (never production credentials). Waveforms and reports stay in private scratch state:

```sh
cd ../pipecat-external-voice-transport
.venv/bin/python tests/e2e/acoustics.py <private-corpus-directory>
# Back in the satellite checkout:
HA_TESTBED_ACOUSTICS_DIR=<private-corpus-directory> npm run e2e:test -- <run-id> --keep --grep 'acoustic: real'
```

The 11-case matrix uses actual Chromium fake-device capture and shipped microWakeWord/TFLite, Hey Jarvis, moderate sensitivity. It separately records detection and downstream PCM. The deterministic reply is **not** recognition evidence. Eight seconds of initialization padding are excluded from SNR calculation; noise-only and unrelated-speech clips must not open a conversation. Severe-profile misses remain ordinary failing assertions, not skipped tests.

For continuous speech, generate with `--continuous --source-directory <cached-corpus>`: trimmed synthesis padding and a 120ms wake/command gap. Realtime VAD can legitimately treat a one-second pause as an utterance boundary; file-device reacquisition also restarts the clip unlike a physical microphone.

A supervised credentialed test server (`transport/tests/e2e/live_server.py`) plus the shared fixed Unix bridge allows real OpenAI -> scratch MCP without giving HA an outbound network route. `HA_TESTBED_LIVE_OPENAI=1` changes assertions to actual twenty-second Pasta tool results and independent MCP timer truth, not fake captions. That mode must only target a deliberately switched, owned scratch runtime. Stop the supervised source and tear down the labeled bridge after testing.

## Not yet certified

HACS device authorization/release download until a human authorizes the test seed; native HA STT/TTS; acoustic wake accuracy; Android native bridge/background/screen-off behavior; real echo/speaker quality; complete browser interruption/overlapping-media-guard scenarios. Provider cancellation has independent deterministic tests. These are separate acceptance gates, not implied by passing the core smoke suite.
