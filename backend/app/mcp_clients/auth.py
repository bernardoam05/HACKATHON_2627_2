"""OAuth (Keycloak, authorization code + PKCE) for the Altice MCP.

The client `mcp-client` is pre-registered; its redirect host resolves to 127.0.0.1, so a one-shot
local listener on the redirect port catches the code. Run once in backend/:

    uv run python -m app.mcp_clients.auth login    # opens the browser, stores the tokens in SQLite
    uv run python -m app.mcp_clients.auth status   # stored? expiry? refresh token?
    uv run python -m app.mcp_clients.auth logout

Afterwards the backend runs unattended: tokens are read from DB_PATH and refreshed automatically.
The backend never opens a browser: without a usable token it raises McpAuthRequired.

# Owner: Team A
"""
from __future__ import annotations

import asyncio
import sys
import time
import webbrowser
from urllib.parse import parse_qs, urlparse

from mcp.client.auth import OAuthClientProvider
from mcp.shared.auth import (
    AuthorizationCodeResult,
    OAuthClientInformationFull,
    OAuthClientMetadata,
    OAuthToken,
)

from app.config import Settings, get_settings
from app.storage.repository import Repository

from .base import McpAuthRequired

TOKEN_KEY = "altice.oauth.tokens"
LOGIN_TIMEOUT_S = 300


class SqliteTokenStorage:
    """MCP SDK TokenStorage backed by our SQLite `kv` table.

    The SDK only learns a token's lifetime from `expires_in` when it receives it, so after a
    restart it would treat an old access token as fresh, get a 401 and fall into an interactive
    login. We store when the token was obtained and hand back the remaining lifetime, so the SDK
    refreshes instead.
    """

    def __init__(self, repo: Repository, settings: Settings) -> None:
        self._repo = repo
        self._settings = settings

    async def get_tokens(self) -> OAuthToken | None:
        stored = await self._repo.kv_get(TOKEN_KEY)
        if not stored:
            return None
        token = OAuthToken.model_validate(stored["token"])
        if token.expires_in is not None:
            elapsed = int(time.time() - stored["obtained_at"])
            token.expires_in = max(0, token.expires_in - elapsed)
        return token

    async def set_tokens(self, tokens: OAuthToken) -> None:
        await self._repo.kv_set(
            TOKEN_KEY, {"token": tokens.model_dump(mode="json"), "obtained_at": time.time()}
        )

    async def get_client_info(self) -> OAuthClientInformationFull:
        # Pre-registered in Keycloak: never let the SDK try dynamic client registration.
        return OAuthClientInformationFull(
            client_id=self._settings.ALTICE_OAUTH_CLIENT_ID,
            **client_metadata(self._settings).model_dump(),
        )

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        """Nothing to store: the client id comes from settings."""


def client_metadata(settings: Settings) -> OAuthClientMetadata:
    return OAuthClientMetadata(
        client_name="Smart Home Agent",
        redirect_uris=[settings.ALTICE_OAUTH_REDIRECT_URI],
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        token_endpoint_auth_method="none",
    )


async def _refuse_interactive_login(*_: object) -> AuthorizationCodeResult:
    raise McpAuthRequired(
        "Altice login required: run `uv run python -m app.mcp_clients.auth login` in backend/"
    )


def build_provider(
    settings: Settings, repo: Repository, interactive: bool = False
) -> OAuthClientProvider:
    """httpx2 auth for the Altice MCP. Non-interactive (the backend) never opens a browser."""
    storage = SqliteTokenStorage(repo, settings)
    if interactive:
        callback = LocalCallback(settings.ALTICE_OAUTH_REDIRECT_URI)
        redirect, wait = callback.open_browser, callback.wait_for_code
    else:
        redirect = wait = _refuse_interactive_login
    return OAuthClientProvider(
        server_url=settings.ALTICE_MCP_URL,
        client_metadata=client_metadata(settings),
        storage=storage,
        redirect_handler=redirect,
        callback_handler=wait,
    )


