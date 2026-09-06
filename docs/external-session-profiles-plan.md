# External Transport session profiles and HA test console plan

## Status

Research and design only. This document spans:

- `voice-satellite-card-integration` (Home Assistant configuration, session context, and UI)
- `pipecat-external-voice-transport` (trusted tool policy and provider session construction)

No profile or test-console behavior described here is implemented yet.

## Goals

1. Configure External Transport sessions from Home Assistant without exposing credentials to browsers.
2. Support reusable per-session profiles for prompt, voice, and tool policy.
3. Keep the Pipecat deployment authoritative over every executable tool.
4. Let a satellite profile supply context needed by device-scoped features such as timers.
5. Add an admin-only text test console in the existing HA sidebar panel that can run without a configured satellite for non-device-scoped tools.
6. Optionally bind a test session to an existing satellite when device context or timer behavior is required.
7. Keep all provider calls, event streams, payloads, and teardown bounded and auditable.

## Research findings

### Existing Voice Satellite configuration

- External Transport URL, bearer token, TLS verification, and ready timeout are stored in each satellite config entry's options.
- The browser never receives those credentials; Home Assistant owns the provider WebSocket.
- A persistent `ExternalConversationRuntime` is created per satellite entity and reused across turns until terminal closure.
- Browser presentation settings already have a server-backed per-satellite store, but its WebSocket save endpoint is not an appropriate security boundary for prompts or tool permissions.
- The integration already registers a custom HA sidebar panel, so no new frontend registration mechanism is required for a test console.

### Home Assistant native text APIs

`conversation/process` accepts text, agent, conversation ID, device ID, and satellite ID. It does not select External Transport and does not expose per-request provider voice or tool policy.

`assist_pipeline/run` supports text input without a device, but it runs an HA Assist pipeline. It does not connect to the External Transport server and its public WebSocket schema does not provide the replacement system-prompt/voice/tool-profile contract needed here.

Therefore a satellite-free External Transport console requires a custom integration WebSocket command rather than repurposing either native endpoint.

### Home Assistant MCP and timers

Home Assistant's standard `/api/mcp` creates its LLM context with `device_id=None`. HA's intent LLM platform exposes timer tools only when the context device has registered timer support. Consequently, timer intent tools are absent from the current Pipecat MCP session even if their names are added to Pipecat's allowlist.

Voice Satellite timers are attached to the satellite's HA device. Its existing `voice_satellite.start_timer` service intentionally calls HA's `TimerManager`, which updates `active_timers`, emits timer lifecycle events, and drives the existing timer pills/alerts.

Timer integration therefore requires a scoped Voice Satellite tool or context injection; merely widening the current HA MCP allowlist cannot enable it.

## Trust model

### Deployment authority

The Pipecat server owns the maximum capability set. Its trusted, read-only configuration defines:

- MCP/script providers;
- credentials by environment-variable reference;
- named tool profiles;
- the maximum allowlist for each profile;
- optional server-injected arguments;
- concurrency and timeout limits.

A client may request only a profile name and, optionally, a subset of that profile's tools. The server rejects unknown profiles and computes:

```text
effective_tools = requested_subset ∩ server_profile_allowlist
```

An omitted subset means the complete server-approved profile. An empty intersection fails closed rather than silently falling back to broader tools.

### Home Assistant authority

Home Assistant owns:

- profile records and satellite-to-profile assignments;
- selection of an approved server profile name;
- session prompt and voice values;
- the current satellite entity/device context;
- admin-only test-console overrides.

HA never stores MCP/OpenAI credentials in session profiles and never sends External Transport credentials to the frontend.

### Browser authority

Normal cards may start/stop their assigned session but cannot change security-sensitive tool policy. Only an HA administrator may create/edit profiles or submit arbitrary test prompts/voices/tool subsets through the test console.

## Proposed data model

Create a separate versioned HA Store, not the existing panel settings store:

