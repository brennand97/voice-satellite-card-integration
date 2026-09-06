# External Transport session profiles and generic HA conversation plan

## Status

Research and design only. This document spans:

- `voice-satellite-card-integration` (Home Assistant configuration, session context, and UI)
- `pipecat-external-voice-transport` (trusted tool policy and provider session construction)

No profile or generic-conversation behavior described here is implemented yet.

## Goals

1. Configure External Transport sessions from Home Assistant without exposing credentials to browsers.
2. Support reusable per-session profiles for prompt, voice, and tool policy.
3. Keep the Pipecat deployment authoritative over every executable tool.
4. Let a real satellite session supply the HA device ID needed by device-scoped features such as timers.
5. Add an admin-only generic conversation launcher in the existing HA sidebar panel that requires no configured satellite and intentionally has no timer/device support.
6. Support exact tool names and anchored suffix wildcards in trusted MCP allowlists.
7. Compile every session through one clean, immutable Pipecat session model rather than distributing profile/tool conditionals across providers.
8. Keep all provider calls, event streams, payloads, and teardown bounded and auditable.

## Research findings

### Existing Voice Satellite configuration

- External Transport URL, bearer token, TLS verification, and ready timeout are stored in each satellite config entry's options. This must be refactored before a generic launcher can work with zero Satellite entries.
- The browser never receives those credentials; Home Assistant owns the provider WebSocket.
- A persistent `ExternalConversationRuntime` is created per satellite entity and reused across turns until terminal closure.
- Browser presentation settings already have a server-backed per-satellite store, but its WebSocket save endpoint is not an appropriate security boundary for prompts or tool permissions.
- The integration already registers a custom HA sidebar panel, so no new frontend registration mechanism is required for a generic conversation launcher.

### Home Assistant native text APIs

`conversation/process` accepts text, agent, conversation ID, device ID, and satellite ID. It does not select External Transport and does not expose per-request provider voice or tool policy.

`assist_pipeline/run` supports text input without a device, but it runs an HA Assist pipeline. It does not connect to the External Transport server and its public WebSocket schema does not provide the replacement system-prompt/voice/tool-profile contract needed here.

Therefore a satellite-free External Transport console requires a custom integration WebSocket command rather than repurposing either native endpoint.

### Home Assistant MCP and timers

Home Assistant's standard `/api/mcp` creates its LLM context with `device_id=None`. HA's intent LLM platform exposes timer tools only when the context device has registered timer support. Consequently, timer intent tools are absent from the current Pipecat MCP session even if their names are added to Pipecat's allowlist.

Voice Satellite timers are attached to the satellite's HA device. Its existing `voice_satellite.start_timer` service intentionally calls HA's `TimerManager`, which updates `active_timers`, emits timer lifecycle events, and drives the existing timer pills/alerts.

Timer integration therefore requires a scoped Voice Satellite tool or context injection; merely widening the current HA MCP allowlist cannot enable it.

## Explicit non-goals

- Do not create a virtual, synthetic, or ephemeral Satellite/device for the generic conversation launcher.
- Do not provide timer UI or timer tools to generic launcher sessions.
- Do not let clients send MCP endpoints, credentials, provider definitions, arbitrary wildcard policies, or contextual argument values.
- Do not put profile compilation logic in the OpenAI realtime service or event sink.

## Trust model

### Deployment authority

The Pipecat server owns the maximum capability set. Its trusted, read-only configuration defines:

- MCP/script providers;
- credentials by environment-variable reference;
- named tool profiles;
- the maximum allowlist for each profile;
- optional server-injected arguments;
- concurrency and timeout limits.

A client may request only a profile name and, optionally, an exact-name subset of that profile's tools. The server rejects unknown profiles and compiles the effective tools from discovered names:

```text
provider_allowed = matches(provider_allow_patterns, discovered_tool_name)
profile_allowed  = matches(profile_allow_patterns, discovered_tool_name)
client_requested = requested_subset is omitted or discovered_tool_name in requested_subset
effective_tool   = provider_allowed and profile_allowed and client_requested
```

Trusted provider/profile allowlists accept exact names or one terminal `*`, for example `intent__Hass*`. Matching is anchored to the whole tool name; `*` has no glob/regex semantics elsewhere. Reject empty prefixes, bare `*`, embedded/multiple wildcards, duplicate patterns, and patterns that match no discovered tool. A suffix wildcard intentionally authorizes future discovered tools with that prefix, so capability previews and audit logs must show the expanded concrete names. Wire-level `requested_tools` remains exact-name-only so a client cannot request an open-ended future capability. An omitted subset means the complete server-approved profile. An empty result fails closed rather than silently falling back to broader tools.

