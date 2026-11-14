"""NASA is never called here: every request goes to a mock transport. The fixture is one real Giovanni answer."""
import base64
from pathlib import Path

import httpx
import numpy as np
import pytest

import series
import sources
from nasa import NasaError

FIXTURE = (Path(__file__).parent / "fixtures" / "giovanni_airs_alba_iulia.csv").read_text()
AIR = series.VARIABLES["gio:AIRS3STM_7_0_SurfAirTemp_A"]
BASIC = "Basic " + base64.b64encode(b"u:p").decode()
REAL_CLIENT = httpx.Client                                    # serve() may run twice in one test


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    monkeypatch.setenv("EARTHDATA_USERNAME", "u")
    monkeypatch.setenv("EARTHDATA_PASSWORD", "p")
    monkeypatch.setattr(sources.time, "sleep", lambda _: None)
    sources.forget("earthdata"); sources.forget("appeears")


def serve(monkeypatch, handler):
    calls = []
    def record(request):
        calls.append(request)
        return handler(request, calls)
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: REAL_CLIENT(transport=httpx.MockTransport(record), **kwargs))
    return calls


def nasa(request, calls, timeseries=lambda n: httpx.Response(200, text=FIXTURE)):
    if request.url.host == "urs.earthdata.nasa.gov":
        return httpx.Response(200, json={"access_token": f"edl-{len(calls)}", "token_type": "Bearer", "expiration_date": "01/12/2027"})
    if request.url.path == "/api/login":
        return httpx.Response(200, json={"token_type": "Bearer", "token": "app-1", "expiration": "2026-11-15T09:23:48Z"})
    return timeseries(sum(c.url.path == "/timeseries" for c in calls))


def test_parse_real_giovanni_answer():
    cell, first, values = sources.parse_giovanni(FIXTURE)
    assert cell == (46.5, 23.5) and first == "2002-09" and len(values) == 290
    assert values[0] == 292.164 and values[-1] == 286.992 and not np.isnan(values).any()
    assert np.isnan(sources.parse_giovanni(FIXTURE.replace("292.164", "-9999"))[2][0])           # the file's undef value
    gap = sources.parse_giovanni(FIXTURE.replace("2002-10-01 00:00,286.07\n", ""))[2]           # a month NASA left out
    assert len(gap) == 290 and np.isnan(gap[1])
    assert len(sources.parse_giovanni(FIXTURE.split("Timestamp")[0])[2]) == 0                    # header only: an empty cell


def test_logins_are_cached_and_use_basic_auth(monkeypatch):
    calls = serve(monkeypatch, nasa)
    assert sources.has_login()
    assert sources.earthdata_token() == "edl-1" == sources.earthdata_token() and len(calls) == 1
    assert calls[0].method == "POST" and str(calls[0].url) == "https://urs.earthdata.nasa.gov/api/users/find_or_create_token"
    assert calls[0].headers["Authorization"] == BASIC
    assert sources.appeears_token() == "app-1" == sources.appeears_token() and len(calls) == 2
    assert calls[1].url.host == "appeears.earthdatacloud.nasa.gov" and calls[1].headers["Authorization"] == BASIC
    sources.forget("earthdata")
    assert sources.earthdata_token() == "edl-3"


def test_appeears_token_is_renewed_after_twelve_hours(monkeypatch):
    calls = serve(monkeypatch, nasa)
    clock = [1000.0]
    monkeypatch.setattr(sources, "now", lambda: clock[0])
    sources.appeears_token(); clock[0] += 12 * 3600 - 1; sources.appeears_token()
    assert len(calls) == 1
    clock[0] += 2; sources.appeears_token()
    assert len(calls) == 2


def test_no_login_and_refused_login(monkeypatch):
    monkeypatch.setenv("EARTHDATA_USERNAME", "name-xyz")
    monkeypatch.setenv("EARTHDATA_PASSWORD", "word-xyz")
    serve(monkeypatch, lambda request, calls: httpx.Response(401, json={"error": "invalid_credentials"}))
    with pytest.raises(NasaError) as refused:
        sources.earthdata_token()
    assert refused.value.authentication and "xyz" not in str(refused.value)
    monkeypatch.delenv("EARTHDATA_PASSWORD")
    assert not sources.has_login()


def test_giovanni_cell_sends_the_right_request(monkeypatch):
    calls = serve(monkeypatch, nasa)
    cell, first, values = sources.giovanni_cell(AIR, 46.5, 23.5)
    assert cell == (46.5, 23.5) and first == "2002-09" and len(values) == 290
    request = calls[-1]
    assert str(request.url).startswith("https://api.giovanni.earthdata.nasa.gov/timeseries?")
    assert dict(request.url.params) == {"data": "AIRS3STM_7_0_SurfAirTemp_A", "location": "[46.5,23.5]",
                                        "time": f"{AIR['begin']}T00:00:00/{AIR['end']}T23:59:59"}
    assert request.headers["Authorization"] == "Bearer edl-1"


@pytest.mark.parametrize("refusal", [dict(status_code=401), dict(status_code=403),
                                     dict(status_code=500, headers={"x-amzn-errortype": "AuthorizerConfigurationException"})])
def test_refused_token_logs_in_again_once(monkeypatch, refusal):
    calls = serve(monkeypatch, lambda r, c: nasa(r, c, lambda n: httpx.Response(**refusal) if n == 1 else httpx.Response(200, text=FIXTURE)))
    assert len(sources.giovanni_cell(AIR, 46.5, 23.5)[2]) == 290
    assert [c.url.host.split(".")[0] for c in calls] == ["urs", "api", "urs", "api"]
    sources.forget("earthdata")
    serve(monkeypatch, lambda r, c: nasa(r, c, lambda n: httpx.Response(**refusal)))
    with pytest.raises(NasaError) as error:
        sources.giovanni_cell(AIR, 46.5, 23.5)
    assert error.value.authentication


def test_giovanni_retries_trouble_then_gives_up(monkeypatch):
    calls = serve(monkeypatch, lambda r, c: nasa(r, c, lambda n: httpx.Response(503) if n < 3 else httpx.Response(200, text=FIXTURE)))
    assert len(sources.giovanni_cell(AIR, 46.5, 23.5)[2]) == 290
    serve(monkeypatch, lambda r, c: nasa(r, c, lambda n: httpx.Response(503)))
    with pytest.raises(NasaError, match="Giovanni"):
        sources.giovanni_cell(AIR, 46.5, 23.5)


def test_power_cells(monkeypatch):
    feature = {"geometry": {"coordinates": [23.75, 46.0, 300.0]},
               "properties": {"parameter": {"T2MDEW": {"198101": -5.97, "198102": -999.0, "198113": 4.0, "198201": 1.5}}}}
    calls = serve(monkeypatch, lambda request, calls: httpx.Response(200, json={"features": [feature]}))
    first, values = sources.power_cells("T2MDEW")[(46.0, 23.75)]
    assert first == "1981-01" and len(values) == 13 and values[0] == -5.97 and np.isnan(values[1]) and values[12] == 1.5
    assert dict(calls[0].url.params) == {"latitude-min": "43.25", "latitude-max": "48.75", "longitude-min": "20.0", "longitude-max": "30.0",
                                         "parameters": "T2MDEW", "community": "AG", "start": "1981", "end": "2025", "format": "JSON"}
    assert calls[0].url.host == "power.larc.nasa.gov" and "Authorization" not in calls[0].headers
    serve(monkeypatch, lambda request, calls: httpx.Response(422, json={"messages": ["bad"]}))
    with pytest.raises(NasaError, match="POWER"):
        sources.power_cells("T2MDEW")
