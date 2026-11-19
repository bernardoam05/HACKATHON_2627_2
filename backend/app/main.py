"""FastAPI app, lifespan wiring, error handlers. (Shared contract)"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.api import routes_agent, routes_devices, routes_plugins, ws
from app.api.ports import Services
from app.config import Settings, get_settings

log = logging.getLogger(__name__)


class _Unimplemented:
    """Placeholder for every service until Team A wires real ones: calls raise -> 501."""

    def __getattr__(self, name: str):
        async def _raise(*_a, **_k):
            raise NotImplementedError(f"{name} is not implemented yet (Team A)")

        return _raise


def unimplemented_services() -> Services:
    u = _Unimplemented()
    return Services(devices=u, rooms=u, sim=u, plugins=u, agent=u, actions=u, history=u)


async def build_real_services(settings: Settings) -> Services:
    """Real services from Team A's `app.agent.wiring.build_services`; 501 everywhere until it exists.

    The returned container may expose `poller` (run by `run_poller`) and `aclose()` (shutdown).
    """
    try:
        from app.agent.wiring import build_services  # Owner: Team A
    except ModuleNotFoundError as e:
        if e.name != "app.agent.wiring":
            raise
        log.warning("app.agent.wiring not found: serving 501 for every service")
        return unimplemented_services()
    return await build_services(settings)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    task: asyncio.Task | None = None
    if settings.USE_FAKES:
        from app.api.fakes import build_fake_services

        app.state.services = build_fake_services()
    else:
        app.state.services = await build_real_services(settings)
        from app.poller.poller import run_poller  # Owner: Team A

        async def _guarded() -> None:
            try:
                await run_poller(app.state.services)
            except NotImplementedError:
                pass
            except Exception:
                log.exception("poller stopped")

        task = asyncio.create_task(_guarded())
    yield
    if task:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    aclose = getattr(app.state.services, "aclose", None)
    if aclose is not None:
        await aclose()


app = FastAPI(title="Smart Home Agent", lifespan=lifespan)
app.include_router(routes_devices.router)
app.include_router(routes_plugins.router)
app.include_router(routes_agent.router)
app.include_router(ws.router)


@app.exception_handler(NotImplementedError)
async def not_implemented(_: Request, exc: NotImplementedError) -> JSONResponse:
    return JSONResponse(status_code=501, content={"detail": str(exc) or "Not implemented"})


@app.exception_handler(KeyError)
async def not_found(_: Request, exc: KeyError) -> JSONResponse:
    """Services raise KeyError(id) for an unknown id."""
    missing = exc.args[0] if exc.args else ""
    return JSONResponse(status_code=404, content={"detail": f"Not found: {missing}"})


@app.exception_handler(ValueError)
async def conflict(request: Request, exc: ValueError) -> JSONResponse:
    """Services raise ValueError when the request conflicts with the current state
    (e.g. confirming an action that is no longer pending). Pydantic errors stay 500 (bugs)."""
    if isinstance(exc, ValidationError):
        log.error("unexpected validation error on %s: %s", request.url.path, exc)
        return JSONResponse(status_code=500, content={"detail": "Internal validation error"})
    return JSONResponse(status_code=409, content={"detail": str(exc) or "Conflict"})