### Home Assistant authority

Home Assistant owns:

- profile records and satellite-to-profile assignments;
- selection of an approved server profile name;
- session prompt and voice values;
- the current satellite entity/device context;
- admin-only generic-conversation overrides.

HA stores only External Transport connection credentials in dedicated config entries. It never stores MCP/OpenAI credentials in session profiles and never sends any External Transport credential to the frontend.

### Browser authority

Normal cards may start/stop their assigned session but cannot change security-sensitive tool policy. Only an HA administrator may create/edit profiles or submit arbitrary prompts/voices/tool subsets through the generic conversation launcher.

## External Conversation Service config entries

Extend the integration config flow with an entry kind dedicated to an External Transport connection. It is not a Satellite entity/device and creates no entity platforms. It stores only backend connection data:

- stable connection ID and display name;
- External Transport URL and bearer token;
- TLS verification;
- ready/connect timeout.

The integration can then load and register its sidebar panel with only this service entry configured. Profiles reference the stable connection ID. Both real Satellite runtimes and the generic conversation broker resolve credentials from that entry; neither copies credentials into profile storage.

For backward compatibility, existing Satellite-local connection options continue to work during migration. Provide an admin migration action that creates a service connection from those options and updates the Satellite's profile/connection reference. Never silently choose credentials from the first available Satellite.

## Proposed data model

Create a separate versioned HA Store, not the existing panel settings store:

```json
{
  "profiles": {
    "default-home": {
      "name": "Default home assistant",
      "connection_id": "primary-external",
      "initial_prompt": null,
      "initial_voice": null,
      "server_tool_profile": "home-default",
      "requested_tools": null,
      "timer_access": true
    },
    "read-only-test": {
      "name": "Read-only testing",
      "connection_id": "primary-external",
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

- stable opaque profile and connection IDs separate from display names;
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

Allow `session.start` to identify either a real satellite (the existing shape) or a generic client. Existing Satellite clients remain valid; the generic HA launcher sends a client identity without pretending to be a satellite:

```json
{
  "client": {"id": "ha-external-console", "name": "External conversation console", "kind": "ha_console"},
  "conversation": {
    "profile": "home-read-only",
    "requested_tools": ["homeassistant__GetLiveContext"],
    "initial_prompt": "...",
    "initial_voice": "ballad",
    "device_id": null
  }
}
```

For a real Satellite runtime, HA supplies both the existing satellite entity identity and its registry `device_id`. The generic conversation launcher always sends `device_id: null` and has no timer/device context. `initial_prompt` and `initial_voice` already exist server-side. Add bounded validation for client identity, profile, requested tools, and device ID.

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
      "allowed_tools": ["homeassistant__GetLiveContext", "intent__Hass*"]
    }
  },
  "profiles": {
    "home-read-only": {
      "providers": ["home-assistant"],
      "allowed_tools": ["homeassistant__GetLiveContext"]
    },
    "home-default": {
      "providers": ["home-assistant"],
      "allowed_tools": ["homeassistant__GetLiveContext", "intent__Hass*"]
    }
  },
  "default_profile": "home-default"
}
```

Parse and validate this file once at application startup into immutable definitions. Expand exact/trailing-wildcard policy only after MCP discovery, against the concrete names returned for that session. Keep MCP transport contexts session-scoped and task-affine as they are today.

Audit metadata should include requested/effective profile and effective tool names. Prompt text remains available only under explicit `debug_content` policy and must be redacted like other sensitive content.

## Pipecat session compilation model

Keep session construction independent of OpenAI and MCP implementation details. Use a single compilation path with small immutable models:

```text
ParsedSessionStart
  -> SessionContext
  -> SessionProfileResolver
  -> SessionToolPlanner
  -> CompiledSessionPlan
  -> AgentSessionFactory
```

Suggested responsibilities:

- `SessionContext`: client kind/ID, optional satellite entity ID, optional HA device ID, conversation ID, and audit correlation IDs.
- `SessionProfileDefinition`: prompt/voice defaults, provider references, trusted allow patterns, and contextual-tool rules loaded at startup.
- `SessionProfileResolver`: combines deployment default, named profile, and allowed per-session prompt/voice/exact-tool overrides. It performs no network I/O.
- `ToolNamePattern`: one canonical exact-or-terminal-wildcard parser/matcher reused by provider and profile policies.
- `SessionToolPlanner`: discovers provider tools, applies provider/profile/request filters, checks context requirements, transforms schemas, and returns concrete bindings.
- `ContextInjection`: declares hidden arguments such as `device_id`, their source in `SessionContext`, and whether absence disables the tool.
- `CompiledToolBinding`: immutable model-facing schema plus provider-facing name, fixed/injected arguments, timeout, and provider handle.
- `CompiledSessionPlan`: final system prompt, voice, effective profile, concrete tool bindings, limits, and non-secret diagnostic summary.
- `AgentSessionFactory`: consumes only `CompiledSessionPlan`; the OpenAI provider and Pipecat bridge do not resolve profiles, wildcard policies, or HA context.

