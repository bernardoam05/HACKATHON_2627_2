# CONTRACTS

Human-readable copy of PLAN.md section 5. Keep in sync with `backend/app/schemas/`, `frontend/src/api/types.ts` and `backend/tests/contracts/` in the same `contract/*` PR.

## Contracts

All models are Pydantic v2 in `backend/app/schemas/`, mirrored in `frontend/src/api/types.ts`.
Device/capability field names keep **Altice camelCase** on purpose.

### 5.1 Devices — `schemas/device.py`

- `DeviceSource` enum: `altice | sim`
- `Capability` (extra allowed): `type`, `name=""`, `value`, `availableValues?`, `min?`, `max?`
- `Device` (extra allowed): Altice fields `id, name, type, capabilities[], categories[], roomName?, lastCommunicationTime?`
  + ours `source, alias?, roomId?, enabled=True`; helper `capability(type)`
- `CapabilityUpdate`: `type, value?, min?, max?, redValue?, greenValue?, blueValue?` (same as Altice `update_device_capabilities.body`)
- `Room {id, name, isEntrance: bool = False}`, `RoomCreate {name, isEntrance = False}` — contact sensors in an entrance room are treated as the front door (D14)
- `DeviceAssignment {alias?, roomId?, enabled?}` — partial update from the manager
- `SimDeviceCreate {type, name, roomId?, template?, capabilities?}`

### 5.2 Plugins — `schemas/plugin.py`

- `AutonomyLevel` enum: `auto | auto_notify | suggest`
- `DevicePurpose` enum: `comfort | entertainment | energy | security | environment`
- `PluginStatus` enum: `builtin | proposed | confirmed`
- `ConfigMode` enum: `basic | advanced`
- `CapabilitySpec {type, description, writable: bool, autonomy: AutonomyLevel = suggest}`
- `DevicePlugin {type, displayName, purpose, capabilities: CapabilitySpec[], starterAutomations: str[], status, configMode?}`
  + method `autonomy_for(cap_type)` → spec value, else `suggest`
- `CATEGORY_DEFAULT_AUTONOMY = {"security": suggest, "energy": auto_notify}`
- Resolution order for a capability's autonomy: confirmed plugin → builtin plugin → category default → `suggest`.
- Builtin plugins (YAML in `backend/app/plugins/types/`): `bulb, plug, temperatureSensor, contactSensor,
  smokeSensor, camera, gateway, tv`. `motionSensor` intentionally has **no** builtin plugin (demo S5).
  Security capabilities (alarm, siren, camera recording) are always `suggest`.

### 5.3 History & actions — `schemas/events.py`

- `StateSnapshot {id, takenAt, devices: Device[]}`
- `StateChange {id, deviceId, capability, old, new, at, causedByAgent: bool, actionId?}`
- `ActionStatus` enum: `executed | pending_confirmation | rejected | undone | failed`
- `ActionRecord {id, decisionId, deviceId, update: CapabilityUpdate, autonomy, status, expectedValue, createdAt, resolvedAt?}`
- `UserFeedback {actionId, kind: undo | confirm | reject | manual_override, at, note?}`
- `CooldownReason` enum: `manual_override | undo | user`
- `DeviceCooldown {deviceId, until? (null = until the user unblocks), reason, actionId?, capability?, note? (PT-PT, what the agent did and what the user set), startedAt, defaultMinutes (next override on this device; 0 = never block)}` (D9)
- `CooldownUpdate {minutes? (0..1440, 0 = unblock now), untilUnblocked = false, rememberForDevice = false}`: exactly one of `minutes` / `untilUnblocked`; `rememberForDevice` (only with `minutes`) makes `minutes` this device's default for future overrides

### 5.4 Agent — `schemas/agent.py`

