# Demo (Team B)

Scripts and scenarios for the S1–S5 demo. They only talk to the **simulator** and read the backend; they never write to the real Altice house.

## Once: seed history (S3)

```
python demo/seed_history.py                     # writes demo/seed/history.jsonl (14 days, deterministic)
cd backend
uv run python -m app.storage.import_seed ../demo/seed/history.jsonl --align-to-today
```

## Start everything

```
cd simulator && uv run python server.py                              # :8100
cd backend   && uv run uvicorn app.main:app                          # :8000 (see backend_env per scenario)
cd frontend  && npm run dev                                          # :5173
```

In the **Gestor** tab, create the rooms (Sala, Cozinha, Corredor, Entrada). Mark **Entrada** as "É a entrada da casa", then assign the simulated devices to their rooms. S4 needs the entrance room.

## Run a scenario

```
cd simulator
uv run python ../demo/run_scenario.py s1            # play the steps, then check what the house did
uv run python ../demo/run_scenario.py s4 --no-check # only play the steps (e.g. while presenting)
```

Exit code: 0 passed, 1 an expectation failed, 2 the scenario could not run.

| Scenario | Backend env | What to look at |
|---|---|---|
| s1 | `NOW_OVERRIDE=2026-11-19T21:00:00` | TV on -> living-room light dims (Chat / Atividade) |
| s2 | none | Kitchen -> corridor: lights follow. Run it a few times until a reflex is proposed, approve it (Aprendizagem), then run it again: instant |
| s3 | `NOW_OVERRIDE=2026-11-20T07:20:00`, seed imported | Coffee machine switches on before 07:30 |
| s4 | `EMPTY_AFTER_MINUTES=0.5` | Door opens/closes, then no motion: lights off; arming the alarm is suggested (real gateway only) |
| s5 | none | A smart lock of an unknown type appears; configure it in Gestor (Básico / Avançado) |

The backend reads `NOW_OVERRIDE` and `EMPTY_AFTER_MINUTES` at startup, so restart it between scenarios that need different values.
