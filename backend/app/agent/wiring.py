"""Builds the real service graph for main.py (Team A side of the wiring).

# Owner: Team A
"""
from __future__ import annotations

from typing import Any

from app.api.ports import Services
from app.api.ws import broadcaster
from app.config import Settings
from app.devices.registry import DeviceRegistry
from app.devices.rooms import RoomStore
from app.mcp_clients.altice import AlticeClient
from app.mcp_clients.base import McpClient
from app.mcp_clients.sim import SimClient
from app.plugins.registry import PluginRegistry
from app.poller.poller import Poller
from app.poller.scheduler import LatestWinsScheduler
from app.schemas import AgentStatusMsg, DeviceSource
from app.storage.db import init_db
from app.storage.repository import Repository

from .agent import Clock, HomeAgent
from .executor import Executor
from .feedback import FeedbackService
from .insights import InsightEngine
from .onboarding import Onboarding
from .reflexes import ReflexEngine
from .tools import PresenceWatcher


async def build_services(
    settings: Settings,
    clients: dict[DeviceSource, McpClient] | None = None,
    model: Any = None,
) -> Services:
    """Real services. `clients`/`model` are injectable for tests.

    Extra attributes on the returned container: `poller`, `scheduler`, `repo`, `aclose()`.
    """
    repo = Repository(await init_db(settings.DB_PATH))
    if clients is None:
        clients = {}
        if settings.SIM_MCP_URL:
            clients[DeviceSource.sim] = SimClient(settings.SIM_MCP_URL)
        if settings.ALTICE_MCP_URL:
            clients[DeviceSource.altice] = AlticeClient(
                settings.ALTICE_MCP_URL, settings.ALTICE_MCP_TOKEN
            )

    rooms = RoomStore(repo)
    registry = DeviceRegistry(repo, rooms, clients)
    onboarding = Onboarding(model or settings.AGENT_MODEL)
    plugins = PluginRegistry(repo, devices=registry.list_devices, proposer=onboarding.propose)
    broadcast = broadcaster.broadcast
    clock = Clock(settings.NOW_OVERRIDE)
    insights = InsightEngine(
        repo, broadcast, settings.INSIGHT_MIN_CONFIDENCE, settings.REFLEX_MAX_UNDOS
    )
    feedback = FeedbackService(repo, insights)
    executor = Executor(registry, repo, plugins, broadcast, feedback)
    reflexes = ReflexEngine(
        repo, registry, executor, plugins, broadcast, clock.now, settings.REFLEX_MIN_CONFIDENCE
    )
    agent = HomeAgent(
        settings, registry, repo, plugins, rooms, executor, broadcast,
        insights=insights, reflexes=reflexes, feedback=feedback, clock=clock, model=model,
    )
    presence = PresenceWatcher(registry, repo, settings.EMPTY_AFTER_MINUTES * 60, agent.wake)

    async def on_status(busy: bool, dirty: bool) -> None:
        await broadcast(AgentStatusMsg(busy=busy, dirty=dirty))

    async def run_agent() -> None:
        await agent.run_cycle()

    scheduler = LatestWinsScheduler(run_agent, on_status)
    agent.scheduler = scheduler
    poller = Poller(
        registry,
        repo,
        scheduler,
        broadcast,
        intervals={
            DeviceSource.sim: settings.SIM_POLL_SECONDS,
            DeviceSource.altice: settings.ALTICE_POLL_SECONDS,
        },
        hooks=[feedback, reflexes, presence],  # order matters: overrides before reflexes
        plugins=plugins,
    )

    services = Services(
        devices=registry,
        rooms=rooms,
        sim=registry,
        plugins=plugins,
        agent=agent,
        actions=executor,
        history=repo,
    )

    async def aclose() -> None:
        presence.cancel()
        agent.wakeups.cancel_all()
        await scheduler.stop()
        for c in clients.values():
            await c.close()
        await repo.close()

    services.poller = poller  # type: ignore[attr-defined]
    services.scheduler = scheduler  # type: ignore[attr-defined]
    services.repo = repo  # type: ignore[attr-defined]
    services.reflexes = reflexes  # type: ignore[attr-defined]
    services.insights = insights  # type: ignore[attr-defined]
    services.feedback = feedback  # type: ignore[attr-defined]
    services.presence = presence  # type: ignore[attr-defined]
    services.aclose = aclose  # type: ignore[attr-defined]
    services.on_ready = agent.request_startup  # type: ignore[attr-defined]
    return services
