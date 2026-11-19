# CLAUDE.md

## 1. Project in 5 lines

- Goal: an assistant that is the **intelligence layer of the smart home**; it reasons on every device state change and adapts the house (execute / notify / suggest by sensitivity).
- Two MCP sources merged into one device layer: the **Altice MCP** (real house) and **our simulator MCP** (same tool names and data format).
- Agent loop: poller diffs snapshots -> active reflexes run instantly -> agent (Pydantic AI) gets only the delta + belief state + insights -> returns a typed `Decision` -> executor applies per-capability autonomy -> device layer writes.
- Stack: Python 3.11 + FastAPI, SQLite, `mcp` SDK, React + TypeScript + Vite.
- Full details: [PLAN.md](PLAN.md) (single source of truth).

## 2. Locked decisions (do not redesign without the team)

- D1 Simulator MCP exposes the same tool names/format as Altice; one merged device layer.
- D2 FastAPI + Pydantic AI + SQLite + `mcp` SDK + React/TS/Vite + Web Speech API; model set by `AGENT_MODEL`.
- D3 Delta context + agent-written `beliefState` + insights; full snapshot periodically; code never decides "user is in kitchen / house is empty".
- D4 Poller (sim 2 s, Altice 5-10 s); every diff is stored.
- D5 Latest-wins scheduling with a `dirty` flag.
- D6 Autonomy levels: `auto`, `auto_notify`, `suggest`.
- D7 Autonomy per capability in the device-type plugin; unknown -> category default -> `suggest`.
- D8 Anti-loop: changes matching an agent action in the last ~15 s do not wake the agent.
- D9 Manual override: reverted agent action -> cooldown + feedback.
- D10 The agent never calls write tools; it returns a `Decision`, the executor writes.
- D11 One declarative plugin per device type; Altice devices cannot be created/removed; rooms are ours.
- D12 Unknown type -> Basic (agent proposal) or Advanced (user edits) configuration, `suggest` until confirmed.
- D13 Two teams; ownership enforced by a hook; shared contract in `backend/app/schemas/` and `backend/app/api/`.
- D14 Empty house = door open->closed in an entrance room + no motion for `EMPTY_AFTER_MINUTES`; the agent decides from `get_presence_facts()`.
- D15 Adaptation through agent-learned `Insight`s with confidence.
- D16 Agent-created `Reflex`es: run only after user confirmation, without the LLM, still respecting autonomy.

## 3. Who am I working for

Read `TEAM` from the environment. If unset, ask the user which team they are in and tell them to create `.claude/settings.local.json` containing `{"env": {"TEAM": "A"}}` (or `"B"`). That file is untracked.

## 4. Ownership table

| Path class | Paths | Team A | Team B |
|---|---|---|---|
| Team A | `backend/app/{agent,plugins,poller,storage,devices,mcp_clients}/`, `backend/tests/team_a/` | edit | **deny** |
| Team B | `frontend/` (except `src/api/types.ts`), `simulator/`, `demo/`, `backend/fixtures/`, `backend/tests/team_b/` | **deny** | edit |
| Shared contract | `backend/app/{schemas,api}/`, `backend/app/main.py`, `backend/app/config.py`, `frontend/src/api/types.ts`, `docs/CONTRACTS.md`, `backend/tests/contracts/` | ask | ask |
| Both | `docs/` (except `CONTRACTS.md`) | edit | edit |
| Locked | `CLAUDE.md`, `PLAN.md`, `.claude/`, `.gitignore`, `backend/pyproject.toml`, `backend/.env.example`, `frontend/package.json`, `.github/` | **deny** | **deny** |

Only edit files your team owns. Shared contract edits need a `contract/*` branch and cross-team review; update schemas + `types.ts` + `CONTRACTS.md` + contract tests in the same change.

## 5. Never do

- Edit the other team's files or locked files, or bypass the hook via Bash (redirection, `sed -i`, `mv`, `rm`, scripts that write files).
- Push to `main` or force-push.
- Call Altice MCP write tools from tests or scripts (only the executor may write to the real house).
- Make alarm / siren / camera-recording actions anything other than `suggest`.
- Give the agent write tools (D10).
- Let a reflex run before the user confirmed it, or perform a `suggest` action (D16).
- Commit `.env`, API keys or `*.db`.
- Change Altice field names (camelCase) in device schemas.

## 6. Commands

- Backend: `cd backend && uv sync && uv run uvicorn app.main:app --reload` (fakes: `USE_FAKES=1`)
- Simulator: `cd simulator && uv run python server.py` (port 8100)
- Frontend: `cd frontend && npm i && npm run dev`
- Tests: `cd backend && uv run pytest` · `cd frontend && npm run typecheck`
- Hook tests: `cd backend && uv run pytest ../.claude/hooks/test_hook.py`
- Lint: `cd backend && uv run ruff check .`

## 7. Conventions

- Python 3.11, type hints everywhere, Pydantic v2, async I/O, `ruff`.
- TypeScript strict, no `any` in `src/api`.
- User-facing text in PT-PT; code and comments in English.
- Stubs carry `# Owner: Team X` and raise `NotImplementedError` with the expected behaviour.

## 8. Definition of done

Tests pass, contract tests pass, no 501 left for the feature, and the demo scenario touched still runs.