```json
{
  "profiles": {
    "default-home": {
      "name": "Default home assistant",
      "initial_prompt": null,
      "initial_voice": null,
      "server_tool_profile": "home-default",
      "requested_tools": null,
      "timer_access": true
    },
    "read-only-test": {
      "name": "Read-only testing",
      "initial_prompt": "...",
      "initial_voice": "ballad",
      "server_tool_profile": "home-read-only",
      "requested_tools": ["homeassistant__GetLiveContext"],
      "timer_access": false
    }
  },
  "assignments": {
    "assist_satellite.kitchen": "default-home"
  }
}
```

Validation requirements:

- stable opaque profile IDs separate from display names;
- prompt: null or non-empty, at most 16,000 UTF-8 bytes;
- voice: null or non-empty, at most 128 UTF-8 bytes;
- profile/tool names: conservative identifier syntax and bounded count/length;
- requested tools: unique string list;
- no URL, bearer token, API key, or arbitrary script command fields;
- unknown keys rejected;
- migration/versioning support;
- diagnostics redact prompt content and show only profile IDs/tool names.

Assignment should live in this profile store or config-entry options. Do not expose tool-profile assignment as an unrestricted HA `select` entity because changing it changes executable capability.

## External Transport protocol additions

Extend additive v1 `session.start.conversation` fields:

```json
{
  "profile": "home-default",
  "requested_tools": ["homeassistant__GetLiveContext"],
  "initial_prompt": "...",
  "initial_voice": "ballad"
}
```

`initial_prompt` and `initial_voice` already exist server-side. Add bounded `profile` and `requested_tools` validation.

The server should return effective, non-secret session configuration in `session.ready`:

```json
{
  "effective_profile": "home-default",
  "effective_tools": ["homeassistant__GetLiveContext"],
  "effective_voice": "ballad"
}
```

Do not echo prompt content. A prompt hash may be returned for diagnostics if useful.

Compatibility:

- no profile field uses a deployment-defined default profile;
- the existing flat tool config becomes the implicit `default` profile;
- older clients remain valid;
- unknown profiles fail with a stable `unknown_tool_profile` protocol error.

## Server-side named tool profiles

Evolve `voice-tools.json` without allowing wire-supplied provider definitions:

```json
{
  "providers": {
    "home-assistant": {
      "transport": "streamable_http",
      "url": "https://homeassistant.example/api/mcp",
      "bearer_token_env": "HOMEASSISTANT_MCP_TOKEN",
      "allowed_tools": ["homeassistant__GetLiveContext", "intent__HassTurnOn"]
    }
  },
  "profiles": {
    "home-read-only": {
      "providers": ["home-assistant"],
      "allowed_tools": ["homeassistant__GetLiveContext"]
    },
    "home-default": {
      "providers": ["home-assistant"],
      "allowed_tools": ["homeassistant__GetLiveContext", "intent__HassTurnOn"]
    }
  },
  "default_profile": "home-default"
}
```

Parse and validate this file once at application startup into immutable definitions. Build a session-scoped registry from the selected profile. Keep MCP transport contexts session-scoped and task-affine as they are today.

Audit metadata should include requested/effective profile and effective tool names. Prompt text remains available only under explicit `debug_content` policy and must be redacted like other sensitive content.

## Context-bound Voice Satellite tools

Add a Voice Satellite LLM tool platform/API contribution for timer operations, beginning with a namespaced tool such as:

```text
voice_satellite__StartTimer
```

Its public HA/MCP schema may need a satellite target because standard HA MCP has no device context. The model must not choose that target. Add trusted server-side contextual argument injection:

- hide `entity_id` from the model-facing OpenAI schema;
- inject the authenticated session's `satellite_entity_id` when calling HA MCP;
- reject the call when the session has no satellite target;
- reject target override attempts;
- expose the timer tool only in profiles that allow it and sessions with a valid target.

The HA tool calls `TimerManager` through the same logic as `voice_satellite.start_timer`, preserving timer pills, updates, cancellation, and alerts.

