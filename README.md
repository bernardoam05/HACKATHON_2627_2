# Smart Home Agent

The intelligence layer of a smart home. It reasons on every device state change and adapts the
house, choosing per capability whether to execute, notify or suggest.

Two MCP sources are merged into one device layer: the **Altice MCP** (the real house) and our
**simulator MCP** (same tool names and data format).

## Project structure

```
.
├── backend/                 FastAPI app (Python 3.11+, SQLite)
│   ├── app/
│   │   ├── agent/           Pydantic AI agent, prompts, wiring, executor
│   │   ├── plugins/         one declarative plugin per device type
│   │   ├── poller/          snapshot polling, diffing, latest-wins scheduler
│   │   ├── storage/         SQLite repository
│   │   ├── devices/         merged device registry and rooms
│   │   ├── mcp_clients/     Altice client (OAuth), simulator client
│   │   ├── schemas/         shared Pydantic contract
│   │   ├── api/             REST + WebSocket routes
│   │   ├── config.py        settings, read from the root .env
│   │   └── main.py
│   ├── fixtures/
│   ├── tests/               contracts/, team_a/, team_b/
│   └── Dockerfile
├── simulator/               simulator MCP server (port 8100)
│   ├── server.py  world.py  home.json  templates/
│   └── Dockerfile
├── frontend/                React + TypeScript + Vite
│   ├── src/                 api/ features/ live/ ui/ lib/
│   ├── nginx.conf           serves the build and proxies /api and /ws
│   └── Dockerfile
├── demo/                    scenarios (s1..), seed data, scenario runner
├── docs/                    CONTRACTS.md
├── docker-compose.yml
├── .env.example             single config file (copy to .env)
```

## Configuration

There is a single `.env` at the repo root. It holds both the Docker host ports and the backend
settings.

```bash
cp .env.example .env
```

Then edit `.env`:

| Variable | Purpose |
|---|---|
| `FRONTEND_PORT`, `BACKEND_PORT`, `SIMULATOR_PORT` | host ports for Docker (defaults 5173, 8001, 8100) |
| `AGENT_API_KEY` | key for `AGENT_MODEL` (Gemini by default with `auto`) |
| `AGENT_REASONING_API_KEY` | key for `AGENT_MODEL_REASONING` (Groq by default) |
| `ALTICE_MCP_URL`, `ALTICE_MCP_TOKEN` | Altice MCP URL and API key (`cgh_...`) |
| `USE_FAKES=1` | run the backend with fakes, no LLM or MCP calls |

`.env` is git-ignored. Never commit keys.
Settings are read from the root `.env`, whichever folder the backend is started from.
Environment variables you set yourself take precedence over the file.

## Run with Docker (recommended)

```bash
cp .env.example .env            # then fill in the keys
docker compose up --build -d
```

| Service | URL |
|---|---|
| Frontend | http://localhost:5173 |
| Backend API | http://localhost:8001 |
| Simulator MCP | http://localhost:8100/mcp |

After changing `.env`, recreate the backend so it reloads the values:

```bash
docker compose up -d --force-recreate backend
```

The SQLite database lives in the `backend-data` volume and survives rebuilds.

### Altice login (one time)

The Altice MCP needs an OAuth login. Run it once:

```bash
docker compose run --rm --service-ports altice-login
```

1. Open the URL it prints in your browser and sign in.
2. The browser is redirected to `mcp-redirect.futurelabsinnovations.com:5434`, which points at
   your machine, and the container stores the tokens in the `backend-data` volume.
3. Restart the backend so it picks the tokens up: `docker compose restart backend`.

Port 5434 must be free on the host. The token is refreshed automatically. Repeat the login only if
the refresh token expires.

Check the login state:

```bash
docker compose --profile login run --rm --no-deps altice-login python -m app.mcp_clients.auth status
```

## Run without Docker

Requires Python 3.11+, [uv](https://docs.astral.sh/uv/) and Node 18+.

```bash
# 1. simulator (port 8100)
cd simulator && uv sync && uv run python server.py

# 2. backend (port 8000), in another terminal
cd backend && uv sync && uv run uvicorn app.main:app --reload
#    USE_FAKES=1 runs it without LLM or MCP calls

# 3. frontend, in another terminal
cd frontend && npm install && npm run dev
```

Altice login without Docker: `cd backend && uv run python -m app.mcp_clients.auth login`.

On Windows, run `uv` from PowerShell or Git Bash. Do not share one `.venv` between Windows and WSL.

## Tests and checks

```bash
cd backend && uv run pytest              # backend tests
cd backend && uv run ruff check .        # lint
cd frontend && npm run typecheck         # TypeScript
```

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Chat returns 500, logs show `API key not valid` or `Invalid API Key` | wrong LLM keys in `.env`; fix them and recreate the backend |
| No Altice devices, logs show `login required` | run the Altice login, then `docker compose restart backend` |
| Port already in use | change the port in `.env` and run `docker compose up -d` |
| `uv sync` times out downloading packages | network issue; retry, or use Docker (timeout is already raised) |

## Demo

Scenarios live in `demo/scenarios/`. See [demo/README.md](demo/README.md).
