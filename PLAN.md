# PLAN.md — "From a house that obeys to a house that understands"

ATC Hackathon 2026 · Altice Labs challenge · Smart home + AI over MCP

This file is the single source of truth for the project. It is written to be executed by
Claude Code: **Phase 0** (section 11) tells Claude Code exactly what to generate — the repository
skeleton, the shared contracts, and `CLAUDE.md` with team ownership and permissions.

---

## 1. Goal

An assistant that acts as the **intelligence layer of the house**. It does not wait for commands:
it watches every device state change, reasons about what the user is doing (room, activity, routine)
and adapts the house — executing, notifying, or suggesting depending on how sensitive the action is.

The solution is **modular through MCP**: the AI works with whatever devices are available, real
(Altice MCP) or simulated (our simulator MCP). More devices → more context → more automation.

### Demo scenarios (must work end-to-end)

| # | Scenario | Devices | Autonomy |
|---|---|---|---|
| S1 | TV turns on in the evening → living-room light dims | sim TV, real/sim bulb | auto |
| S2 | User leaves kitchen into corridor → kitchen light fades off, corridor light fades on. First times: decided by the LLM; once learned and confirmed, runs instantly as an agent-created reflex (D16) | sim motion sensors, 2 bulbs | auto |
| S3 | Coffee routine learned from plug consumption history → plug turns on shortly before usual time, off afterwards | sim coffee plug + seeded history | auto + notify |
| S4 | House becomes empty (door opened/closed, then no motion anywhere for `EMPTY_AFTER_MINUTES` — see D14) → propose switching devices off, adjusting temperature and **arming the alarm** | entrance contact sensor, motion sensors, camera, plugs, security hub | suggest (alarm) |
| S5 | New device of unknown type is added (e.g. `motionSensor`) → agent proposes its configuration (basic) or user configures it (advanced) | any sim device | suggest until confirmed |

---

## 2. Locked decisions

