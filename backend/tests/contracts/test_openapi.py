"""Contract tests: every REST path of PLAN 5.5 exists; fakes serve them; unimplemented -> 501."""
import pytest
from fastapi.testclient import TestClient

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


def test_unimplemented_returns_501(monkeypatch):
    monkeypatch.setenv("USE_FAKES", "0")
    get_settings.cache_clear()
    with TestClient(app) as c:
        assert c.get("/api/devices").status_code == 501
    get_settings.cache_clear()


def test_fake_plugin_confirm_does_not_collide_with_action_confirm(client):
    body = {"type": "motionSensor", "displayName": "Motion", "purpose": "comfort"}
    r = client.put("/api/plugins/motionSensor", json=body)
    assert r.status_code == 200 and r.json()["status"] == "confirmed"