The tool registry dispatches only `CompiledToolBinding` objects. At invocation it validates model arguments against the transformed public schema, injects trusted context arguments into a separate provider argument object, and then calls the provider. Never mutate model arguments in place.

All profile/pattern/context errors are compilation failures before OpenAI connects. This avoids partial sessions and keeps policy logic out of realtime frame handling.

## Context-bound Voice Satellite tools

Add a Voice Satellite LLM tool platform/API contribution for timer operations, beginning with a namespaced tool such as:

```text
voice_satellite__StartTimer
```

Standard HA MCP has no device context, so the custom HA/MCP timer tool must accept a `device_id` provider argument. The model must never see or choose that argument. The compiled Pipecat tool plan applies trusted contextual injection:

- accept the device ID only from the authenticated HA backend after it resolves the Satellite's entity-registry device; never accept it from browser profile/test fields;
- require `SessionContext.home_assistant_device_id` to be present;
- omit the timer tool entirely when that field is absent, including every generic console session;
- remove `device_id` from the model-facing OpenAI schema;
- inject the exact session device ID only in the provider-facing call;
- reject model attempts to provide hidden/contextual fields;
- expose the timer tool only when its server profile also permits it.

The HA tool validates that the injected device ID belongs to a registered timer-capable Voice Satellite device, then calls `TimerManager` through the same logic as `voice_satellite.start_timer`. A merely non-empty or syntactically valid ID is not sufficient. This preserves timer pills, updates, cancellation, and alerts on the real device.

Later timer tools may include cancel/status/pause/resume/add/remove time, but they must reuse HA's timer manager and the same compiled device-context requirement.

## HA runtime profile resolution

At creation of a new `ExternalConversationRuntime`:

1. Resolve the satellite's assigned HA profile.
2. Validate it against the local schema.
3. Resolve the entity registry entry and snapshot its real HA device ID.
4. Snapshot prompt, voice, server profile name, and requested subset.
5. Create `SessionStart` with that snapshot, satellite identity, and device ID.
6. Verify `session.ready` reports the expected effective profile/tools.
7. Record only non-secret effective metadata in diagnostics.

A persistent provider WebSocket cannot change prompt, voice, or tools safely mid-session. Editing/reassigning a profile must terminally close the current runtime; the next wake creates a new runtime with the new snapshot. Never mutate an active provider conversation in place.

If profile resolution fails, show one explicit configuration error and do not fall back to a broader profile.

## Admin profile UI

Add an admin-only section to the existing Voice Satellite sidebar panel:

- list/create/duplicate/delete profiles;
- assign profiles to satellite entities;
- prompt editor with replacement semantics clearly labeled;
- provider voice text/select field;
- External Conversation Service connection selector;
- server profile selector populated from a safe server capability endpoint or manually configured known IDs;
- requested-tool checklist constrained to the selected server profile's advertised names;
- timer-access indicator explaining that only real Satellite sessions carry device context;
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

## Generic HA conversation launcher

Add an admin-only text conversation launcher to the existing HA sidebar panel. It is not a Satellite, does not create or impersonate an HA device, and has no timer support. It communicates only with a custom HA WebSocket broker:

```text
voice_satellite/external_conversation/start
voice_satellite/external_conversation/turn
voice_satellite/external_conversation/cancel
```

The HA backend owns the External Transport client and credentials. The browser receives sanitized lifecycle/text/tool events and signed audio URLs through the existing event normalization path.

Conversation start options:

- profile ID;
- optional one-session prompt override;
- optional one-session voice override;
- optional exact-name requested tool subset;
- text-only input initially.

Every launcher session:

- uses a generic protocol client identity such as `voice_satellite.external_conversation`;
- sends no satellite entity and `conversation.device_id: null`;
- permits only ordinary tools compiled from its approved server profile;
- automatically omits every context-required tool, including timers;
- does not infer a room or area from a device;
- renders transcript/tool lifecycle/audio in the panel itself;
- guarantees bounded idle and maximum-session shutdown;
- cannot displace or mutate a physical Satellite runtime.