| # | Decision |
|---|---|
| D1 | Simulated devices (coffee machine plug, TV, second lamp, second contact sensor, motion sensors) live in **our own simulator MCP server** that exposes the **same tool names and data format as the Altice MCP**. One device layer merges both sources. |
| D2 | Stack: **Python 3.11 + FastAPI** backend, **Pydantic AI** agent, **SQLite** storage, official **`mcp` Python SDK** (client for both servers, FastMCP for the simulator), **React + TypeScript + Vite** frontend, **Web Speech API** for voice. LLM provider is configurable (`AGENT_MODEL` env), not hard-coded. |
| D3 | **Delta context, no rule-based interpretation layer.** The agent runs on **every state change** but receives only the **changes since its last run** plus its own **belief state** (a short summary it wrote at the end of the previous run, returned in `Decision.beliefState`) and its top **insights** (D15). It pulls anything else on demand through read-only tools (`get_home_state`, `get_device`, `get_history`, `get_presence_facts`, `get_routine_stats`). A **full snapshot** is sent at startup and every `FULL_SNAPSHOT_EVERY_RUNS` runs or `FULL_SNAPSHOT_EVERY_SECONDS` to correct belief drift. Code only collects, stores and aggregates facts; it never decides "user is in the kitchen", "watching TV" or "house is empty". |
| D4 | There is no push from Altice → we implement a **poller**: sim every 2 s, Altice every 5–10 s. Every snapshot diff is stored as history. |
| D5 | **Latest-wins scheduling**: while the agent is running, new changes only set a `dirty` flag. When the run ends, if dirty, the agent runs **once more** on the current snapshot + all changes since its last run. Intermediate states are not reasoned on individually, but are always persisted. |
| D6 | **Three autonomy levels**: `auto` (execute + explain), `auto_notify` (execute + notify with Undo), `suggest` (propose, execute only after user confirmation). |
| D7 | Autonomy is declared **per capability** in the device-type plugin (e.g. camera `motionDetected` readable, `sirenSwitch` = suggest). Unknown type → derive from Altice `categories` (`security`→suggest, `energy`→auto_notify) → otherwise **suggest**. Nothing unknown is ever automatic. |
| D8 | **Anti-loop**: every agent action is logged with its expected resulting state. A change matching an agent action from the last ~15 s is stored but does **not** wake the agent. |
| D9 | **Manual override**: if the user reverts an agent action within N minutes, the agent leaves that device alone for a cooldown and the override is stored as feedback injected into future prompts. |
| D10 | The agent **never calls write tools directly**. It returns a typed `Decision`; an executor applies autonomy rules and calls the device layer. Agent tools are read-only. |
| D11 | **Device plugins**: one declarative plugin per device type. Several instances of the same type are allowed. Altice devices can't be created/removed (no MCP tool) — only assigned room/alias/enabled. Sim devices can be created/removed. Rooms are defined by us (Altice returns none). |
| D12 | **Unknown device type → two configuration modes**: **Basic** (agent proposes purpose, per-capability autonomy, descriptions, starter automations; user accepts with one click) and **Advanced** (user edits every field, starting from the proposal). Stays `suggest` until confirmed. |
| D14 | **Empty house** = an open→closed event on a contact sensor in an **entrance room** (`Room.isEntrance`), followed by **no motion in any room** (sim motion sensors + camera `motionDetected`) for `EMPTY_AFTER_MINUTES` (10 real, 0.5 demo). Motion without a door event never means empty (quiet night = asleep). Back to **occupied** when motion follows a door event. TV off / no manual use only supports the conclusion. The agent decides, from raw facts returned by the read-only tool `get_presence_facts()`; code computes no inference. S4 actions are `suggest`, so a false positive costs only a rejection (stored as feedback). |
| D15 | **Adaptation through agent-learned insights, not fixed rules.** The agent creates, reinforces, weakens or deletes `Insight`s (statement, confidence 0..1, evidence ids, lastConfirmedAt) via `Decision.insightUpdates`. Undo/Reject lower confidence, confirmations and repeated observations raise it; below `INSIGHT_MIN_CONFIDENCE` an insight is dropped. The top `INSIGHTS_IN_PROMPT` insights go into every prompt. Users can see and delete insights in the UI ("What the house learned"). |
| D16 | **Agent-created reflexes.** When an insight is confident (≥ `REFLEX_MIN_CONFIDENCE`) the agent may propose a `Reflex` (trigger: device+capability+value condition, optional time window/room; actions: `CapabilityUpdate`s). Reflexes start `proposed` and run only after the **user confirms** them. Active reflexes run instantly in the poller, **without calling the LLM**, and still respect per-capability autonomy (a reflex can never perform a `suggest` action). Every reflex execution is logged as an agent action (anti-loop) and reported to the agent as a change, so the agent can revise, pause or delete it. Undo of a reflex action counts as negative feedback; N undos auto-pause it. |
| D13 | Two teams. Ownership by **team**, enforced in Claude Code by a hook (section 9). Shared contract = `backend/app/schemas/`, `backend/app/api/`. |

### Facts from the Altice MCP fixtures (`backend/fixtures/`)

- 7 real devices, all in room "Hack 5": `bulb`, `gateway` (security hub), `plug`, `temperatureSensor`,
  `camera`, `contactSensor`, `smokeSensor`. `get_home_rooms` and `get_home_scenarios` are empty.
- Write tools: `update_device_capabilities` (power / brightness / temperature / color_rgb),
  `post_timer_device`, `post_new_scenario`, `post_execute_scenario`, `patch_scenario_activation`,
  `patch_home_security_status`. No subscription/event tool.
- No per-room presence source in the real house → S2 relies on simulated motion sensors.
- Only one real contact sensor → one contact sensor (real or the simulated second one) must be placed in a room marked `isEntrance` to act as the front door (S4).
- Real plug reports 0 W today/yesterday → S3 relies on seeded history.

---

## 3. Architecture