- `ProposedAction {deviceId, update: CapabilityUpdate, reason}`
- `Decision {id, createdAt, trigger: state_change | chat | startup | reflex_report, explanation (user-facing, PT-PT), inferredRoom?, inferredActivity?, actions: ProposedAction[], confidence: 0..1, beliefState: str (≤ ~800 chars, agent-written), insightUpdates: InsightUpdate[], reflexProposals: ReflexProposal[], reflexUpdates: ReflexUpdate[]}`
- `Insight {id, statement, confidence: 0..1, evidence: str[] (change/action/feedback ids), createdAt, lastConfirmedAt, status: active | dropped}`
- `InsightUpdate {op: create | reinforce | weaken | delete, insightId?, statement?, delta?: float, evidence?: str[]}`
- `ReflexTrigger {kind: state | time | first_motion = state, deviceId = "", capability = "", operator: "=" | "<" | ">" | "changed" = "=", value?, roomId?, timeWindow?: {from: "HH:MM", to: "HH:MM", weekdays?: int[]}, at?: "HH:MM", weekdays?: int[] (0 = Monday), delayMinutes?: 0..720}`:
  - `state`: fires on a device change (needs `deviceId` + `capability`);
  - `time`: every day at `at` (house time), only on `weekdays` if given (e.g. "07:30, Mon-Fri -> bedroom light on");
  - `first_motion`: `delayMinutes` after the first motion of the house day (e.g. "30 min after waking up -> coffee machine on");
  - in an `all` reflex, `state` triggers next to a time trigger are conditions on the current state.
- `Reflex {id, name, triggers: ReflexTrigger[], triggerLogic: all | any, actions: ProposedAction[], sourceInsightId, status: proposed | active | paused | deleted, createdAt, executions: int, undos: int, lastFiredAt?}`: scheduled reflexes fire at most once per house day, without the LLM, and never perform a `suggest` action (D16)
- `ReflexCreate {name, triggers: ReflexTrigger[] (>= 1), triggerLogic = all, actions: ProposedAction[] (>= 1)}`: user-created, active at once
- `ReflexProposal {reflex: Reflex (status=proposed), rationale}`, `ReflexUpdate {reflexId, op: pause | resume | delete | modify, reflex?: Reflex, reason}`
- `AgentRunInput {mode: delta | full, snapshot?: StateSnapshot (only when mode=full), changesSinceLastRun: StateChange[] (includes reflex executions), beliefState?: str, insights: Insight[] (top N), recentActions: ActionRecord[], feedback: UserFeedback[], rooms: Room[], plugins: DevicePlugin[] (only for devices in the changes when mode=delta), now}`
- `ChatRequest {message, inputMode: text | voice}`, `ChatResponse {reply, decision?}`
- `PluginProposal {plugin: DevicePlugin, rationale}`
- `PresenceFacts {now, lastDoorEvent?: {deviceId, roomId, openedAt, closedAt?}, lastMotionByRoom: {roomId, deviceId, at}[], secondsSinceLastMotion?, secondsSinceLastDoorEvent?, motionAfterDoorEvent: bool, emptyAfterSeconds}` — raw facts only, no `isEmpty` field (D14)

### 5.5 REST API — `backend/app/api/`

Routes only depend on service **Protocols** in `api/ports.py` (implemented by Team A). `USE_FAKES=1` wires `api/fakes.py`
(fixture-backed) so Team B can build the UI before Team A is ready. Otherwise `main.py` builds the
real services with Team A's `app.agent.wiring.build_services(settings)` (501 everywhere while that
module does not exist) and runs its poller.

Service errors map to HTTP status codes in `main.py` (body `{"detail": str}`):

| Service raises | Status | Meaning |
|---|---|---|
| `NotImplementedError` | **501** | feature not implemented yet |
| `KeyError(id)` | **404** | unknown device / room / action / insight / reflex / plugin type |
| `ValueError` | **409** | conflicts with the current state (e.g. confirm/reject an action that is not pending, undo an action that was not executed, remove an Altice device, resume a reflex that is not paused) |
| pydantic `ValidationError` | 500 | internal bug, never a client error |

