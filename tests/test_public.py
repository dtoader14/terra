"""The NASA login taken from the environment, and public mode. No real login or NASA call is used."""
import re
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app as server
import sources
from platform_settings import Settings

HEADERS = {"X-Earth-Request": "1"}
QUERY = dict(variable="lst_day", start="2024-07-01", end="2024-07-31", bbox=[23, 43.5, 28, 46.5])


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "settings", Settings(tmp_path / "platform-settings.json"))
    monkeypatch.setattr(server, "PUBLIC", False)
    monkeypatch.setattr(sources, "has_login", lambda: False)
    server.sessions.clear(); server.jobs.clear(); server.admin_sessions.clear()


def with_login(monkeypatch, started):
    monkeypatch.setattr(sources, "has_login", lambda: True)
    monkeypatch.setattr(sources, "appeears_token", lambda: "env-secret-token")
    monkeypatch.setattr(server, "threading", SimpleNamespace(Thread=lambda target, args, **kwargs: SimpleNamespace(start=lambda: started.append(args))))


def test_env_login_serves_heat_maps_without_admin(monkeypatch):
    started = []
    with_login(monkeypatch, started)
    server.settings.save_token("admin-saved-token", None)
    with TestClient(server.app) as client:
        config = client.get("/api/config")
        assert config.json()["provider"]["available"] and config.json()["series_login"] is True and config.json()["public"] is False
        assert "env-secret-token" not in config.text and "admin-saved-token" not in config.text
        assert client.post("/api/jobs", json=QUERY, headers=HEADERS).status_code == 202
        assert started[0][2] == "env-secret-token"


def test_without_login_terra_is_unchanged():
    with TestClient(server.app) as client:
        config = client.get("/api/config").json()
        assert config["provider"]["state"] == "unconfigured" and config["series_login"] is False
        assert client.post("/api/jobs", json=QUERY, headers=HEADERS).status_code == 503
        assert client.get("/admin").status_code == 200 and client.get("/api/admin/session").status_code == 200


def test_public_mode_switches_admin_off_and_keeps_heat_maps(monkeypatch):
    started = []
    with_login(monkeypatch, started)
    monkeypatch.setattr(server, "PUBLIC", True)
    with TestClient(server.app) as client:
        assert client.get("/api/config").json()["public"] is True
        for method, path in [("GET", "/admin"), ("GET", "/api/admin/session"), ("POST", "/api/admin/setup"), ("PUT", "/api/admin/nasa")]:
            assert client.request(method, path, json={"password": "x" * 12}, headers=HEADERS).status_code == 404
        assert client.post("/api/jobs", json=QUERY, headers=HEADERS).status_code == 202 and len(started) == 1
        assert client.get("/api/health").json() == {"status": "ok"}


def test_allowed_hosts():
    assert server.allowed_hosts("") == ["localhost", "127.0.0.1", "testserver"]
    assert server.allowed_hosts("terra.onrender.com, example.org ,") == ["localhost", "127.0.0.1", "testserver", "terra.onrender.com", "example.org"]


def test_public_mode_believes_the_https_proxy(monkeypatch):
    """A host's HTTPS proxy talks plain http to the server; heat maps must still pass the same-origin check."""
    started = []
    with_login(monkeypatch, started)
    monkeypatch.setattr(server, "PUBLIC", True)
    behind_proxy = HEADERS | {"Origin": "https://testserver", "X-Forwarded-Proto": "https"}
    with TestClient(server.app) as client:
        client.get("/api/config")
        assert client.post("/api/jobs", json=QUERY, headers=behind_proxy).status_code == 202
        assert client.post("/api/jobs", json=QUERY, headers=behind_proxy | {"Origin": "https://evil.example"}).status_code == 403
    with TestClient(server.app) as client:
        cookie = client.get("/api/config", headers={"X-Forwarded-Proto": "https"}).headers["set-cookie"].lower()
        assert "secure" in cookie                                            # the visitor cookie is HTTPS-only there
    monkeypatch.setattr(server, "PUBLIC", False)                             # on a laptop that header is not believed
    with TestClient(server.app) as client:
        client.get("/api/config")
        assert client.post("/api/jobs", json=QUERY, headers=behind_proxy).status_code == 403


def test_the_page_names_every_source_and_keeps_its_own_status_line():
    page = (server.ROOT / "static" / "index.html").read_text()
    assert "through AppEEARS only" not in page                       # no longer true once time series exist
    assert "Giovanni" in page and "POWER" in page and "GeoNames" in page
    assert 'id="series-status-title"' in page and 'id="series-status-message"' in page


LEAKED = bool(__import__("os").environ.get("EARTHDATA_PASSWORD"))        # read once, as a bare yes or no, when tests are collected


def test_the_real_login_never_reaches_the_test_run():
    assert LEAKED is False                                               # importing app loads .env; conftest must stop it first


def test_the_page_asks_for_place_then_period_then_variable_and_is_about_romania_only():
    page = (server.ROOT / "static" / "index.html").read_text()
    script = (server.ROOT / "static" / "app.js").read_text()
    assert page.index("Set your place") < page.index("Pick a time period") < page.index("Choose a variable")
    assert "Choose a region, variable" not in page   # the status line names them in the new order too
    assert 'id="region"' not in page and 'id="place-search"' in page
    assert page.index('id="place-search"') < page.index('id="draw-button"') < page.index('id="west"')
    for elsewhere in ("California", "Amazon", "Alps", "california", "amazon", "alps"):
        assert elsewhere not in page and elsewhere not in script


def test_every_element_the_page_scripts_look_up_is_on_the_page():
    page = (server.ROOT / "static" / "index.html").read_text()
    for name in ("app.js", "series.js"):
        script = (server.ROOT / "static" / name).read_text()
        for element in sorted(set(re.findall(r"\$\('([\w-]+)'\)", script) + re.findall(r"\['west', 'south', 'east', 'north'(?:, '([\w-]+)')+\]", script))):
            assert f'id="{element}"' in page, f"{name} looks up #{element}, which is not on the page"