```
            ┌────────────── frontend (React) ──────────────┐
            │  Chat + voice   │  Manager   │ Activity feed  │
            └───────┬─────────┴─────┬──────┴──────┬─────────┘
                REST /api/*         │          WebSocket /ws
            ┌───────┴───────────────┴─────────────┴─────────┐
            │              backend/app/api  (SHARED)         │
            ├────────────────────────────────────────────────┤
            │ agent/  ── Decision ──► executor (autonomy)    │
            │   ▲ beliefState · insights · reflexes (learned)│
            │   ▲ wakes (latest-wins)          │             │
            │ poller/ ── diff ──► storage/ (SQLite)          │
            │   │                               ▲ action log │
            │ devices/ (merged registry, plugins/)           │
            │   │                     │                      │
            │ mcp_clients/altice   mcp_clients/sim           │
            └───┼─────────────────────┼──────────────────────┘
                ▼                     ▼
        Altice MCP (real)     simulator/ MCP (ours, same format)
```

Run loop: `poller` reads all enabled devices → diff vs previous snapshot → persist snapshot + changes
→ **active reflexes matching a change execute immediately** (no LLM, autonomy-checked, logged as agent actions)
→ tag changes caused by agent (anti-loop) → if any non-agent change: `scheduler.request_run()`
→ agent run (or dirty flag if busy) → `Decision` → executor applies per-capability autonomy →
device layer writes → action log → WebSocket broadcast.

---

## 4. Repository skeleton

Ownership: **A** = Team A (Intelligence), **B** = Team B (Experience & Simulation),
**S** = Shared contract, **L** = Locked (team agreement only).

```
HACKATHON_2627/
├── CLAUDE.md                         L  rules for Claude Code (section 9)
├── PLAN.md                           L  this file
├── .gitignore                        L  includes .env, *.db, .claude/settings.local.json
├── .claude/
│   ├── settings.json                 L  hook registration + deny rules
│   └── hooks/check_ownership.py      L  ownership enforcement
├── docs/
│   ├── CONTRACTS.md                  S  human-readable copy of section 5
│   └── executive-summary.md          A+B
├── backend/
│   ├── pyproject.toml                L
│   ├── .env.example                  L
│   ├── fixtures/                     B  Altice MCP recordings (exists)
│   ├── app/
│   │   ├── main.py                   S  FastAPI app, lifespan wiring, 501 handler
│   │   ├── config.py                 S  settings from env
│   │   ├── schemas/                  S  device.py, plugin.py, events.py, agent.py, ws.py
│   │   ├── api/                      S  routes_*.py, ws.py, ports.py (service Protocols), fakes.py
│   │   ├── mcp_clients/              A  altice.py, sim.py, base.py
│   │   ├── devices/                  A  registry.py (merge + read_state/set_capability), rooms.py
│   │   ├── plugins/                  A  registry.py, defaults.py, types/*.yaml (bulb, plug, tv, ...)
│   │   ├── poller/                   A  poller.py, diff.py, scheduler.py (latest-wins), antiloop.py
│   │   ├── storage/                  A  db.py, repository.py, import_seed.py
│   │   └── agent/                    A  agent.py, prompts.py, tools.py, executor.py, feedback.py, onboarding.py, belief.py, insights.py, reflexes.py
│   └── tests/
│       ├── contracts/                S  schema + OpenAPI snapshot tests
│       ├── team_a/                   A
│       └── team_b/                   B  simulator conformance vs fixtures
├── simulator/                        B  FastMCP server, templates/, behaviours (coffee heating, TV power draw)
├── demo/                             B  seed_history.py, seed/history.jsonl, scenarios/*.yaml, run_scenario.py
└── frontend/                         B
    ├── package.json                  L
    └── src/
        ├── api/types.ts              S  TS mirror of backend/app/schemas
        ├── api/client.ts, ws.ts      B
        ├── features/chat/            B  ChatPage, useVoice (Web Speech API)
        ├── features/manager/         B  DeviceList, RoomEditor, PluginConfig (Basic | Advanced)
        ├── features/activity/        B  ActivityFeed (decisions, actions, Undo/Confirm, ⚡ reflex badge)
        └── features/learned/         B  InsightsList, ReflexList (Confirm/Pause/Delete), BeliefPanel
```