### Why not use HA's default conversation view

HA's standard conversation UI invokes `conversation/process`, which selects HA conversation agents and cannot carry External Transport prompt/voice/tool-profile configuration. Reusing its visual component may be possible later, but its backend command cannot provide the required semantics.

A custom section in the already-registered HA sidebar panel is therefore the smallest correct generic conversation entry point. It requires neither a Voice Satellite config entry nor any synthetic device.

## Existing tool-event prerequisite

Before profile/generic-conversation work, fix and test correlation for tool events emitted after a provider response finishes. The HA `ExternalConversationRuntime` currently clears `_response_id` on `assistant.response_finished` and fences later events whose response ID differs from `None`. OpenAI commonly emits a pre-tool response boundary before `assistant.tool_call_started`; those events can therefore be dropped before reaching the card.

Use a separate tool-operation lease/correlation map rather than treating tool lifecycle as active audio-response ownership. Required tests:

- tool start after a preamble response finish reaches the card;
- tool finish reaches the same turn after response ownership clears;
- stale tool events from an interrupted/replaced turn are ignored;
- multiple parallel tool calls retain distinct call IDs;
- arguments/results remain bounded and are not rendered by the normal card;
- the admin generic conversation view can optionally display redacted debug payloads.

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

### Phase 1 — Server session compiler and named profiles

1. Add immutable `SessionContext`, profile, pattern, contextual-injection, compiled-tool, and compiled-session models.
2. Define a backward-compatible trusted config schema.
3. Parse immutable providers/profiles and exact/trailing-wildcard patterns at startup.
4. Add generic client identity plus session `device_id`, `profile`, and exact `requested_tools` fields.
5. Discover concrete tools and compile provider/profile/request allowlists through one `SessionToolPlanner`.
6. Compile prompt, voice, context-required tools, transformed schemas, and injected arguments before provider startup.
7. Return effective non-secret configuration in `session.ready`.
8. Audit profile compilation and rejection.

### Phase 2 — HA connections, profile store, and satellite assignment

1. Add the entity-free External Conversation Service config-entry kind.
2. Move reusable URL/token/TLS/timeout resolution behind a connection registry.
3. Add the validated versioned profile Store.
4. Add admin-only profile CRUD/assignment WebSocket API.
5. Add connection/profile editor and assignment UI.
6. Resolve and snapshot connection/profile when creating a real Satellite runtime.
7. Recreate runtime after connection/profile changes.
8. Add backward-compatible migration from Satellite-local connection options.

### Phase 3 — Device-context timer tool

1. Register a namespaced Voice Satellite timer tool with HA's LLM tooling.
2. Make `device_id` a required provider argument and validate it as a timer-capable Voice Satellite device in HA.
3. Add server-side hidden contextual argument injection.
4. Compile the tool only when the profile permits it and `SessionContext.home_assistant_device_id` is present.
5. Verify native timer entity updates, pills, finish alert, and cancellation.
6. Verify generic conversation sessions never advertise or invoke the timer tool.

### Phase 4 — Admin generic conversation launcher

1. Refactor an entity-independent HA External session broker from runtime code.
2. Add admin-only start/turn/cancel WebSocket commands.
3. Add panel UI for profile/prompt/voice and exact-name subset, with no satellite/device selector.
4. Always compile launcher sessions with no device context.
5. Render text, audio, and tool lifecycle with privacy controls.
6. Add bounded idle/session timers and terminal cleanup.
7. Add a satellite-free E2E test proving ordinary tools work and context-required tools are absent.

## Acceptance criteria

- An External Conversation Service entry can exist and power the HA panel without any Satellite entity.
- A satellite can be assigned a named HA session profile without browser-visible credentials.
- Prompt and voice overrides apply only to the newly created provider session.
- HA cannot request any tool outside the server-authorized profile.
- Unknown profiles/subsets fail closed with useful diagnostics.
- Exact names and anchored terminal-wildcard patterns select only discovered tools they match.
- A timer request from a real Satellite session with a validated HA device ID creates the existing countdown pill and finish alert.
- A generic conversation session has no device identity and cannot access timer/device-context tools.
- An administrator can run a text-only External conversation from the HA sidebar panel without configuring a satellite.
- OpenAI/Pipecat provider code consumes one immutable compiled session plan and contains no profile/wildcard/HA-device policy branches.
- Normal users cannot edit profiles, widen tools, or run arbitrary prompt experiments.
- Tool arguments/results remain transported but are not rendered by normal Satellite UI.
- All queues, calls, subscriptions, session expiry, and shutdown paths are bounded.