Later timer tools may include cancel/status/pause/resume/add/remove time, but they should reuse HA's timer manager and remain scoped to the bound satellite device.

## HA runtime profile resolution

At creation of a new `ExternalConversationRuntime`:

1. Resolve the satellite's assigned HA profile.
2. Validate it against the local schema.
3. Snapshot prompt, voice, server profile name, and requested subset.
4. Create `SessionStart` with that snapshot and satellite identity.
5. Verify `session.ready` reports the expected effective profile/tools.
6. Record only non-secret effective metadata in diagnostics.

A persistent provider WebSocket cannot change prompt, voice, or tools safely mid-session. Editing/reassigning a profile must terminally close the current runtime; the next wake creates a new runtime with the new snapshot. Never mutate an active provider conversation in place.

If profile resolution fails, show one explicit configuration error and do not fall back to a broader profile.

## Admin profile UI

Add an admin-only section to the existing Voice Satellite sidebar panel:

- list/create/duplicate/delete profiles;
- assign profiles to satellite entities;
- prompt editor with replacement semantics clearly labeled;
- provider voice text/select field;
- server profile selector populated from a safe server capability endpoint or manually configured known IDs;
- requested-tool checklist constrained to the selected server profile's advertised names;
- timer-access indicator explaining that a real satellite target is required;
- unsaved-change and active-session-restart warnings.

Backend WebSocket commands should all use `@websocket_api.require_admin`:

```text
voice_satellite/external_profiles/list
voice_satellite/external_profiles/save
voice_satellite/external_profiles/delete
voice_satellite/external_profiles/assign
voice_satellite/external_profiles/server_capabilities
```

The current panel-settings endpoints must not be reused for this data.

## Satellite-free test console

### Feasible first version

Add an admin-only text console to the existing HA sidebar panel. It communicates only with a custom HA WebSocket broker:

```text
voice_satellite/external_test/start
voice_satellite/external_test/turn
voice_satellite/external_test/cancel
```

The HA backend owns the External Transport client and credentials. The browser receives sanitized lifecycle/text/tool events and signed audio URLs through the existing event normalization path.

Test start options:

- profile ID;
- optional one-session prompt override;
- optional one-session voice override;
- optional requested tool subset;
- optional existing satellite target;
- text-only input initially.

Without a satellite target:

- use a synthetic protocol client identity such as `voice_satellite.test_console`;
- permit stateless/read-only and ordinary approved HA tools;
- do not expose timer/device-context tools;
- do not claim an area inferred from a device;
- render transcript/tool lifecycle/audio in the panel itself;
- guarantee bounded idle and maximum-session shutdown.

With an existing satellite target:

- inject that entity/device context for timer-capable tools;
- clearly label that real home/device actions can occur;
- optionally mirror timer UI on that satellite only when explicitly selected;
- prevent simultaneous test and physical sessions from silently displacing each other; require confirmation or use a separate provider session while retaining the target solely for tool context.

### Why not use HA's default conversation view

HA's standard conversation UI invokes `conversation/process`, which selects HA conversation agents and cannot carry External Transport prompt/voice/tool-profile configuration. Reusing its visual component may be possible later, but its backend command cannot provide the required semantics.

A custom panel section is therefore the smallest correct implementation. It is still a normal HA sidebar view and does not require a configured Voice Satellite entity for basic text testing.

### Virtual test satellite stretch goal

A truly timer-capable session with no real satellite would require an ephemeral or persistent HA device that registers as timer-capable and owns timer event presentation. That introduces device registry lifecycle, timer handler ownership, alert routing, cleanup after restart, and ambiguous physical output. Do not synthesize a device in the first implementation.

If later required, implement an explicit `External Test Console` config entry/device rather than creating transient registry entries. Its panel—not a physical card—would own timer pills and alerts.

## Existing tool-event prerequisite

