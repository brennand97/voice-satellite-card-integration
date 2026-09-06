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
5. Expose each profile as a native HA `ConversationEntity` that works in the standard conversation view with no configured Satellite and intentionally has no timer/device support.
6. Support exact tool names and anchored suffix wildcards in trusted MCP allowlists.
7. Compile every session through one clean, immutable Pipecat session model rather than distributing profile/tool conditionals across providers.
8. Keep all provider calls, event streams, payloads, and teardown bounded and auditable.

## Research findings

### Existing Voice Satellite configuration

- External Transport URL, bearer token, TLS verification, and ready timeout are stored in each Satellite config entry's options. This must be refactored before a conversation entity can work with zero Satellite entries.
- The browser never receives those credentials; Home Assistant owns the provider WebSocket.
- A persistent `ExternalConversationRuntime` is created per satellite entity and reused across turns until terminal closure.
- Browser presentation settings already have a server-backed per-satellite store, but its WebSocket save endpoint is not an appropriate security boundary for prompts or tool permissions.
- The integration already registers a custom HA sidebar panel, which can later host an optional admin Prompt Lab without defining the core satellite-free model.

### Home Assistant native conversation model

`conversation.ConversationEntity` is the correct native abstraction for an External Transport model backend. Once the integration registers such an entity as an agent, HA's standard `conversation/process` command and conversation UI can select it with an `agent_id`, conversation ID, and no Satellite/device.

A conversation entity receives HA's `ChatLog`. The log supports streamed assistant text and externally executed tool calls/results (`ToolInput.external=True`), allowing Pipecat tool lifecycle to appear in HA's standard conversation view without HA executing the same tool again.

Modern HA model integrations use one credential-bearing parent config entry and one `conversation` config subentry per configured agent/profile. This fits External Transport better than a custom profile Store.

`assist_pipeline/run` remains useful when STT/TTS orchestration is wanted, but it is not the model abstraction. It can point at a pipeline whose conversation agent is our entity; without a physical Satellite, any TTS is HA pipeline TTS rather than OpenAI Realtime audio.

The standard `conversation/process` schema does not carry arbitrary per-request prompt, voice, or tool policy. Persistent profile values belong in conversation subentries. A separate admin Prompt Lab may add ephemeral overrides later, but the basic satellite-free conversation requires no custom transport-specific launcher command.

### Home Assistant MCP and timers

Home Assistant's standard `/api/mcp` creates its LLM context with `device_id=None`. HA's intent LLM platform exposes timer tools only when the context device has registered timer support. Consequently, timer intent tools are absent from the current Pipecat MCP session even if their names are added to Pipecat's allowlist.

Voice Satellite timers are attached to the satellite's HA device. Its existing `voice_satellite.start_timer` service intentionally calls HA's `TimerManager`, which updates `active_timers`, emits timer lifecycle events, and drives the existing timer pills/alerts.

Timer integration therefore requires a scoped Voice Satellite tool or context injection; merely widening the current HA MCP allowlist cannot enable it.

## Explicit non-goals

- Do not create a virtual, synthetic, or ephemeral Satellite/device for generic conversation entities.
- Do not provide timer UI or timer tools to generic conversation-entity sessions.
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
- admin-only ephemeral Prompt Lab overrides, if that optional UI is later added.

HA stores only External Transport connection credentials in dedicated config entries. It never stores MCP/OpenAI credentials in session profiles and never sends any External Transport credential to the frontend.

### Browser authority

Normal cards may start/stop their assigned session but cannot change security-sensitive tool policy. HA's normal conversation UI selects a configured conversation entity; only an administrator may create/edit its profile subentry or use a future ephemeral Prompt Lab.

## External Conversation Service entry and profile subentries

Extend the integration config flow with an `External Conversation Service` parent entry. It is not a Satellite/device. It stores backend connection data:

- stable entry ID and display name;
- External Transport URL and bearer token;
- TLS verification;
- ready/connect timeout.

Following HA's current OpenAI/Anthropic integration model, add one `conversation` config subentry for each External agent profile. Each subentry creates a selectable `conversation` entity and stores:

```yaml
name: Reginold read-only
initial_prompt: null
initial_voice: null
server_tool_profile: home-read-only
requested_tools:
  - homeassistant__GetLiveContext
```

The parent owns credentials; subentries never copy them. A physical Voice Satellite config entry references a parent connection entry plus a conversation subentry ID. HA's standard conversation UI selects the generated entity directly.

This allows the integration and conversation entity to exist with zero Satellite entries. Both real Satellite runtimes and conversation entities resolve credentials from the parent entry. Never borrow credentials from an arbitrary Satellite.

Satellite-local External Transport credentials are unsupported. Every physical Satellite must explicitly reference a parent service entry and conversation profile.

