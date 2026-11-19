"""MCP client base: async session wrapper with reconnect and timeouts.

# Owner: Team A
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client
from mcp.shared.exceptions import MCPError

from app.schemas import CapabilityUpdate, Device, DeviceSource

log = logging.getLogger(__name__)


class McpToolError(RuntimeError):
    """The server answered, but the tool reported an error."""


class McpUnavailable(RuntimeError):
    """Could not reach the server (connect failure, timeout, dropped session)."""


class McpAuthRequired(McpUnavailable):
    """No usable OAuth token: someone has to log in (`python -m app.mcp_clients.auth login`)."""


AUTH_RETRY_SECONDS = 60  # while login is required, do not hit the server on every poll


def _leaves(exc: BaseException) -> list[BaseException]:
    if isinstance(exc, BaseExceptionGroup):
        return [leaf for sub in exc.exceptions for leaf in _leaves(sub)]
    return [exc]


def _root_cause(exc: BaseException) -> BaseException:
    """anyio task groups wrap errors in ExceptionGroups: surface the meaningful one (auth first)."""
    leaves = _leaves(exc)
    return next((e for e in leaves if isinstance(e, McpAuthRequired)), leaves[0] if leaves else exc)


class McpClient:
    """Wraps an MCP session; methods: list_devices(), get_device(id), update_capabilities(id, CapabilityUpdate), rooms().

    The session lives in a dedicated background task (anyio contexts must be entered and exited in
    the same task), is opened lazily and re-opened after any transport failure. Calls are serialised.

    `target` is a Streamable HTTP URL or, in tests, an in-process `MCPServer`.
    """

    source: DeviceSource = DeviceSource.sim

    def __init__(
        self,
        target: Any,
        *,
        headers: dict[str, str] | None = None,
        auth: httpx2.Auth | None = None,
        timeout_seconds: float = 10.0,
        connect_timeout_seconds: float = 10.0,
    ) -> None:
        self._target = target
        self._headers = headers or {}
        self._auth = auth
        self._auth_blocked_until = 0.0  # monotonic time; set after McpAuthRequired
        self._timeout = timeout_seconds
        self._connect_timeout = connect_timeout_seconds
        self._client: Client | None = None
        self._runner: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._call_lock = asyncio.Lock()

    # --- session lifecycle ------------------------------------------------------------------

    async def _run_session(self, ready: asyncio.Future[None]) -> None:
        try:
            async with contextlib.AsyncExitStack() as stack:
                if isinstance(self._target, str):
                    http = await stack.enter_async_context(
                        create_mcp_http_client(headers=self._headers or None, auth=self._auth)
                    )
                    transport = streamable_http_client(self._target, http_client=http)
                    client = Client(transport, read_timeout_seconds=self._timeout)
                else:
                    client = Client(self._target, read_timeout_seconds=self._timeout)
                self._client = await stack.enter_async_context(client)
                ready.set_result(None)
                await self._stop.wait()
        except BaseException as e:  # noqa: BLE001 - surfaced to the waiting caller
            root = _root_cause(e)
            if not ready.done():
                if isinstance(root, McpAuthRequired):
                    ready.set_exception(McpAuthRequired(str(root)))
                else:
                    ready.set_exception(McpUnavailable(f"{self._target_label()}: {root!r}"))
            if isinstance(e, asyncio.CancelledError):
                raise
            if not isinstance(root, McpAuthRequired):
                log.warning("%s session ended: %r", self._target_label(), root)
        finally:
            self._client = None

    async def _ensure(self) -> Client:
        if self._client is not None and self._runner is not None and not self._runner.done():
            return self._client
        loop = asyncio.get_running_loop()
        if loop.time() < self._auth_blocked_until:
            raise McpAuthRequired(f"{self._target_label()}: login required")
        self._stop = asyncio.Event()
        ready: asyncio.Future[None] = loop.create_future()
        self._runner = asyncio.create_task(self._run_session(ready))
        try:
            await asyncio.wait_for(asyncio.shield(ready), self._connect_timeout)
        except TimeoutError as e:
            await self._reset()
            raise McpUnavailable(f"{self._target_label()}: connect timeout") from e
        except McpAuthRequired:
            self._auth_blocked_until = loop.time() + AUTH_RETRY_SECONDS
            log.warning("%s: OAuth login required: run `python -m app.mcp_clients.auth login` "
                        "in backend/ (retrying in %ss)", self._target_label(), AUTH_RETRY_SECONDS)
            raise
        assert self._client is not None
        return self._client

    async def _reset(self) -> None:
        self._stop.set()
        runner, self._runner = self._runner, None
        if runner is not None and not runner.done():
            try:  # graceful exit first, so the session can close cleanly
                await asyncio.wait_for(asyncio.shield(runner), 2.0)
            except BaseException:  # noqa: BLE001
                runner.cancel()
                with contextlib.suppress(BaseException):
                    await runner
        self._client = None

    async def close(self) -> None:
        await self._reset()

    def _target_label(self) -> str:
        return self._target if isinstance(self._target, str) else type(self._target).__name__

    # --- generic call -----------------------------------------------------------------------

    async def call(self, tool: str, arguments: dict[str, Any] | None = None) -> Any:
        """Call a tool and return its parsed JSON payload."""
        async with self._call_lock:
            client = await self._ensure()
            try:
                result = await asyncio.wait_for(
                    client.call_tool(tool, arguments or {}), self._timeout
                )
            except MCPError as e:
                # The server answered with a JSON-RPC error (e.g. -32602 "tool not allowed for
                # this client_type"): the session is fine, so keep it and report a tool error.
                raise McpToolError(f"{tool}: [{e.code}] {e.message}") from e
            except Exception as e:
                await self._reset()
                raise McpUnavailable(f"{self._target_label()} {tool}: {e!r}") from e
        payload = _parse_result(result)
        if result.is_error:
            raise McpToolError(f"{tool}: {payload}")
        return payload

    # --- device API (Altice-compatible tool names) ------------------------------------------

    async def list_devices(self) -> list[Device]:
        data = await self.call("get_home_devices")
        items = data.get("devices", []) if isinstance(data, dict) else data
        return [self._device(d) for d in items or []]

    async def get_device(self, device_id: str) -> Device:
        return self._device(await self.call("get_device_details", {"deviceId": device_id}))

    async def update_capabilities(self, device_id: str, update: CapabilityUpdate) -> Device | None:
        """Write one capability. Returns the updated device when the server sends it back."""
        body = update.model_dump(exclude_none=True)
        data = await self.call("update_device_capabilities", {"deviceId": device_id, "body": body})
        if isinstance(data, dict) and "id" in data and "type" in data:
            return self._device(data)
        return None

    async def rooms(self) -> list[dict[str, Any]]:
        data = await self.call("get_home_rooms")
        return data.get("rooms", []) if isinstance(data, dict) else []

    def _device(self, data: dict[str, Any]) -> Device:
        dev = Device.model_validate(data)
        dev.source = self.source
        return dev


def _parse_result(result: Any) -> Any:
    """Prefer structured content; fall back to JSON in the first text block."""
    structured = getattr(result, "structured_content", None)
    if structured is not None:
        # MCPServer wraps non-object returns as {"result": ...}
        if isinstance(structured, dict) and set(structured) == {"result"}:
            return structured["result"]
        return structured
    texts = [c.text for c in result.content or [] if getattr(c, "type", None) == "text"]
    if not texts:
        return None
    raw = texts[0] if len(texts) == 1 else "[" + ",".join(texts) + "]"
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw
