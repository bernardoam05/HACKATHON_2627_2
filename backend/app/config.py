"""Settings from environment. (Shared contract)"""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    ALTICE_MCP_URL: str = ""
    # Altice MCP API key (the "cgh_..." key): sent as X-API-Key, NOT as the Bearer token.
    ALTICE_MCP_TOKEN: str = Field(default="", repr=False)  # secrets never in reprs/logs
    ALTICE_CLIENT_TYPE: str = "core"  # sent as X-Client-Type; decides which tools are visible
    # Keycloak OAuth (authorization code + PKCE) for the Bearer token. The redirect host resolves
    # to 127.0.0.1: `python -m app.mcp_clients.auth login` listens on its port to catch the code.
    ALTICE_OAUTH_CLIENT_ID: str = "mcp-client"
    ALTICE_OAUTH_REDIRECT_URI: str = "http://mcp-redirect.futurelabsinnovations.com:5434/callback"
    SIM_MCP_URL: str = "http://localhost:8100/mcp"
    # Fast model for everyday reactions: one model, a comma-separated fallback list, or "auto".
    AGENT_MODEL: str = "auto"
    # Key for the AGENT_MODEL provider; exported to its standard variable (GEMINI_API_KEY,
    # OPENAI_API_KEY, ...) at startup, because values read from .env are not process env vars.
    AGENT_API_KEY: str = Field(default="", repr=False)
    # Model for runs that need reasoning (chat, startup/daily review, wake-ups, onboarding).
    # Same syntax as AGENT_MODEL ("openai:" alone = the default OpenAI model). Empty: like
    # AGENT_MODEL ("auto" -> Gemini Flash first). The fast chain is always appended as fallback.
    AGENT_MODEL_REASONING: str = ""
    # Key for the reasoning provider when it differs from AGENT_MODEL's (e.g. OpenAI + Gemini).
    AGENT_REASONING_API_KEY: str = Field(default="", repr=False)
    DB_PATH: str = "home.db"
    SIM_POLL_SECONDS: float = 2
    ALTICE_POLL_SECONDS: float = 7
    USE_FAKES: bool = False
    NOW_OVERRIDE: str | None = None  # ISO datetime, fakes the clock for demos (house local time)
    # The house's time zone (IANA): schedules ("07:30"), routines and "Now" are in local time.
    TIMEZONE: str = "Europe/Lisbon"
    EMPTY_AFTER_MINUTES: float = 10  # 0.5 for demo
    FULL_SNAPSHOT_EVERY_RUNS: int = 20
    FULL_SNAPSHOT_EVERY_SECONDS: int = 900
    INSIGHTS_IN_PROMPT: int = 8
    INSIGHT_MIN_CONFIDENCE: float = 0.2
    REFLEX_MIN_CONFIDENCE: float = 0.8
    REFLEX_MAX_UNDOS: int = 2
    OVERRIDE_WINDOW_MINUTES: float = 10  # a user change this soon after an agent action overrides it
    OVERRIDE_COOLDOWN_MINUTES: float = 20  # default blocking after an override (user can change it)


@lru_cache
def get_settings() -> Settings:
    return Settings()