Validation requirements:

- stable config entry/subentry IDs separate from display names;
- prompt: null or non-empty, at most 16,000 UTF-8 bytes;
- voice: null or non-empty, at most 128 UTF-8 bytes;
- server profile/tool names: conservative identifier syntax and bounded count/length;
- requested tools: unique exact-name string list;
- no MCP URL, bearer token, API key, or arbitrary script command in a profile subentry;
- unknown fields rejected;
- diagnostics redact credential/prompt content and show only profile IDs/tool names.

Do not expose profile assignment as an unrestricted HA `select` entity because changing it changes executable capability.

## External Transport protocol additions

Allow `session.start` to identify either a real Satellite (the existing shape) or a generic conversation agent. Existing Satellite clients remain valid; a `ConversationEntity` sends a generic identity without pretending to be a Satellite:

```json
{
  "client": {"id": "conversation.reginold_read_only", "name": "Reginold read-only", "kind": "ha_conversation"},
  "conversation": {
    "profile": "home-read-only",
    "requested_tools": ["homeassistant__GetLiveContext"],
    "initial_prompt": "...",
    "initial_voice": null,
    "device_id": null,
    "output_modalities": ["text"]
  }
}
```

For a real Satellite runtime, HA supplies both the existing Satellite entity identity and its registry `device_id`, and requests text plus audio output. A generic `ConversationEntity` always sends `device_id: null` and text-only output, so it has no timer/device context and does not generate unused OpenAI audio. `initial_prompt` and `initial_voice` already exist server-side. Add bounded validation for client identity, profile, requested tools, device ID, and output modalities. Voice is meaningful only when audio output is selected.

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

- `SessionContext`: client kind/ID, conversation ID, optional immutable attachment context, and audit correlation IDs.
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

## Optional attachment model

Model the provider conversation independently from where it is presented:

```text
ExternalConversationDefinition (connection + profile)
  -> ExternalConversationSession (provider state + turns + compiled tools)
       -> optional ConversationAttachment
```

A `ConversationAttachment` is a bounded capability/context object, not a separate kind of agent. Initial attachment types can be:

- `None`: generic HA `ConversationEntity`; text input/output, no device context.
- `PhysicalSatelliteAttachment`: live PCM input, signed/native audio output, Satellite event presentation, and trusted Satellite entity/device identity.
- `AdminPanelAttachment` (optional later): browser text and optional browser audio for Prompt Lab experiments, but no HA device context.

The shared session factory derives its plan from the profile plus attachment capabilities:

```text
input_modalities
output_modalities
satellite_entity_id?
home_assistant_device_id?
presentation_capabilities
tool_context
```

A generic realtime audio session is therefore valid without a physical Satellite: an admin panel attachment may request audio and play it locally. Conversely, attaching a physical Satellite adds native audio routing and device-scoped tool context without changing the conversation/profile abstraction.

Attachment context must be fixed before compiling/opening a provider session. A transport binding may disconnect and reconnect for the same declared physical attachment, as the current persistent runtime does, but changing between unattached/panel/physical context requires a new provider session. This prevents a live session from silently gaining tools or changing device authority.

Allow at most one controlling attachment (microphone/input owner) per provider session. Read-only observers can be considered later, but must never become implicit audio or device-context owners.

`ConversationEntity` and `ExternalConversationRuntime` become thin HA adapters over this shared session service:

- `ConversationEntity` opens an unattached text-only session and translates events to HA `ChatLog`.
- `ExternalConversationRuntime` opens the same session model with a `PhysicalSatelliteAttachment` and translates PCM/presentation events.

Neither adapter owns profile resolution, wildcard expansion, tool schema transformation, or provider construction.

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

Use HA config and config-subentry flows as the primary profile editor, matching current model-provider integrations:

- create/reconfigure/delete External Conversation Service parent entries;
- create/duplicate/reconfigure/delete conversation profile subentries;
- prompt editor with replacement semantics clearly labeled;
- provider voice text/select field;
- server profile selector populated from a safe capability query;
- requested-tool checklist constrained to concrete names expanded by the server;
- timer-context indicator explaining that generic conversation entities never carry device context;
- Satellite options-flow selector for assigning a connection/profile subentry;
- active-session restart warnings.

If the sidebar panel presents the same controls, it should launch HA config flows rather than maintain a second profile database. A custom capability-preview WebSocket command must use `@websocket_api.require_admin`. The current panel-settings endpoints must not be reused for security-sensitive data.

## Satellite-free ConversationEntity

Each profile subentry creates a `ConversationEntity` and registers it as an HA conversation agent. The standard HA conversation view can select it and call `conversation/process`; no custom message transport is required for normal use.

For each HA conversation ID, the entity's bounded session manager owns a corresponding text-only External Transport session:

