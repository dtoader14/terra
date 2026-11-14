import os

import pytest

# app.py loads .env when it is imported. Set both names first (dotenv never overrides a name that is
# already set), so a real NASA login is never even present in the test run.
os.environ["EARTHDATA_USERNAME"] = os.environ["EARTHDATA_PASSWORD"] = ""


@pytest.fixture(autouse=True)
def no_real_login(monkeypatch):
    monkeypatch.delenv("EARTHDATA_USERNAME", raising=False)
    monkeypatch.delenv("EARTHDATA_PASSWORD", raising=False)
