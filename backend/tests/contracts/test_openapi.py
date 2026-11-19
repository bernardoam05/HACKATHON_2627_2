"""Contract tests: every REST path of PLAN 5.5 exists; fakes serve them; unimplemented -> 501."""
import pytest
from fastapi.testclient import TestClient

from app import main
from app.api.ports import Services
from app.config import get_settings
from app.main import app

EXPECTED = {
    ("get", "/api/devices"), ("get", "/api/devices/{device_id}"), ("patch", "/api/devices/{device_id}"),
    ("post", "/api/devices/{device_id}/capabilities"), ("post", "/api/sim/devices"),
    ("delete", "/api/sim/devices/{device_id}"), ("get", "/api/sim/templates"),
    ("get", "/api/rooms"), ("post", "/api/rooms"), ("delete", "/api/rooms/{room_id}"),
    ("get", "/api/plugins"), ("get", "/api/plugins/unconfigured"),
    ("post", "/api/plugins/{type}/propose"), ("put", "/api/plugins/{type}"),
    ("post", "/api/chat"), ("get", "/api/decisions"), ("get", "/api/actions"),
    ("post", "/api/actions/{action_id}/confirm"), ("post", "/api/actions/{action_id}/reject"),
    ("post", "/api/actions/{action_id}/undo"), ("get", "/api/history/changes"),
    ("get", "/api/agent/status"), ("get", "/api/insights"), ("delete", "/api/insights/{insight_id}"),
    ("get", "/api/reflexes"), ("post", "/api/reflexes/{reflex_id}/confirm"),
    ("post", "/api/reflexes/{reflex_id}/pause"), ("post", "/api/reflexes/{reflex_id}/resume"),
    ("delete", "/api/reflexes/{reflex_id}"),
    ("get", "/api/cooldowns"), ("put", "/api/cooldowns/{device_id}"),
    ("delete", "/api/cooldowns/{device_id}"),
}


def test_all_paths_exist():
    paths = app.openapi()["paths"]
    have = {(m, p) for p, ops in paths.items() for m in ops}
    assert EXPECTED <= have, EXPECTED - have


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("USE_FAKES", "1")
    get_settings.cache_clear()
    with TestClient(app) as c:
        yield c
    get_settings.cache_clear()


def test_fakes_serve_devices(client):
    r = client.get("/api/devices")
    assert r.status_code == 200 and len(r.json()) >= 1


def test_fake_chat_and_actions(client):
    assert client.post("/api/chat", json={"message": "olá"}).status_code == 200
    assert client.post("/api/actions/act-1/undo").json()["status"] == "undone"


def _real_mode(monkeypatch, services=None):
    """USE_FAKES=0 with no MCP sources and an in-memory DB (never touches the real house)."""
    monkeypatch.setenv("USE_FAKES", "0")
    monkeypatch.setenv("DB_PATH", ":memory:")
    monkeypatch.setenv("SIM_MCP_URL", "")
    monkeypatch.setenv("ALTICE_MCP_URL", "")
    get_settings.cache_clear()
    if services is not None:

        async def _build(_settings):
            return services

        monkeypatch.setattr(main, "build_real_services", _build)


def test_unimplemented_returns_501(monkeypatch):
    _real_mode(monkeypatch, main.unimplemented_services())
    with TestClient(app) as c:
        assert c.get("/api/devices").status_code == 501
    get_settings.cache_clear()


class _Raising:
    """Service stub: get_device raises the exception it was built with."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def get_device(self, device_id: str):
        raise self._exc


@pytest.mark.parametrize(
    ("exc", "status"),
    [(KeyError("nope"), 404), (ValueError("not pending"), 409), (NotImplementedError(), 501)],
)
def test_service_errors_map_to_http_status(monkeypatch, exc, status):
    u = main.unimplemented_services()
    services = Services(devices=_Raising(exc), rooms=u.rooms, sim=u.sim, plugins=u.plugins,
                        agent=u.agent, actions=u.actions, history=u.history)
    _real_mode(monkeypatch, services)
    with TestClient(app) as c:
        assert c.get("/api/devices/nope").status_code == status
    get_settings.cache_clear()


def test_real_services_wired(monkeypatch):
    pytest.importorskip("app.agent.wiring")
    _real_mode(monkeypatch)
    with TestClient(app) as c:
        assert c.get("/api/devices").json() == []
        assert c.get("/api/devices/nope").status_code == 404
        assert c.post("/api/actions/nope/confirm").status_code == 404
        assert c.get("/api/agent/status").status_code == 200
    get_settings.cache_clear()


def test_fake_cooldowns_options(client):
    [cd] = client.get("/api/cooldowns").json()
    assert cd["reason"] == "manual_override" and cd["note"]
    dev = cd["deviceId"]

    shorter = client.put(f"/api/cooldowns/{dev}", json={"minutes": 5, "rememberForDevice": True})
    assert shorter.status_code == 200 and shorter.json()["defaultMinutes"] == 5
    forever = client.put(f"/api/cooldowns/{dev}", json={"untilUnblocked": True})
    assert forever.status_code == 200 and forever.json()["until"] is None
    assert client.delete(f"/api/cooldowns/{dev}").status_code == 204
    assert client.get("/api/cooldowns").json() == []

    for bad in ({}, {"minutes": 5, "untilUnblocked": True},
                {"untilUnblocked": True, "rememberForDevice": True}, {"minutes": -1}):
        assert client.put(f"/api/cooldowns/{dev}", json=bad).status_code == 422, bad
    assert client.put("/api/cooldowns/nope", json={"minutes": 5}).status_code == 404


def test_cooldowns_501_when_not_wired(monkeypatch):
    u = main.unimplemented_services()
    services = Services(devices=u.devices, rooms=u.rooms, sim=u.sim, plugins=u.plugins,
                        agent=u.agent, actions=u.actions, history=u.history)  # no cooldowns
    _real_mode(monkeypatch, services)
    with TestClient(app) as c:
        assert c.get("/api/cooldowns").status_code == 501
    get_settings.cache_clear()


def test_fake_plugin_confirm_does_not_collide_with_action_confirm(client):
    body = {"type": "motionSensor", "displayName": "Motion", "purpose": "comfort"}
    r = client.put("/api/plugins/motionSensor", json=body)
    assert r.status_code == 200 and r.json()["status"] == "confirmed"
