"""Settings from environment. (Shared contract)"""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    ALTICE_MCP_URL: str = ""
    ALTICE_MCP_TOKEN: str = ""
    SIM_MCP_URL: str = "http://localhost:8100/mcp"
    AGENT_MODEL: str = "anthropic:claude-sonnet-5-5"
    # Key for the AGENT_MODEL provider; exported to its standard variable (ANTHROPIC_API_KEY,
    # OPENAI_API_KEY, ...) at startup, because values read from .env are not process env vars.
    AGENT_API_KEY: str = ""
    DB_PATH: str = "home.db"
    SIM_POLL_SECONDS: float = 2
    ALTICE_POLL_SECONDS: float = 7
    USE_FAKES: bool = False
    NOW_OVERRIDE: str | None = None  # ISO datetime, fakes the clock for demos
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