async def token_status(repo: Repository) -> dict[str, object]:
    """No secrets: only whether a token exists and when it expires."""
    stored = await repo.kv_get(TOKEN_KEY)
    if not stored:
        return {"logged_in": False}
    token, obtained = stored["token"], stored["obtained_at"]
    expires_in = token.get("expires_in")
    return {
        "logged_in": True,
        "obtained_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(obtained)),
        "access_expires_in_s": None if expires_in is None else int(obtained + expires_in - time.time()),
        "has_refresh_token": bool(token.get("refresh_token")),
        "scope": token.get("scope"),
    }


class LocalCallback:
    """One-shot HTTP listener on the redirect URI's port; resolves with the authorization code."""

    _PAGE = (
        b"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nConnection: close\r\n\r\n"
        b"<html><body><h2>Casa Inteligente: login conclu\xc3\xaddo.</h2>"
        b"<p>Pode fechar esta janela.</p></body></html>"
    )

    def __init__(self, redirect_uri: str, open_browser: bool = True) -> None:
        parsed = urlparse(redirect_uri)
        self.port = parsed.port or 80
        self._path = parsed.path or "/"
        self._open = open_browser
        self._result: asyncio.Future[AuthorizationCodeResult] | None = None
        self._server: asyncio.Server | None = None

    async def open_browser(self, authorization_url: str) -> None:
        self._result = asyncio.get_running_loop().create_future()
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", self.port)
        print(f"\nOpen this URL to log in:\n\n{authorization_url}\n", flush=True)
        if self._open:
            webbrowser.open(authorization_url)

    async def wait_for_code(self) -> AuthorizationCodeResult:
        assert self._result is not None and self._server is not None
        try:
            return await asyncio.wait_for(self._result, LOGIN_TIMEOUT_S)
        finally:
            self._server.close()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        parts = (await reader.readline()).decode(errors="replace").split()
        target = urlparse(parts[1]) if len(parts) >= 2 else None
        params = parse_qs(target.query) if target else {}
        writer.write(self._PAGE)
        await writer.drain()
        writer.close()
        if target is None or target.path != self._path or not self._result or self._result.done():
            return
        if "error" in params:
            self._result.set_exception(McpAuthRequired(f"Login refused: {params['error'][0]}"))
        elif "code" in params:
            self._result.set_result(AuthorizationCodeResult(
                code=params["code"][0],
                state=params.get("state", [None])[0],
                iss=params.get("iss", [None])[0],
            ))


async def _with_repo(settings: Settings):
    from app.storage.db import init_db

    return Repository(await init_db(settings.DB_PATH))


async def _login() -> int:
    from .altice import AlticeClient

    settings = get_settings()
    if not settings.ALTICE_MCP_URL:
        print("ALTICE_MCP_URL is not set in the root .env")
        return 2
    repo = await _with_repo(settings)
    client = AlticeClient(
        settings.ALTICE_MCP_URL, settings.ALTICE_MCP_TOKEN, settings.ALTICE_CLIENT_TYPE,
        auth=build_provider(settings, repo, interactive=True), timeout_seconds=30,
    )
    client._connect_timeout = LOGIN_TIMEOUT_S
    try:
        devices = await client.list_devices()  # read-only: proves the whole chain works
        print(f"Logged in. get_home_devices: {len(devices)} devices")
    finally:
        await client.close()
    print(await token_status(repo))
    await repo.close()
    return 0


async def _status(logout: bool = False) -> int:
    repo = await _with_repo(get_settings())
    if logout:
        await repo.kv_delete(TOKEN_KEY)
        print("Tokens removed.")
    else:
        print(await token_status(repo))
    await repo.close()
    return 0


def main(argv: list[str]) -> int:
    command = argv[1] if len(argv) > 1 else "status"
    if command == "login":
        return asyncio.run(_login())
    if command in ("status", "logout"):
        return asyncio.run(_status(logout=command == "logout"))
    print("usage: python -m app.mcp_clients.auth [login|status|logout]")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
