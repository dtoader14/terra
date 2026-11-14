from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app as server
from nasa import NasaClient, NasaError
from platform_settings import Settings

HEADERS = {"X-Earth-Request": "1"}
PASSWORD = "test-only-admin-password"
QUERY = dict(variable="lst_day", start="2024-07-01", end="2024-07-31", bbox=[23, 43.5, 28, 46.5])


@pytest.fixture(autouse=True)
def isolated_platform(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "settings", Settings(tmp_path / "platform-settings.json"))
    server.sessions.clear()
    server.jobs.clear()
    server.admin_sessions.clear()
    server.login_failures.clear()


def make_admin(client):
    result = client.post("/api/admin/setup", json={"password": PASSWORD}, headers=HEADERS)
    assert result.status_code == 200
    return result


def test_admin_setup_login_and_csrf():
    with TestClient(server.app) as client:
        assert client.get("/admin").status_code == 200
        assert client.get("/api/admin/session").json()["setup_required"]
        assert client.post("/api/admin/setup", json={"password": PASSWORD}).status_code == 403
        assert client.post("/api/admin/setup", json={"password": PASSWORD}, headers=HEADERS | {"Origin": "https://evil.example"}).status_code == 403
        assert client.post("/api/admin/setup", json={"password": "short"}, headers=HEADERS).status_code == 400
        result = make_admin(client)
        assert "httponly" in result.headers["set-cookie"].lower()
        assert PASSWORD not in server.settings.path.read_text()
        assert client.post("/api/admin/setup", json={"password": PASSWORD}, headers=HEADERS).status_code == 409
        assert client.get("/api/admin/overview").status_code == 200
        client.post("/api/admin/logout", headers=HEADERS)
        assert client.get("/api/admin/overview").status_code == 401
        assert client.post("/api/admin/login", json={"password": "wrong"}, headers=HEADERS).status_code == 401
        assert client.post("/api/admin/login", json={"password": PASSWORD}, headers=HEADERS).status_code == 200


def test_admin_routes_require_admin():
    with TestClient(server.app) as visitor:
        visitor.get("/api/config")
        for method, path, body in [("PUT", "/api/admin/nasa", {"token": "forbidden"}), ("PUT", "/api/admin/nasa/state", {"enabled": False}), ("POST", "/api/admin/nasa/verify", {}), ("DELETE", "/api/admin/nasa", {})]:
            assert visitor.request(method, path, json=body, headers=HEADERS).status_code == 401
        assert visitor.get("/api/admin/overview").status_code == 401
        assert visitor.post("/api/connect", json={"token": "legacy"}, headers=HEADERS).status_code in (404, 405)


def test_shared_token_and_private_visitor_jobs(monkeypatch):
    monkeypatch.setattr(NasaClient, "request", lambda *args, **kwargs: [])
    captured = []
    class CapturedThread:
        def __init__(self, target, args, **kwargs):
            captured.append(args)
        def start(self):
            pass
    # Replace only this module's reference, not Python's threading module.
    monkeypatch.setattr(server, "threading", SimpleNamespace(Thread=CapturedThread))
    with TestClient(server.app) as admin, TestClient(server.app) as a, TestClient(server.app) as b:
        make_admin(admin)
        saved = admin.put("/api/admin/nasa", json={"token": "shared-test-secret"}, headers=HEADERS)
        assert saved.status_code == 200 and saved.json()["available"]
        assert "shared-test-secret" not in saved.text
        for client in (a, b):
            response = client.get("/api/config")
            assert response.json()["provider"]["available"]
            assert "token" not in response.text and "expires_at" not in response.text
            assert client.get("/api/admin/overview").status_code == 401
        first = a.post("/api/jobs", json=QUERY, headers=HEADERS)
        second = b.post("/api/jobs", json=QUERY, headers=HEADERS)
        assert first.status_code == second.status_code == 202
        assert captured[0][2] == captured[1][2] == "shared-test-secret"
        assert a.get("/api/jobs/" + first.json()["id"]).status_code == 200
        assert b.get("/api/jobs/" + first.json()["id"]).status_code == 404
        assert len(a.get("/api/jobs").json()) == len(b.get("/api/jobs").json()) == 1
        overview = admin.get("/api/admin/overview")
        assert overview.json()["metrics"]["running"] == 2
        assert "shared-test-secret" not in overview.text


def test_rejected_replacement_preserves_token(monkeypatch):
    server.settings.create_admin(PASSWORD)
    server.settings.save_token("working-token", None)
    def reject(*args, **kwargs):
        raise NasaError("Rejected", authentication=True)
    monkeypatch.setattr(NasaClient, "request", reject)
    with TestClient(server.app) as client:
        client.post("/api/admin/login", json={"password": PASSWORD}, headers=HEADERS)
        assert client.put("/api/admin/nasa", json={"token": "bad-token"}, headers=HEADERS).status_code == 502
        assert server.settings.token() == "working-token"
        assert server.settings.status()["available"]
        assert client.post("/api/admin/nasa/verify", headers=HEADERS).status_code == 502
        assert server.settings.status()["state"] == "attention"


def test_persistence_expiry_pause_and_removal(monkeypatch):
    monkeypatch.setattr(NasaClient, "request", lambda *args, **kwargs: [])
    with TestClient(server.app) as admin, TestClient(server.app) as visitor:
        make_admin(admin)
        visitor.get("/api/config")
        assert visitor.post("/api/jobs", json=QUERY, headers=HEADERS).status_code == 503
        admin.put("/api/admin/nasa", json={"token": "persistent-token"}, headers=HEADERS)
        restored = Settings(server.settings.path)
        assert restored.authenticate(PASSWORD)
        assert restored.token() == "persistent-token"
        assert (server.settings.path.stat().st_mode & 0o777) == 0o600
        assert admin.put("/api/admin/nasa/state", json={"enabled": False}, headers=HEADERS).json()["state"] == "paused"
        assert visitor.post("/api/jobs", json=QUERY, headers=HEADERS).status_code == 503
        admin.put("/api/admin/nasa/state", json={"enabled": True}, headers=HEADERS)
        assert visitor.get("/api/config").json()["provider"]["available"]
        assert admin.put("/api/admin/nasa", json={"token": "new", "expires_at": "2000-01-01T00:00:00Z"}, headers=HEADERS).status_code == 400
        server.settings.update_connection(expires_at="2000-01-01T00:00:00+00:00")
        assert visitor.get("/api/config").json()["provider"]["state"] == "expired"
        assert admin.delete("/api/admin/nasa", headers=HEADERS).json()["state"] == "unconfigured"
        assert not server.settings.token()


def test_old_failure_does_not_invalidate_replacement():
    server.settings.save_token("replacement-token", None)
    server.settings.mark_invalid("old-token")
    assert server.settings.status()["available"]


def test_login_limit_and_secret_redaction():
    server.settings.create_admin(PASSWORD)
    with TestClient(server.app) as client:
        for _ in range(10):
            assert client.post("/api/admin/login", json={"password": "wrong"}, headers=HEADERS).status_code == 401
        assert client.post("/api/admin/login", json={"password": PASSWORD}, headers=HEADERS).status_code == 429
        response = client.put("/api/admin/nasa", json={"token": {"secret": "should-not-echo"}}, headers=HEADERS)
        assert response.status_code == 422
        assert "should-not-echo" not in response.text