Team A owns intelligence and data; Team B owns everything the user/jury sees plus the simulated
world. The simulator is in Team B so Team B is independent from day one and Team A can test
against it without waiting.

---

## 5. Contracts (shared)

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

### 5.4 Agent — `schemas/agent.py`

- `ProposedAction {deviceId, update: CapabilityUpdate, reason}`
- `Decision {id, createdAt, trigger: state_change | chat | startup | reflex_report, explanation (user-facing, PT-PT), inferredRoom?, inferredActivity?, actions: ProposedAction[], confidence: 0..1, beliefState: str (≤ ~800 chars, agent-written), insightUpdates: InsightUpdate[], reflexProposals: ReflexProposal[], reflexUpdates: ReflexUpdate[]}`
- `Insight {id, statement, confidence: 0..1, evidence: str[] (change/action/feedback ids), createdAt, lastConfirmedAt, status: active | dropped}`
- `InsightUpdate {op: create | reinforce | weaken | delete, insightId?, statement?, delta?: float, evidence?: str[]}`
- `ReflexTrigger {deviceId, capability, operator: "=" | "<" | ">" | "changed", value?, roomId?, timeWindow?: {from: "HH:MM", to: "HH:MM", weekdays?: int[]}}`
- `Reflex {id, name, triggers: ReflexTrigger[], triggerLogic: all | any, actions: ProposedAction[], sourceInsightId, status: proposed | active | paused | deleted, createdAt, executions: int, undos: int}`
- `ReflexProposal {reflex: Reflex (status=proposed), rationale}`, `ReflexUpdate {reflexId, op: pause | resume | delete | modify, reflex?: Reflex, reason}`
- `AgentRunInput {mode: delta | full, snapshot?: StateSnapshot (only when mode=full), changesSinceLastRun: StateChange[] (includes reflex executions), beliefState?: str, insights: Insight[] (top N), recentActions: ActionRecord[], feedback: UserFeedback[], rooms: Room[], plugins: DevicePlugin[] (only for devices in the changes when mode=delta), now}`
- `ChatRequest {message, inputMode: text | voice}`, `ChatResponse {reply, decision?}`
- `PluginProposal {plugin: DevicePlugin, rationale}`
- `PresenceFacts {now, lastDoorEvent?: {deviceId, roomId, openedAt, closedAt?}, lastMotionByRoom: {roomId, deviceId, at}[], secondsSinceLastMotion?, secondsSinceLastDoorEvent?, motionAfterDoorEvent: bool, emptyAfterSeconds}` — raw facts only, no `isEmpty` field (D14)

### 5.5 REST API — `backend/app/api/`

Routes only depend on service **Protocols** in `api/ports.py` (implemented by Team A). Unimplemented
services raise `NotImplementedError` → handler returns **501**. `USE_FAKES=1` wires `api/fakes.py`
(fixture-backed) so Team B can build the UI before Team A is ready.

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
| GET | `/api/history/changes?deviceId=&since=&limit=` | → `StateChange[]` |
| GET | `/api/agent/status` | → `{busy, dirty, lastRunAt, model, beliefState, runsSinceFullSnapshot}` |
| GET | `/api/insights` | → `Insight[]` (active, sorted by confidence) |
| DELETE | `/api/insights/{id}` | → 204 (user removes something the house "learned") |
| GET | `/api/reflexes?status=` | → `Reflex[]` |
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

---

## 6. Agent behaviour (Team A)

- **Pydantic AI** `Agent(model=settings.AGENT_MODEL, deps_type=AgentDeps, output_type=Decision, instructions=...)`.
- Prompt contains (D3, **delta mode**): `changesSinceLastRun` (including reflex executions), previous `beliefState`,
  top insights, plugin descriptions + autonomy for the devices involved in the changes, rooms, recent actions,
  user feedback/overrides, current time. **Full mode** (startup / every N runs / every T seconds) adds the full snapshot.
