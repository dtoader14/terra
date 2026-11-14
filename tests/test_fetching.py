"""NASA is never called here: sources.giovanni_cell and sources.power_cells are replaced by stand-ins."""
import threading
import time
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

import app as server
import fetching
import series
import sources
from nasa import NasaError

FIXTURE = (Path(__file__).parent / "fixtures" / "giovanni_airs_alba_iulia.csv").read_text()
AIR, RAIN, DEW = "gio:AIRS3STM_7_0_SurfAirTemp_A", "gio:GPM_3IMERGM_07_precipitation", "power:T2MDEW"
AIR_NIGHT = "gio:AIRS3STM_7_0_SurfAirTemp_D"
BOX = "22.2,45.2,24.8,46.8"                                # six 1 degree cells: lat 45.5, 46.5 x lon 22.5, 23.5, 24.5
ALBA = series.find_places("Alba Iulia")[0]["id"]


def rising(variable, lat, lon):                            # 20 full years; yearly mean rises 0.12 a year
    return (lat, lon), "2000-01", lat + 0.01 * np.arange(240, dtype="float64")


@pytest.fixture
def nasa(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(fetching, "store", fetching.Store(tmp_path / "series.sqlite"))
    monkeypatch.setattr(sources, "has_login", lambda: True)
    monkeypatch.setattr(sources, "giovanni_cell", lambda v, lat, lon: (calls.append((v["id"], lat, lon)), rising(v, lat, lon))[1])
    fetching.reset()
    return calls


def get(**params):
    with TestClient(server.app) as client:
        return client.get("/api/series", params=params)


def test_box_is_fetched_once_averaged_and_trended(nasa):
    first = get(variable=AIR, bbox=BOX).json()
    assert len(nasa) == 6 and first["from_saved"] is False
    again = get(variable=AIR, bbox=BOX, start="2001-01", end="2019-12")
    body = again.json()
    assert again.status_code == 200 and len(nasa) == 6 and body["from_saved"] is True
    assert body["location"] == {"type": "bbox", "bbox": [22.2, 45.2, 24.8, 46.8], "name": None, "county": None, "grid": "1 x 1 degree", "cells": 6}
    assert body["period"] == {"start": "2001-01", "end": "2019-12"} and len(body["points"]) == 228
    assert body["points"][0]["month"] == "2001-01" and body["points"][0]["cells_used"] == 6
    assert 45.5 < body["points"][0]["value"] - 0.12 < 46.5              # between the two latitudes, plus 12 months of rise
    assert body["trend"]["slope_per_decade"] == pytest.approx(1.2) and body["trend"]["years"] == 19 and body["trend"]["significant"]
    assert body["yearly"][0] == {"year": 2001, "value": pytest.approx(body["points"][0]["value"] + 0.055)}
    assert body["variable"]["id"] == AIR and body["variable"]["kind"] == "satellite" and body["variable"]["unit"] == "K"


def test_a_real_nasa_answer_round_trips(nasa, monkeypatch):
    monkeypatch.setattr(sources, "giovanni_cell", lambda v, lat, lon: sources.parse_giovanni(FIXTURE))
    body = get(variable=AIR, place=ALBA).json()
    rows = [line.split(",") for line in FIXTURE.split("Timestamp (UTC),Data\n")[1].splitlines()]
    assert [(p["month"], p["value"]) for p in body["points"]] == [(stamp[:7], float(value)) for stamp, value in rows]
    assert body["location"]["type"] == "place" and body["location"]["name"] == "Alba Iulia" and body["location"]["cells"] == 1
    assert body["period"] == {"start": "2002-09", "end": "2026-10"}


def test_power_needs_no_login_and_one_call(nasa, monkeypatch):
    boxes = []
    cells = {cell: ("1981-01", np.arange(540, dtype="float64")) for cell in series.cells_in(series.VARIABLES[DEW]["grid"], series.ROMANIA)}
    monkeypatch.setattr(sources, "has_login", lambda: False)
    monkeypatch.setattr(sources, "power_cells", lambda parameter: (boxes.append(parameter), cells)[1])
    assert get(variable=DEW, place=ALBA).status_code == 200 and get(variable=DEW, bbox=BOX).status_code == 200
    assert boxes == ["T2MDEW"] and nasa == []
    assert get(variable=AIR, place=ALBA).status_code == 503              # Giovanni, not saved, no login


def test_slow_fetch_answers_202_then_200(nasa, monkeypatch):
    monkeypatch.setattr(fetching, "WAIT_SECONDS", 0.05)
    monkeypatch.setattr(sources, "giovanni_cell", lambda v, lat, lon: (time.sleep(0.3), rising(v, lat, lon))[1])
    waiting = get(variable=AIR, bbox=BOX)
    assert waiting.status_code == 202 and waiting.json()["status"] == "fetching" and waiting.json()["cells_total"] == 6
    assert 0 <= waiting.json()["cells_saved"] < 6
    deadline = time.time() + 5
    while get(variable=AIR, bbox=BOX).status_code != 200:
        assert time.time() < deadline
        time.sleep(0.05)


def test_same_cells_asked_twice_at_once_are_fetched_once(nasa, monkeypatch):
    monkeypatch.setattr(sources, "giovanni_cell", lambda v, lat, lon: (nasa.append((lat, lon)), time.sleep(0.2), rising(v, lat, lon))[2])
    threads = [threading.Thread(target=get, kwargs=dict(variable=AIR, bbox=BOX)) for _ in range(2)]
    [t.start() for t in threads]; [t.join() for t in threads]
    assert sorted(nasa) == sorted(series.cells_in(series.VARIABLES[AIR]["grid"], (22.2, 45.2, 24.8, 46.8)))


def test_nasa_failing_for_one_cell(nasa, monkeypatch):
    def flaky(v, lat, lon):
        nasa.append((lat, lon))
        if (lat, lon) == (45.5, 22.5) and len(nasa) <= 6:
            raise NasaError("NASA Giovanni did not answer. Please try again shortly.")
        return rising(v, lat, lon)
    monkeypatch.setattr(sources, "giovanni_cell", flaky)
    failed = get(variable=AIR, bbox=BOX)
    assert failed.status_code == 502 and "Giovanni" in failed.json()["detail"]
    assert get(variable=AIR, bbox=BOX).status_code == 200 and nasa.count((45.5, 22.5)) == 2 and len(nasa) == 7


def test_empty_cells_are_saved_and_not_asked_again(nasa, monkeypatch):
    monkeypatch.setattr(sources, "giovanni_cell", lambda v, lat, lon: (nasa.append(1), ((lat, lon), "2000-01", np.array([])))[1])
    body = get(variable=AIR, bbox=BOX).json()
    assert body["points"] == [] and body["trend"] is None and body["trend_reason"] and len(nasa) == 6
    assert get(variable=AIR, bbox=BOX).json()["from_saved"] is True and len(nasa) == 6


def test_period_outside_the_record(nasa):
    body = get(variable=AIR, place=ALBA, start="2030-01", end="2031-12")
    assert body.status_code == 200 and body.json()["points"] == [] and body.json()["trend"] is None
    assert body.json()["trend_reason"] == "Fewer than 10 complete years in this period."


@pytest.mark.parametrize("params,status", [
    (dict(variable="gio:nope", place=ALBA), 404), (dict(variable=AIR, place="nope"), 404),
    (dict(variable=AIR), 400), (dict(variable=AIR, place=ALBA, bbox=BOX), 400),
    (dict(variable=AIR, bbox="1,2,3"), 400), (dict(variable=AIR, bbox="24,45,23,46"), 400), (dict(variable=AIR, bbox="a,b,c,d"), 400),
    (dict(variable=AIR, place=ALBA, start="2001-13"), 400), (dict(variable=AIR, place=ALBA, start="2005-01", end="2004-12"), 400),
    (dict(variable=AIR, bbox="-122.5,35,-119,39"), 422),                 # California: outside Romania
    (dict(variable=RAIN, bbox="20.2,43.6,29.7,48.3"), 422),              # 4,465 unsaved cells
])
def test_refusals(nasa, params, status):
    response = get(**params)
    assert response.status_code == status and isinstance(response.json()["detail"], str) and nasa == []


def test_daily_budget_and_two_at_a_time(nasa, monkeypatch):
    monkeypatch.setattr(fetching, "DAILY_GIOVANNI_CALLS", 5)
    assert get(variable=AIR, bbox=BOX).status_code == 429 and nasa == []
    monkeypatch.setattr(fetching, "DAILY_GIOVANNI_CALLS", 20_000)
    monkeypatch.setattr(fetching, "WAIT_SECONDS", 0.01)
    monkeypatch.setattr(sources, "giovanni_cell", lambda v, lat, lon: (time.sleep(0.5), rising(v, lat, lon))[1])
    assert get(variable=AIR, bbox=BOX).status_code == 202
    assert get(variable=AIR_NIGHT, bbox=BOX).status_code == 202
    assert get(variable="gio:AIRS3STM_7_0_RelHumSurf_A", bbox=BOX).status_code == 429
    deadline = time.time() + 5                                           # let both fetches finish before the next test
    while any(get(variable=v, bbox=BOX).status_code != 200 for v in (AIR, AIR_NIGHT)):
        assert time.time() < deadline
        time.sleep(0.05)


def test_lists():
    with TestClient(server.app) as client:
        variables = client.get("/api/series/variables").json()["variables"]
        assert len(variables) == 354 and {"id", "name", "kind", "grid", "unit"} <= set(variables[0])
        assert client.get("/api/places", params={"q": "alba iu"}).json()["places"][0]["name"] == "Alba Iulia"
        assert client.get("/api/places").json() == {"places": []}


SOIL, ROMANIA_BOX = "gio:GLDAS_NOAH025_M_2_1_SoilMoi0_10cm_inst", "20.2,43.6,29.7,48.3"    # 722 cells


def test_a_constant_variable_answers_without_a_trend(nasa, monkeypatch):
    monkeypatch.setattr(sources, "giovanni_cell", lambda v, lat, lon: ((lat, lon), "2000-01", np.zeros(240)))
    response = get(variable=AIR, place=ALBA)
    assert response.status_code == 200 and response.json()["trend"] is None
    assert response.json()["trend_reason"] == "The yearly means do not change in this period."


def test_a_failed_cell_is_retried_once_not_by_every_poll(nasa, monkeypatch):
    release, calls, failing = threading.Event(), [], (45.5, 22.5)
    def fetch(v, lat, lon):
        calls.append((lat, lon))
        if (lat, lon) == failing and calls.count(failing) == 1:
            raise NasaError("NASA Giovanni did not answer. Please try again shortly.")
        if (lat, lon) != failing:
            release.wait(5)                                              # the other five cells are slow
        return rising(v, lat, lon)
    monkeypatch.setattr(sources, "giovanni_cell", fetch)
    monkeypatch.setattr(fetching, "WAIT_SECONDS", 0.05)
    for _ in range(4):                                                   # the page asks again every few seconds
        assert get(variable=AIR, bbox=BOX).status_code == 202
    assert calls.count(failing) == 1                                     # no retry while that fetch is still running
    release.set()
    deadline = time.time() + 5
    while get(variable=AIR, bbox=BOX).status_code != 200:                # a 502 for the failed fetch, then one retry
        assert time.time() < deadline
        time.sleep(0.05)
    assert calls.count(failing) == 2 and len(calls) == 7


def test_a_refused_login_stops_the_fetch_and_is_remembered(nasa, monkeypatch):
    def refuse(v, lat, lon):
        nasa.append((lat, lon))
        threading.Event().wait(0.05)
        raise NasaError("NASA refused the login for Giovanni.", authentication=True)
    monkeypatch.setattr(sources, "giovanni_cell", refuse)
    first = get(variable=SOIL, bbox=ROMANIA_BOX)
    assert first.status_code == 503 and "login" in first.json()["detail"]
    assert len(nasa) < 200                                               # the other 700 cells were never sent to NASA
    asked = len(nasa)
    again = get(variable=SOIL, bbox=ROMANIA_BOX)
    assert again.status_code == 503 and len(nasa) == asked               # answered at once from memory


def test_a_finished_fetch_leaves_cells_a_newer_fetch_has_taken(nasa):
    old, new, cell = fetching._Fetch(AIR), fetching._Fetch(AIR), (45.5, 22.5)
    fetching._inflight[(AIR, cell)] = new
    fetching._landed(old, [cell])
    assert fetching._inflight[(AIR, cell)] is new
    fetching._landed(new, [cell])
    assert (AIR, cell) not in fetching._inflight


OLD = "gio:GLDAS_NOAH025_M_2_0_Tair_f_inst"                                 # GLDAS 2.0: 1948-01 to 2014-12


def variables(**params):
    with TestClient(server.app) as client:
        return client.get("/api/series/variables", params=params)


def test_variable_list_follows_the_period(nasa):
    old = variables(bbox=BOX, start="1950-01", end="1979-12").json()
    assert old["reason"] is None and len(old["variables"]) == 36
    assert all(v["id"].startswith("gio:GLDAS_NOAH025_M_2_0_") for v in old["variables"])
    assert {(v["available_from"], v["available_to"], v["full_years"]) for v in old["variables"]} == {("1950-01", "1979-12", 30)}
    recent = {v["id"]: v for v in variables(bbox=BOX, start="2021-01", end="2025-12").json()["variables"]}
    assert len(recent) == 317 and OLD not in recent and "gio:TRMM_3B43_7_precipitation" not in recent
    assert recent[AIR]["full_years"] == 5
    part = {v["id"]: v for v in variables(bbox=BOX, start="1990-01", end="2010-12").json()["variables"]}[AIR]
    assert (part["available_from"], part["available_to"], part["full_years"]) == ("2002-09", "2010-12", 8)
    everything = variables().json()
    assert len(everything["variables"]) == 354 and everything["variables"][0]["cells"] is None


def test_variable_list_follows_the_place(nasa):
    assert variables(bbox="-122.5,35,-119,39").json() == {"variables": [], "reason": "This box is outside Romania. Time series cover Romania only."}
    whole = {v["id"]: v for v in variables(bbox=ROMANIA_BOX).json()["variables"]}
    assert whole[RAIN]["cells"] == 4465 and whole[RAIN]["too_large"] is True     # 0.1 degree grid: over the 800-cell limit
    assert whole[AIR]["cells"] == 40 and whole[AIR]["too_large"] is False
    assert whole[DEW]["cells"] == 135 and whole[DEW]["too_large"] is False       # POWER is one call for any box
    town = {v["id"]: v for v in variables(place=ALBA).json()["variables"]}
    assert town[RAIN]["cells"] == 1 and town[RAIN]["too_large"] is False
    county = {v["id"]: v for v in variables(place="county:Cluj").json()["variables"]}
    assert county[AIR]["cells"] >= 1 and county[SOIL]["cells"] > 10
    assert variables(place="nope").status_code == 404 and variables(bbox="1,2,3").status_code == 400
    assert variables(bbox=BOX, place=ALBA).status_code == 400 and variables(start="2005-01", end="2004-12").status_code == 400


def test_a_big_box_stops_being_too_large_once_its_cells_are_saved(nasa, monkeypatch):
    monkeypatch.setattr(fetching, "MAX_UNSAVED_CELLS", 5)                       # BOX holds six 1 degree cells
    assert {v["id"]: v for v in variables(bbox=BOX).json()["variables"]}[AIR]["too_large"] is True
    monkeypatch.setattr(fetching, "MAX_UNSAVED_CELLS", 800)
    assert get(variable=AIR, bbox=BOX).status_code == 200
    monkeypatch.setattr(fetching, "MAX_UNSAVED_CELLS", 5)
    assert {v["id"]: v for v in variables(bbox=BOX).json()["variables"]}[AIR]["too_large"] is False


def test_a_county_is_a_place(nasa):
    body = get(variable=AIR, place="county:Cluj").json()
    assert body["location"]["type"] == "county" and body["location"]["name"] == "Cluj" and body["location"]["cells"] >= 1
    assert body["location"]["bbox"] == series.place("county:Cluj")["bbox"]
    with TestClient(server.app) as client:
        assert client.get("/api/places", params={"q": "cluj"}).json()["places"][0]["kind"] == "county"