Before profile/test-console work, fix and test correlation for tool events emitted after a provider response finishes. The HA `ExternalConversationRuntime` currently clears `_response_id` on `assistant.response_finished` and fences later events whose response ID differs from `None`. OpenAI commonly emits a pre-tool response boundary before `assistant.tool_call_started`; those events can therefore be dropped before reaching the card.

Use a separate tool-operation lease/correlation map rather than treating tool lifecycle as active audio-response ownership. Required tests:

- tool start after a preamble response finish reaches the card;
- tool finish reaches the same turn after response ownership clears;
- stale tool events from an interrupted/replaced turn are ignored;
- multiple parallel tool calls retain distinct call IDs;
- arguments/results remain bounded and are not rendered by the normal card;
- the admin test console can optionally display redacted debug payloads.

Add a `tool_call_id` to start/finish wire events. A response ID alone is insufficient for parallel and post-response tool calls.

## Payload and privacy limits

Tool request/results now cross the protocol but can be large. Before exposing them to a console:

- cap serialized arguments and results independently;
- define truncation metadata rather than silently cutting invalid JSON;
- never carry PCM/audio in tool payloads;
- redact credential-like fields and signed URL query strings server-side;
- normal Satellite UI consumes only tool name/status;
- debug payload display is admin-only and opt-in;
- never include prompts/tool content in normal HA diagnostics;
- retain bounded queue behavior under large or rapid tool events.

## Phased implementation

### Phase 0 — Correct tool lifecycle correlation

1. Add `tool_call_id` server-side.
2. Decouple tool-event correlation from active response leases.
3. Fix HA runtime fencing for post-response tool events.
4. Add request/result size and redaction policy.
5. Run live OpenAI → HA MCP → Satellite visualization acceptance test.

### Phase 1 — Server named profiles

1. Define backward-compatible trusted config schema.
2. Parse immutable providers/profiles at startup.
3. Add session `profile` and `requested_tools` fields.
4. Intersect requested tools with server allowlists.
5. Return effective non-secret configuration in `session.ready`.
6. Audit profile resolution and rejection.

### Phase 2 — HA profile store and satellite assignment

1. Add validated versioned profile Store.
2. Add admin-only CRUD/assignment WebSocket API.
3. Add profile editor/assignment UI.
4. Resolve and snapshot profile when creating runtime.
5. Recreate runtime after profile changes.
6. Add migration/default assignment behavior.

### Phase 3 — Context-bound timer tool

1. Register namespaced Voice Satellite timer tool with HA's LLM tooling.
2. Add server-side hidden contextual argument injection.
3. Expose only when profile permits and a satellite target exists.
4. Verify native timer entity updates, pills, finish alert, and cancellation.
5. Add explicit no-target model error for test-console sessions.

### Phase 4 — Admin text test console

1. Refactor an entity-independent HA External session broker from runtime code.
2. Add admin-only start/turn/cancel WebSocket commands.
3. Add panel UI for profile/prompt/voice/subset and optional satellite target.
4. Render text, audio, and tool lifecycle with privacy controls.
5. Add bounded idle/session timers and terminal cleanup.
6. Add a no-satellite read-only E2E test and a target-bound timer test.

### Phase 5 — Optional virtual test device

Only after the prior phases are stable, evaluate a dedicated config-entry-backed test device for timer UI without a physical satellite.

## Acceptance criteria

- A satellite can be assigned a named HA session profile without browser-visible credentials.
- Prompt and voice overrides apply only to the newly created provider session.
- HA cannot request any tool outside the server-authorized profile.
- Unknown profiles/subsets fail closed with useful diagnostics.
- A timer request on a bound satellite creates the existing countdown pill and finish alert.
- A no-satellite test session cannot access timer/device-context tools.
- An administrator can run a text-only External session from the HA sidebar panel without configuring a satellite.
- Normal users cannot edit profiles, widen tools, or run arbitrary prompt experiments.
- Tool arguments/results remain transported but are not rendered by normal Satellite UI.
- All queues, calls, subscriptions, session expiry, and shutdown paths are bounded.
