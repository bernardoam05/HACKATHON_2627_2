"""FastAPI app, lifespan wiring, 501 handler. (Shared contract)"""
from __future__ import annotations

import asyncio
import contextlib
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api import routes_agent, routes_devices, routes_plugins, ws
from app.api.ports import Services
from app.config import get_settings


class _Unimplemented:
    """Placeholder for every service until Team A wires real ones: calls raise -> 501."""

    def __getattr__(self, name: str):
        async def _raise(*_a, **_k):
            raise NotImplementedError(f"{name} is not implemented yet (Team A)")

        return _raise


def build_real_services() -> Services:
    """Team A replaces this with the real implementations."""
    u = _Unimplemented()
    return Services(devices=u, rooms=u, sim=u, plugins=u, agent=u, actions=u, history=u)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    task: asyncio.Task | None = None
    if settings.USE_FAKES:
        from app.api.fakes import build_fake_services

        app.state.services = build_fake_services()
    else:
        app.state.services = build_real_services()
        from app.poller.poller import run_poller  # Owner: Team A

        async def _guarded() -> None:
            with contextlib.suppress(NotImplementedError):
                await run_poller(app.state.services)

        task = asyncio.create_task(_guarded())
    yield
    if task:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="Smart Home Agent", lifespan=lifespan)
app.include_router(routes_devices.router)
app.include_router(routes_plugins.router)
app.include_router(routes_agent.router)
app.include_router(ws.router)


@app.exception_handler(NotImplementedError)
async def not_implemented(_: Request, exc: NotImplementedError) -> JSONResponse:
    return JSONResponse(status_code=501, content={"detail": str(exc) or "Not implemented"})