- Instructions require the agent to: (1) update `beliefState` every run, (2) call tools when the delta is not enough,
  (3) emit `insightUpdates` when it sees a repeated pattern or receives feedback, (4) propose a reflex only for an
  insight with confidence ≥ `REFLEX_MIN_CONFIDENCE`, (5) pause/modify reflexes that were undone.
- Read-only tools: `get_home_state()` (full current snapshot), `get_history(deviceId, capability, since)`, `get_routine_stats(deviceId, capability, days)`,
  `get_device(deviceId)`, `get_plugin(type)`, `get_presence_facts()` (`PresenceFacts`, section 5.4). **No write tools.**
- **Empty-house rule in the prompt (D14)**: conclude "empty" only if `lastDoorEvent` exists, `motionAfterDoorEvent` is false and
  `secondsSinceLastDoorEvent >= emptyAfterSeconds`; otherwise occupied or unknown. Never infer "empty" from silence alone.
  When "empty" is concluded, propose S4 actions (devices off → `auto_notify`, temperature → per plugin, alarm via
  `patch_home_security_status` → always `suggest`) and do not repeat the proposal until the house is occupied again.
- Executor: for each `ProposedAction` → resolve autonomy (section 5.2) → `auto`: execute; `auto_notify`: execute
  + notify with Undo; `suggest`: store `pending_confirmation`. Skip devices under override cooldown.
  Validate action against plugin (`writable`, value in range / `availableValues`) — never trust the LLM blindly.
- Chat requests use the same agent with `trigger="chat"`; a user's explicit command is still subject to autonomy for security capabilities.
- Onboarding (`agent/onboarding.py`): given an unknown `Device`, return `PluginProposal` (purpose, per-capability
  description/writable/autonomy, starter automations, rationale). Security-like capabilities must be `suggest`.
- Explanations are in **PT-PT**, short, and say *why* ("Baixei a luz da sala porque ligaste a televisão.").

## 7. Poller, history, scheduling (Team A)

- Poll intervals: `SIM_POLL_SECONDS=2`, `ALTICE_POLL_SECONDS=7` (configurable). Failures of one source never stop the other.
- `diff.py`: pure function `diff(prev: StateSnapshot, curr: StateSnapshot) -> list[StateChange]`, also detects device added/removed.
- `antiloop.py`: match change against action log (same device + capability + expected value, within 15 s) → `causedByAgent=True`.
- `scheduler.py` (`LatestWinsScheduler`): `request_run()`; one run at a time; `dirty` flag; rerun once with fresh snapshot;
  broadcast `agent_status`. Optional min gap (e.g. 1 s) to protect LLM cost.
- SQLite tables: `snapshots`, `changes`, `decisions`, `actions`, `feedback`, `rooms`, `device_overrides` (alias/room/enabled), `plugins`,
  `belief_state` (latest + history), `insights`, `reflexes`, `reflex_executions`.
- `agent/reflexes.py` (`ReflexEngine`): called by the poller with each batch of changes **before** waking the agent; evaluates
  active reflexes, executes matching actions through the executor (autonomy check, never `suggest`), logs them as agent
  actions (anti-loop) and appends a synthetic `reflex_executed` change so the agent sees it in the next delta.
- `agent/insights.py`: applies `InsightUpdate`s (confidence clamped 0..1, drop below `INSIGHT_MIN_CONFIDENCE`), converts
  Undo/Reject feedback into `weaken` on the linked insight, auto-pauses a reflex after `REFLEX_MAX_UNDOS`.
- `agent/belief.py`: stores `beliefState`, decides delta vs full mode (`FULL_SNAPSHOT_EVERY_RUNS`, `FULL_SNAPSHOT_EVERY_SECONDS`).

## 8. Frontend (Team B)

- **Chat**: text + push-to-talk (Web Speech API `SpeechRecognition`, `lang="pt-PT"`), replies read aloud
  (`speechSynthesis`), decisions rendered as cards with explanation and action chips.
- **Activity feed**: live from `/ws`; `auto_notify` actions with **Undo**; `suggest` actions with **Confirm / Reject**;
  reflex executions marked with a ⚡ badge.
