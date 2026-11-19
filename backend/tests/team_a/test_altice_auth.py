"""Team A: Altice MCP authentication (X-API-Key + X-Client-Type headers + Keycloak OAuth Bearer).
No network: nothing here talks to the real Altice MCP or Keycloak."""
from __future__ import annotations

import asyncio
import socket

import pytest
from mcp.shared.auth import OAuthToken

from app.config import Settings
from app.mcp_clients import auth as auth_mod
from app.mcp_clients.altice import AlticeClient
from app.mcp_clients.auth import LocalCallback, SqliteTokenStorage, build_provider, token_status
from app.mcp_clients.base import McpAuthRequired, McpUnavailable, _root_cause
from app.storage.db import init_db
from app.storage.repository import Repository

SETTINGS = Settings(ALTICE_MCP_URL="https://altice.example/mcp", ALTICE_MCP_TOKEN="k",
                    ALTICE_OAUTH_CLIENT_ID="mcp-client",
                    ALTICE_OAUTH_REDIRECT_URI="http://localhost:5434/callback")


@pytest.fixture
async def repo():
    r = Repository(await init_db(":memory:"))
    yield r
    await r.close()


def test_api_key_goes_in_x_api_key_not_bearer():
    c = AlticeClient("https://altice.example/mcp", "cgh_secret", "core")
    assert c._headers == {"X-API-Key": "cgh_secret", "X-Client-Type": "core"}
    assert "Authorization" not in c._headers


async def test_token_storage_returns_remaining_lifetime(repo, monkeypatch):
    storage = SqliteTokenStorage(repo, SETTINGS)
    assert await storage.get_tokens() is None
    monkeypatch.setattr(auth_mod.time, "time", lambda: 1_000.0)
    await storage.set_tokens(OAuthToken(access_token="a", token_type="Bearer", expires_in=300,
                                        refresh_token="r"))
    monkeypatch.setattr(auth_mod.time, "time", lambda: 1_200.0)  # 200 s later (e.g. restart)
    token = await storage.get_tokens()
    assert token is not None and token.expires_in == 100 and token.refresh_token == "r"
    monkeypatch.setattr(auth_mod.time, "time", lambda: 2_000.0)
    assert (await storage.get_tokens()).expires_in == 0  # expired -> SDK refreshes

    status = await token_status(repo)
    assert status["logged_in"] and status["has_refresh_token"]
    assert set(status) == {"logged_in", "obtained_at", "access_expires_in_s",
                           "has_refresh_token", "scope"}  # never the tokens themselves


async def test_client_is_preregistered_never_dynamic(repo):
    info = await SqliteTokenStorage(repo, SETTINGS).get_client_info()
    assert info.client_id == "mcp-client"
    assert [str(u) for u in info.redirect_uris] == ["http://localhost:5434/callback"]
    assert info.token_endpoint_auth_method == "none"


async def test_backend_provider_never_opens_a_browser(repo):
    provider = build_provider(SETTINGS, repo, interactive=False)
    with pytest.raises(McpAuthRequired, match="auth login"):
        await provider.context.redirect_handler("https://keycloak.example/auth?x=1")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _get(port: int, path: str) -> None:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(f"GET {path} HTTP/1.1\r\nHost: localhost\r\n\r\n".encode())
    await writer.drain()
    await reader.read()
    writer.close()


async def test_local_callback_catches_the_code():
    port = _free_port()
    cb = LocalCallback(f"http://localhost:{port}/callback", open_browser=False)
    await cb.open_browser("https://keycloak.example/auth")
    await _get(port, "/favicon.ico")  # other paths are ignored
    await _get(port, "/callback?state=s1&iss=https%3A%2F%2Fkc&code=abc")
    result = await cb.wait_for_code()
    assert (result.code, result.state, result.iss) == ("abc", "s1", "https://kc")


async def test_local_callback_reports_refused_login():
    port = _free_port()
    cb = LocalCallback(f"http://localhost:{port}/callback", open_browser=False)
    await cb.open_browser("https://keycloak.example/auth")
    await _get(port, "/callback?error=access_denied&state=s1")
    with pytest.raises(McpAuthRequired, match="access_denied"):
        await cb.wait_for_code()


def test_root_cause_prefers_auth_error_inside_exception_groups():
    nested = ExceptionGroup("tg", [ExceptionGroup("inner", [
        ConnectionError("x"), McpAuthRequired("login required")])])
    assert isinstance(_root_cause(nested), McpAuthRequired)
    assert isinstance(_root_cause(ExceptionGroup("tg", [ConnectionError("y")])), ConnectionError)


async def test_login_required_does_not_hit_the_server_on_every_poll():
    c = AlticeClient("https://altice.example/mcp", "k")
    c._auth_blocked_until = asyncio.get_running_loop().time() + 60
    with pytest.raises(McpAuthRequired):
        await c.list_devices()
    assert c._runner is None  # no connection attempt
    assert issubclass(McpAuthRequired, McpUnavailable)  # poller treats it as a source being down
