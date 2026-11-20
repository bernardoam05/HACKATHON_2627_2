"""Team A: model keys from .env reach the provider SDKs; fast and reasoning model chains."""
from __future__ import annotations

import os

import pytest
from pydantic_ai.models.fallback import FallbackModel

from app.agent.agent import (
    AUTO_MODELS,
    AUTO_REASONING_MODELS,
    DEFAULT_PROVIDER_MODELS,
    build_model,
    fast_names,
    reasoning_names,
)
from app.agent.wiring import export_agent_api_key
from app.config import Settings

VARS = ("GEMINI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GROQ_API_KEY")


def cfg(**kw) -> Settings:
    """Settings that ignore the developer's real backend/.env (keys must never leak into tests)."""
    return Settings(_env_file=None, **kw)


@pytest.fixture
def clean_env(monkeypatch):
    for var in VARS:
        monkeypatch.setenv(var, "placeholder")  # recorded, so monkeypatch restores it after
        monkeypatch.delenv(var)
    yield


def test_exports_to_provider_variable(clean_env):
    assert export_agent_api_key(cfg(AGENT_MODEL="auto", AGENT_API_KEY="g")) == "GEMINI_API_KEY"
    assert os.environ["GEMINI_API_KEY"] == "g"
    assert export_agent_api_key(cfg(AGENT_MODEL="openai:gpt-x", AGENT_API_KEY="o")) == "OPENAI_API_KEY"
    assert os.environ["OPENAI_API_KEY"] == "o"


def test_gemini_fast_with_openai_reasoning_get_their_own_keys(clean_env):
    s = cfg(AGENT_MODEL="auto", AGENT_API_KEY="gemini-key",
            AGENT_MODEL_REASONING="openai:", AGENT_REASONING_API_KEY="openai-key")
    assert set(export_agent_api_key(s).split(",")) == {"GEMINI_API_KEY", "OPENAI_API_KEY"}
    assert os.environ["GEMINI_API_KEY"] == "gemini-key"
    assert os.environ["OPENAI_API_KEY"] == "openai-key"


def test_real_environment_wins(clean_env, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "from-shell")
    export_agent_api_key(cfg(AGENT_MODEL="google:m", AGENT_API_KEY="from-dotenv"))
    assert os.environ["GEMINI_API_KEY"] == "from-shell"


def test_nothing_to_do(clean_env):
    assert export_agent_api_key(cfg(AGENT_MODEL="google:m", AGENT_API_KEY="")) is None
    assert export_agent_api_key(cfg(AGENT_MODEL="mystery:m", AGENT_API_KEY="k")) is None
    assert "GEMINI_API_KEY" not in os.environ


# --- the two tiers ----------------------------------------------------------------------------


def test_reasoning_defaults():
    assert reasoning_names(cfg(AGENT_MODEL="auto")) == list(AUTO_REASONING_MODELS)
    assert AUTO_REASONING_MODELS[0].endswith("flash")  # Flash before Lite
    assert reasoning_names(cfg(AGENT_MODEL="google:a")) == ["google:a"]  # single model: same


def test_openai_reasoning_falls_back_to_gemini_flash_then_lite():
    names = reasoning_names(cfg(AGENT_MODEL="auto", AGENT_MODEL_REASONING="openai:"))
    assert names[:2] == ["openai:gpt-5.4-mini", "openai:gpt-5-mini"]  # "openai:" alone
    gemini = names[2:]
    assert gemini == list(AUTO_REASONING_MODELS)  # OpenAI down / no credit -> Gemini Flash, Lite
    assert set(AUTO_MODELS) <= set(names) and len(names) == len(set(names))


def test_explicit_reasoning_model_and_fallback():
    names = reasoning_names(cfg(AGENT_MODEL="google:fast", AGENT_MODEL_REASONING="openai:gpt-z"))
    assert names == ["openai:gpt-z", "google:fast"]


def test_groq_reasoning_and_shared_pool(clean_env, monkeypatch):
    """groq: alone -> its free models first for reasoning; they are also the fast chain's last
    resort, and each provider gets its own key."""
    monkeypatch.setenv("GROQ_API_KEY", "placeholder")
    monkeypatch.delenv("GROQ_API_KEY")
    s = cfg(AGENT_MODEL="auto", AGENT_API_KEY="g", AGENT_MODEL_REASONING="groq:<modelo>",
            AGENT_REASONING_API_KEY="gsk")
    groq = list(DEFAULT_PROVIDER_MODELS["groq"])
    assert reasoning_names(s) == groq + list(AUTO_REASONING_MODELS)
    assert fast_names(s) == list(AUTO_MODELS) + groq
    export_agent_api_key(s)
    assert os.environ["GEMINI_API_KEY"] == "g" and os.environ["GROQ_API_KEY"] == "gsk"
    chain = build_model(fast_names(s))
    assert isinstance(chain, FallbackModel) and len(chain.models) == len(AUTO_MODELS) + len(groq)


def test_model_without_key_is_left_out_of_the_chain(clean_env):
    chain = build_model(["test", "groq:openai/gpt-oss-120b", "test"])  # no GROQ_API_KEY
    assert isinstance(chain, FallbackModel) and len(chain.models) == 2