- **What the house learned**: insights with confidence bars and evidence, delete button; reflexes with
  Confirm (for `proposed`), Pause/Resume, Delete; current `beliefState` shown as "What the house thinks is happening".
- **Manager**: device list grouped by room with source badge (real / simulated), alias/room/enabled editing,
  add/remove simulated devices from templates, room CRUD, **plugin configuration dialog with Basic and Advanced tabs**
  (opened automatically on `plugin_needed`).
- Vite dev proxy: `/api` and `/ws` → `http://localhost:8000`.

---

## 9. Teams, ownership and permissions

### Teams

- **Team A — Intelligence (3 people)**: `backend/app/{agent,plugins,poller,storage,devices,mcp_clients}/`, `backend/tests/team_a/`.
- **Team B — Experience & Simulation (2 people)**: `frontend/` (except `src/api/types.ts`), `simulator/`, `demo/`,
  `backend/fixtures/`, `backend/tests/team_b/`.
- **Shared contract**: `backend/app/schemas/`, `backend/app/api/`, `backend/app/main.py`, `backend/app/config.py`,
  `frontend/src/api/types.ts`, `docs/CONTRACTS.md`, `backend/tests/contracts/`.
- **Both teams**: `docs/` (except `CONTRACTS.md`).
- **Locked**: `CLAUDE.md`, `PLAN.md`, `.claude/`, `.gitignore`, `backend/pyproject.toml`, `backend/.env.example`,
  `frontend/package.json`, `.github/`. Changed only by a human after team agreement.

### Permission matrix (enforced for Claude Code)

| Path class | Team A member | Team B member |
|---|---|---|
| Team A paths | edit | **deny** |
| Team B paths | **deny** | edit |
| Shared contract | **ask** (needs cross-team PR) | **ask** (needs cross-team PR) |
| Both-teams docs | edit | edit |
| Locked | **deny** | **deny** |
| Read anything | allow | allow |

### Enforcement mechanism

- Each member creates an untracked `.claude/settings.local.json`: `{"env": {"TEAM": "A"}}` (or `"B"`).
- `.claude/settings.json` (committed) registers a **PreToolUse** hook on `Edit|Write|MultiEdit|NotebookEdit`
  running `python "$CLAUDE_PROJECT_DIR/.claude/hooks/check_ownership.py"`.
- The hook reads the tool input from stdin, normalises `file_path`/`notebook_path` (Windows backslashes,
  relative to `CLAUDE_PROJECT_DIR`), classifies it with the table above and prints
  `{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"allow|ask|deny","permissionDecisionReason":"..."}}`.
  Missing/invalid `TEAM` → deny with a message explaining how to set it. Paths outside the repo → allow.
- `settings.json` also denies: `Bash(git push origin main:*)`, `Bash(git push --force:*)`, `Bash(git push -f:*)`,
  `Read(./backend/.env)`, `Edit(./backend/.env)`.
- `CLAUDE.md` tells Claude not to create or modify files through Bash (redirection, `sed -i`, `mv`, `rm`) to bypass the hook.

### Git workflow

- Branches: `team-a/<topic>`, `team-b/<topic>`, `contract/<topic>`. No direct pushes to `main`; PR required.
- Team-owned PRs: approved by someone in the same team.
- `contract/*` PRs: approved by **one member of each team**; must update `schemas/`, `frontend/src/api/types.ts`,
  `docs/CONTRACTS.md` and contract tests **together**.
- Commit style: `feat(agent): ...`, `fix(sim): ...`, `contract: ...`.

### `CLAUDE.md` content (Phase 0 must create it exactly with these sections)

