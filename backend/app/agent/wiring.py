"""Builds the real service graph for main.py (Team A side of the wiring).

# Owner: Team A
"""
from __future__ import annotations

import logging
import os
from typing import Any

from app.api.ports import Services
from app.api.ws import broadcaster
from app.config import Settings
from app.devices.registry import DeviceRegistry
from app.devices.rooms import RoomStore
from app.mcp_clients.altice import AlticeClient
from app.mcp_clients.auth import build_provider
from app.mcp_clients.base import McpClient
from app.mcp_clients.sim import SimClient
from app.plugins.registry import PluginRegistry
from app.poller.poller import Poller
from app.poller.scheduler import LatestWinsScheduler
from app.schemas import AgentStatusMsg, DeviceSource
from app.storage.db import init_db
from app.storage.repository import Repository

from .agent import Clock, HomeAgent, build_model, model_names
from .executor import Executor
from .feedback import FeedbackService
from .insights import InsightEngine
from .onboarding import Onboarding
from .reflexes import ReflexEngine
from .tools import PresenceWatcher

log = logging.getLogger(__name__)


# Provider prefix of AGENT_MODEL ("anthropic:claude-...") -> the env var its SDK reads.
PROVIDER_KEY_ENV = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "google-gla": "GEMINI_API_KEY",
    "google": "GEMINI_API_KEY",
    "groq": "GROQ_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "xai": "XAI_API_KEY",
}


def export_agent_api_key(settings: Settings) -> str | None:
    """Make AGENT_API_KEY visible to the provider SDK(s) of AGENT_MODEL (one model, a list or
    `auto`). A variable already set in the real environment wins. Returns the env var names
    used, comma-separated (None if nothing to do)."""
    if not settings.AGENT_API_KEY:
        return None
    exported: list[str] = []
    for name in model_names(settings.AGENT_MODEL):
        provider = name.split(":", 1)[0] if ":" in name else ""
        var = PROVIDER_KEY_ENV.get(provider)
        if var is None:
            log.warning("AGENT_API_KEY set but provider %r is unknown; export its key yourself",
                        provider)
            continue
        os.environ.setdefault(var, settings.AGENT_API_KEY)
        if var not in exported:
            exported.append(var)
    return ",".join(exported) or None


async def build_services(
    settings: Settings,
    clients: dict[DeviceSource, McpClient] | None = None,
    model: Any = None,
) -> Services:
    """Real services. `clients`/`model` are injectable for tests.

    Extra attributes on the returned container: `poller`, `scheduler`, `repo`, `aclose()`.
    """
    export_agent_api_key(settings)
    repo = Repository(await init_db(settings.DB_PATH))
    if clients is None:
        clients = {}
        if settings.SIM_MCP_URL:
            clients[DeviceSource.sim] = SimClient(settings.SIM_MCP_URL)
        if settings.ALTICE_MCP_URL:
            clients[DeviceSource.altice] = AlticeClient(
                settings.ALTICE_MCP_URL,
                settings.ALTICE_MCP_TOKEN,
                settings.ALTICE_CLIENT_TYPE,
                auth=build_provider(settings, repo),  # never interactive in the backend
            )

    rooms = RoomStore(repo)
    registry = DeviceRegistry(repo, rooms, clients)
    onboarding = Onboarding(model or build_model(settings.AGENT_MODEL))
    plugins = PluginRegistry(repo, devices=registry.list_devices, proposer=onboarding.propose)
    broadcast = broadcaster.broadcast
    clock = Clock(settings.NOW_OVERRIDE)
    insights = InsightEngine(
        repo, broadcast, settings.INSIGHT_MIN_CONFIDENCE, settings.REFLEX_MAX_UNDOS
    )
    feedback = FeedbackService(
        repo, insights, broadcast,
        default_minutes=settings.OVERRIDE_COOLDOWN_MINUTES,
        override_window_minutes=settings.OVERRIDE_WINDOW_MINUTES,
        device_check=registry.get_device,
    )
    await feedback.load()
    executor = Executor(registry, repo, plugins, broadcast, feedback)
    registry.manual_handler = executor.manual
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
        cooldowns=feedback,
    )

    async def aclose() -> None:
        presence.cancel()
        feedback.close()
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
