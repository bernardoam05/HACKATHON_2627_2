"""Team A: AGENT_API_KEY from .env reaches the provider SDK."""
from __future__ import annotations

import os

import pytest

from app.agent.wiring import export_agent_api_key
from app.config import Settings


@pytest.fixture
def clean_env(monkeypatch):
    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.setenv(var, "placeholder")  # recorded, so monkeypatch restores it after
        monkeypatch.delenv(var)
    yield


def test_exports_to_provider_variable(clean_env):
    s = Settings(AGENT_MODEL="anthropic:claude-sonnet-5-5", AGENT_API_KEY="test-key")
    assert export_agent_api_key(s) == "ANTHROPIC_API_KEY"
    assert os.environ["ANTHROPIC_API_KEY"] == "test-key"

    s = Settings(AGENT_MODEL="openai:gpt-x", AGENT_API_KEY="other")
    assert export_agent_api_key(s) == "OPENAI_API_KEY"
    assert os.environ["OPENAI_API_KEY"] == "other"


def test_real_environment_wins(clean_env, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-shell")
    export_agent_api_key(Settings(AGENT_MODEL="anthropic:m", AGENT_API_KEY="from-dotenv"))
    assert os.environ["ANTHROPIC_API_KEY"] == "from-shell"


def test_nothing_to_do(clean_env):
    assert export_agent_api_key(Settings(AGENT_MODEL="anthropic:m", AGENT_API_KEY="")) is None
    assert export_agent_api_key(Settings(AGENT_MODEL="mystery:m", AGENT_API_KEY="k")) is None
    assert "ANTHROPIC_API_KEY" not in os.environ