1. **Project in 5 lines** — goal, the two MCP sources, the agent loop; link to `PLAN.md`.
2. **Locked decisions** — D1–D16 as one-liners; "do not redesign these without the team".
3. **Who am I working for** — "Read `TEAM` from the environment. If unset, ask the user which team they are in and tell them to create `.claude/settings.local.json`."
4. **Ownership table** — section 9 table; "Only edit files your team owns. Shared contract edits need a `contract/*` branch and cross-team review; update schemas + `types.ts` + `CONTRACTS.md` + contract tests in the same change."
5. **Never do** —
   - edit other team's or locked files, or bypass the hook via Bash;
   - push to `main` or force-push;
   - call Altice MCP write tools from tests or scripts (only the executor may write to the real house);
   - make alarm / siren / camera recording actions anything other than `suggest`;
   - give the agent write tools (D10);
   - let a reflex run before the user confirmed it, or perform a `suggest` action (D16);
   - commit `.env`, API keys, `*.db`;
   - change Altice field names (camelCase) in device schemas.
6. **Commands** — backend: `cd backend && uv sync && uv run uvicorn app.main:app --reload`; fakes: `USE_FAKES=1`;
   simulator: `cd simulator && uv run python server.py` (port 8100); frontend: `cd frontend && npm i && npm run dev`;
   tests: `uv run pytest`, `npm run typecheck`; lint: `uv run ruff check .`.
7. **Conventions** — Python 3.11, type hints everywhere, Pydantic v2, async I/O, `ruff`; TS strict, no `any` in `src/api`;
   user-facing text in PT-PT, code/comments in English.
8. **Definition of done** — tests pass, contract tests pass, no 501 left for the feature, demo scenario touched still runs.

---

## 10. Milestones

| Phase | Team A | Team B | Integration check |
|---|---|---|---|
| **0 · Skeleton & contracts** (both, first) | — | — | Claude Code generates section 11; `uvicorn` starts, OpenAPI builds, contract tests green, hook blocks a cross-team edit |
| **1 · Vertical slice (S1)** | Altice + sim clients, merged registry, poller + diff + SQLite, scheduler, minimal agent, executor (`auto`) | Simulator with `tv` + `bulb` templates, `sim_set_state`; chat page + activity feed on fakes | TV on in sim → light dims → decision card in UI; delta mode + `beliefState` working |
| **2 · Movement & safety (S2, S4)** | Anti-loop, autonomy resolution, override cooldown, `suggest` flow, `get_presence_facts` + empty-house prompt rule, insights (create/reinforce/weaken), `ReflexEngine` + reflex proposals | `motionSensor`, `contactSensor` templates, fades; Confirm/Reject/Undo in feed; voice; "What the house learned" page | Walk kitchen→corridor a few times → insight → reflex proposed → confirmed → next walk is instant; empty house → alarm suggestion confirmed by voice |
| **3 · Routines (S3)** | `import_seed`, `get_routine_stats` tool, routine prompting | `coffee_plug` behaviour, `seed_history.py`, `run_scenario.py` | Seeded history → agent turns plug on before 07:30 (time can be faked via `NOW_OVERRIDE`) |
| **4 · Manager & onboarding (S5)** | Plugin registry, `propose`, confirm, `plugin_needed` event | Manager UI, rooms, add/remove sim devices, Basic/Advanced dialog | Add `motionSensor` → Basic proposal accepted → used by agent |
| **5 · Real house & demo** | Hardening against Altice latency/failures, cost guard | Demo script, executive summary, pitch | Full S1–S5 run twice without intervention, one step on the real Altice house |

---

## 11. Phase 0 — instructions for Claude Code

Run these in order from the repository root. Do not implement business logic: stubs raise
`NotImplementedError` with a docstring stating the expected behaviour and the owning team (`# Owner: Team A`).

1. Create the directory tree of section 4 (keep the existing `backend/fixtures/`, `docs/`, and anything already in `backend/` and `frontend/` — merge, never delete).
2. Write **fully implemented** shared contract files:
   `backend/app/schemas/{device,plugin,events,agent,ws}.py` (section 5.1–5.4, 5.6), `backend/app/schemas/__init__.py` re-exporting all models.
3. Write `backend/app/api/ports.py` (Protocols: `DeviceService`, `RoomService`, `SimAdminService`, `PluginService`,
   `AgentService`, `ActionService`, `HistoryService`), route modules for every endpoint in 5.5, `ws.py` broadcaster,
   and `fakes.py` that serves `backend/fixtures/get_home_devices.json` and canned decisions.
