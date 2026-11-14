import pytest


@pytest.fixture(autouse=True)
def no_real_login(monkeypatch):
    """app.py loads .env at import; keep a real NASA login away from every test."""
    monkeypatch.delenv("EARTHDATA_USERNAME", raising=False)
    monkeypatch.delenv("EARTHDATA_PASSWORD", raising=False)