1. HA opens/loads its `ChatLog` and supplies the user message.
2. The entity resolves its immutable profile subentry.
3. It opens or reuses a Pipecat session keyed by conversation entity ID, HA conversation ID, HA user ID, and profile revision.
4. It sends generic client identity, `device_id: null`, and `output_modalities: ["text"]`.
5. It maps Pipecat text deltas into `ChatLog.async_add_delta_content_stream`.
6. It maps Pipecat tool start events to `llm.ToolInput(..., external=True)` and result events to `ToolResultContentDeltaDict`, so HA visualizes but does not re-execute them.
7. It returns `conversation.async_get_result_from_chat_log` for native HA response semantics.
8. It serializes turns per conversation so concurrent HA requests cannot overlap or cross streams.
9. It expires idle sessions and closes all sessions on unload with bounded waits.

The generic entity:

- creates no Satellite or HA device;
- always has no device context;
- automatically omits timers and every other context-required tool;
- does not infer a room or area from a device;
- cannot displace or mutate a physical Satellite runtime;
- appears anywhere HA can select a conversation agent, including the standard conversation view and text Assist pipelines.

The standard view cannot submit arbitrary one-off prompt/voice/tool changes. Those remain profile-subentry settings. If rapid ephemeral experiments are still desirable, add an admin-only `Prompt Lab` section to the existing sidebar panel later. It may create a temporary generic session through a custom HA broker, but it must use the same profile resolver/session compiler and remain device-less. It is an optional convenience layer, not the satellite-free model itself.

Because standard `conversation/process` is text-oriented, the generic entity should not request OpenAI audio. If an optional Prompt Lab needs to compare Realtime voices, it can explicitly request audio and play the signed stream in that admin panel.

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

### Phase 2 — HA conversation entities and Satellite assignment

1. Add the entity-free External Conversation Service parent config entry.
2. Add conversation profile config subentries and config flows.
3. Create/register one `ConversationEntity` per profile subentry.
4. Implement the bounded per-conversation External session manager and ChatLog adapter.
5. Add text-only output modality support and external tool-call/result mapping.
6. Add Satellite options-flow assignment to a connection/profile subentry.
7. Resolve and snapshot connection/profile/device context when creating a real Satellite runtime.
8. Recreate runtimes after connection/profile changes.
9. Require explicit service/profile assignment; do not retain Satellite-local credential fallback or migration code.

### Phase 3 — Device-context timer tool

1. Register a namespaced Voice Satellite timer tool with HA's LLM tooling.
2. Make `device_id` a required provider argument and validate it as a timer-capable Voice Satellite device in HA.
3. Add server-side hidden contextual argument injection.
4. Compile the tool only when the profile permits it and `SessionContext.home_assistant_device_id` is present.
5. Verify native timer entity updates, pills, finish alert, and cancellation.
6. Verify generic conversation sessions never advertise or invoke the timer tool.

### Phase 4 — Optional admin Prompt Lab

1. First validate the generated conversation entities in HA's standard conversation view.
2. Only if ephemeral experiments remain awkward, add an admin Prompt Lab to the existing sidebar panel.
3. Reuse the same parent connection resolver, generic session manager, ChatLog/event adapter, and server compiler.
4. Permit one-session prompt/voice/exact-tool overrides but never device context.
5. Request audio only when explicitly testing a voice; otherwise remain text-only.
6. Add bounded idle/session timers and terminal cleanup.
7. Add a satellite-free E2E test proving ordinary tools work and context-required tools are absent.

## Acceptance criteria

- An External Conversation Service entry and conversation profile subentry can create a selectable HA conversation entity without any Satellite entity.
- A satellite can be assigned a named HA session profile without browser-visible credentials.
- Prompt and voice overrides apply only to the newly created provider session.
- HA cannot request any tool outside the server-authorized profile.
- Unknown profiles/subsets fail closed with useful diagnostics.
- Exact names and anchored terminal-wildcard patterns select only discovered tools they match.
- A timer request from a real Satellite session with a validated HA device ID creates the existing countdown pill and finish alert.
- A generic conversation session has no device identity and cannot access timer/device-context tools.
- A user can run a text-only External conversation from HA's standard conversation view by selecting the generated conversation entity, without configuring a Satellite.
- Pipecat tool start/results populate HA ChatLog as externally executed tools and are never executed twice.
- OpenAI/Pipecat provider code consumes one immutable compiled session plan and contains no profile/wildcard/HA-device policy branches.
- Normal users cannot edit profiles, widen tools, or run arbitrary prompt experiments.
- Tool arguments/results remain transported but are not rendered by normal Satellite UI.
- All queues, calls, subscriptions, session expiry, and shutdown paths are bounded.