4. Write `backend/app/main.py` (lifespan: build services — fakes if `USE_FAKES=1`; start poller task if not fakes;
   `NotImplementedError` → 501) and `backend/app/config.py` (`ALTICE_MCP_URL`, `ALTICE_MCP_TOKEN`, `SIM_MCP_URL`,
   `AGENT_MODEL`, `DB_PATH`, poll intervals, `USE_FAKES`, `NOW_OVERRIDE`, `EMPTY_AFTER_MINUTES`, `FULL_SNAPSHOT_EVERY_RUNS`, `FULL_SNAPSHOT_EVERY_SECONDS`, `INSIGHTS_IN_PROMPT`,
   `INSIGHT_MIN_CONFIDENCE`, `REFLEX_MIN_CONFIDENCE`, `REFLEX_MAX_UNDOS`).
5. Write Team A stubs: `mcp_clients/{base,altice,sim}.py`, `devices/{registry,rooms}.py`, `plugins/{registry,defaults}.py`
   + `plugins/types/*.yaml` builtins (section 5.2), `poller/{poller,diff,scheduler,antiloop}.py`,
   `storage/{db,repository,import_seed}.py`, `agent/{agent,prompts,tools,executor,feedback,onboarding,belief,insights,reflexes}.py`.
6. Write Team B stubs: `simulator/server.py` (FastMCP, tools of 5.7 registered with stub bodies), `simulator/templates/*.json`,
   `demo/{seed_history,run_scenario}.py`, `demo/scenarios/s1..s5.yaml`, Vite React TS app with the files of section 4/8
   and `src/api/types.ts` mirroring the schemas.
7. Write `backend/tests/contracts/test_schemas.py` (models round-trip the Altice fixtures) and `test_openapi.py`
   (every path in 5.5 exists), `backend/tests/team_b/test_sim_conformance.py` (simulator output validates as `Device`).
8. Write `backend/pyproject.toml` (fastapi, uvicorn[standard], pydantic>=2, pydantic-ai, mcp, aiosqlite, pyyaml,
   pytest, pytest-asyncio, ruff), `backend/.env.example`, `.gitignore`.
9. Write `.claude/settings.json`, `.claude/hooks/check_ownership.py` (section 9) and a `test_hook.py` that feeds sample
   payloads for TEAM=A/B on owned, other-team, shared, locked and outside paths.
10. Write `CLAUDE.md` (section 9 content) and `docs/CONTRACTS.md` (copy of section 5).
11. Verify: `uv run pytest`, `USE_FAKES=1 uv run uvicorn app.main:app` responds on `/api/devices`, `npm run typecheck`,
    hook tests pass. Then commit on `contract/skeleton` and open a PR for both teams to approve.

After Phase 0 merges, `CLAUDE.md` and `.claude/` become locked and each team works only in its paths.

---

## 12. Risks

| Risk | Mitigation |
|---|---|
| LLM latency breaks S2 (light-follows-you) | Delta prompts + latest-wins; confirmed reflexes run without the LLM; fades in simulator hide the delay of the first, LLM-decided runs |
| Belief state drifts from reality (missed change, wrong summary) | Periodic full-snapshot runs; `get_home_state` tool; belief shown in UI so drift is visible |
| Bad insight / reflex learned | User confirmation before a reflex runs; Undo weakens insight; auto-pause after `REFLEX_MAX_UNDOS`; delete from UI |
| LLM cost from reasoning on every change | Delta prompts (changes + belief + top insights), history via tools, min gap between runs, disabled devices excluded |
| Agent loops on its own actions | Anti-loop rule (D8) + contract test |
| Unsafe action on real house | Executor validates against plugin; security always `suggest`; no write tools in agent; tests never hit Altice writes |
| Altice MCP unavailable at demo | Everything runs on the simulator; real house is a bonus step |
| Contract drift between teams | One shared folder, cross-team PRs, contract tests, `types.ts` updated in the same PR |