| Method | Path | Body → Response |
|---|---|---|
| GET | `/api/devices` | → `Device[]` (merged, both sources) |
| GET | `/api/devices/{id}` | → `Device` |
| PATCH | `/api/devices/{id}` | `DeviceAssignment` → `Device` |
| POST | `/api/devices/{id}/capabilities` | `CapabilityUpdate` → `Device` (manual control from UI; logged as user action) |
| POST | `/api/sim/devices` | `SimDeviceCreate` → `Device` |
| DELETE | `/api/sim/devices/{id}` | → 204 (409 if device source is `altice`) |
| GET | `/api/sim/templates` | → `{id, type, name}[]` |
| GET/POST | `/api/rooms` | → `Room[]` / `RoomCreate` → `Room` |
| DELETE | `/api/rooms/{id}` | → 204 |
| GET | `/api/plugins` | → `DevicePlugin[]` |
| GET | `/api/plugins/unconfigured` | → `string[]` (device types present with no confirmed/builtin plugin) |
| POST | `/api/plugins/{type}/propose` | → `PluginProposal` (Basic mode, agent-generated) |
| PUT | `/api/plugins/{type}` | `DevicePlugin` (configMode basic\|advanced) → `DevicePlugin` (status=confirmed) |
| POST | `/api/chat` | `ChatRequest` → `ChatResponse` |
| GET | `/api/decisions?limit=50` | → `Decision[]` |
| GET | `/api/actions?status=&limit=50` | → `ActionRecord[]` |
| POST | `/api/actions/{id}/confirm` · `/reject` · `/undo` | → `ActionRecord` |
| GET | `/api/cooldowns` | → `DeviceCooldown[]` (active blocks after user overrides) |
| PUT | `/api/cooldowns/{deviceId}` | `CooldownUpdate` → `DeviceCooldown` (shorten / extend / until unblocked / remember; creates one with reason `user` if none; 422 if both or neither choice) |
| DELETE | `/api/cooldowns/{deviceId}` | → 204 (unblock now; idempotent) |
| GET | `/api/history/changes?deviceId=&since=&limit=` | → `StateChange[]` |
| GET | `/api/agent/status` | → `{busy, dirty, lastRunAt, model, beliefState, runsSinceFullSnapshot}` |
| GET | `/api/insights` | → `Insight[]` (active, sorted by confidence) |
| DELETE | `/api/insights/{id}` | → 204 (user removes something the house "learned") |
| GET | `/api/reflexes?status=` | → `Reflex[]` |
| POST | `/api/reflexes` | `ReflexCreate` → `Reflex` (status=active; user-created schedule/automation; 409 if an action needs confirmation or the capability does not exist, 404 for an unknown device, 422 for an invalid trigger) |
| POST | `/api/reflexes/{id}/confirm` · `/pause` · `/resume` | → `Reflex` |
| DELETE | `/api/reflexes/{id}` | → 204 |

### 5.6 WebSocket — `/ws` (`schemas/ws.py`, discriminated union on `type`)

- `{"type":"state_changed","changes":StateChange[]}`
- `{"type":"decision","decision":Decision}`
- `{"type":"action_updated","action":ActionRecord}`
- `{"type":"agent_status","busy":bool,"dirty":bool}`
- `{"type":"device_added"|"device_removed","device":Device}`
- `{"type":"plugin_needed","deviceType":str}` → UI opens Basic/Advanced config
- `{"type":"insight_updated","insight":Insight}`
- `{"type":"reflex_proposed"|"reflex_updated","reflex":Reflex}` → UI asks user to confirm a proposed reflex
- `{"type":"reflex_executed","reflexId":str,"actions":ActionRecord[]}`
- `{"type":"cooldown_started","cooldown":DeviceCooldown}` → the user overrode the agent: UI shows `note` and the options (keep, shorter/longer, until unblocked, unblock now, remember for this device). The overridden action also arrives as `action_updated` with status `undone`. Also sent again (same `deviceId`, replace the previous one) when the user changes a block with PUT.
- `{"type":"cooldown_ended","deviceId":str,"reason":"expired"|"user"}`

### 5.7 Simulator MCP — `simulator/` (Team B), consumed by `mcp_clients/sim.py` (Team A)

Same names and payload shapes as Altice (verified against `backend/fixtures/`):

- `get_home_devices()` → `{"devices": Device[]}` (without our extra fields)
- `get_device_details(deviceId)` → `Device`
- `update_device_capabilities(deviceId, body: CapabilityUpdate)` → updated `Device`
- `get_home_rooms()` → `{"rooms": []}`

Simulator-only admin tools (prefixed `sim_`):

- `sim_list_templates()`, `sim_add_device(template, name, roomName?, capabilities?)`, `sim_remove_device(deviceId)`
- `sim_set_state(deviceId, capability, value)` — simulates a *physical* event (user presses TV remote, walks into the corridor). Not used by the agent.
- `sim_load_scenario(name)` — puts the world in the start state of a demo scenario.

Templates: `tv` (power, volume, instantPower), `coffee_plug` (plug; heats ~1200 W for 2 min after power on, then idle ~2 W),
`bulb`, `contactSensor`, `motionSensor` (`motionDetected: bool`, `lastMotionAt`).
Writes to brightness may be applied as a short fade (S2 "smoothly").

### 5.8 Seed history — `demo/seed/history.jsonl`

One `StateChange` JSON per line (14 days of coffee-plug `instantPower` on weekday mornings ~07:30, plus TV evenings).
Imported by Team A's `python -m app.storage.import_seed demo/seed/history.jsonl`.
