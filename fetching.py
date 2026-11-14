"""Saved grid cells and the fetching that fills them: the answer behind GET /api/series."""
import math
import re
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import closing
from datetime import date
from pathlib import Path

import numpy as np
from fastapi import HTTPException

import series
import sources
from nasa import NasaError

MAX_UNSAVED_CELLS = 800        # per request: about two minutes of Giovanni calls
MAX_FETCHES = 2                # fetches running at once
DAILY_GIOVANNI_CALLS = 20_000  # the public link must not spend the NASA login without limit
GIOVANNI_WORKERS = 24          # about six calls a second, the rate measured as safe
WAIT_SECONDS = 20              # a request waits this long, then answers 202
MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
BOX_HELP = "A box is west,south,east,north in degrees."


class Store:
    """One row per variable and grid cell, holding that cell's whole monthly record."""

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._run("CREATE TABLE IF NOT EXISTS cell (variable TEXT, lat INTEGER, lon INTEGER, first TEXT, vals BLOB, PRIMARY KEY (variable, lat, lon))")

    def _run(self, sql, args=()):
        with closing(sqlite3.connect(self.path, timeout=30)) as db, db:
            return db.execute(sql, args).fetchall()

    @staticmethod
    def _key(cell):  # whole numbers of 1/10000 degree, so lookups never compare decimals
        return round(cell[0] * 10000), round(cell[1] * 10000)

    def get(self, variable, cells):
        wanted = {self._key(cell): cell for cell in cells}
        if not wanted:
            return {}
        lats, lons = [key[0] for key in wanted], [key[1] for key in wanted]
        rows = self._run("SELECT lat, lon, first, vals FROM cell WHERE variable = ? AND lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?",
                         (variable, min(lats), max(lats), min(lons), max(lons)))
        return {wanted[(lat, lon)]: (first, np.frombuffer(vals, dtype="float64")) for lat, lon, first, vals in rows if (lat, lon) in wanted}

    def put(self, variable, cell, first, values):
        self._run("INSERT OR REPLACE INTO cell VALUES (?, ?, ?, ?, ?)", (variable, *self._key(cell), first, np.asarray(values, dtype="float64").tobytes()))


class _Fetch:
    def __init__(self, variable):
        self.variable, self.error = variable, None


store = Store(Path(__file__).resolve().parent / "data" / "series.sqlite")
_changed = threading.Condition()   # guards the three structures below; notified when a cell lands
_inflight = {}                     # (variable id, cell) -> the fetch that is getting it
_active = set()                    # fetches still running
_spent = {"day": None, "calls": 0}
_pool = ThreadPoolExecutor(GIOVANNI_WORKERS)


def reset():
    with _changed:
        _inflight.clear()
        _active.clear()
        _spent.update(day=None, calls=0)
        _changed.notify_all()


def _fetch(fetch, variable, cells, saving):
    """Runs in its own thread: get the cells from NASA and save each one as it arrives."""
    vid = variable["id"]

    def landed(batch):
        with _changed:
            for cell in batch:
                _inflight.pop((vid, cell), None)
            _changed.notify_all()

    try:
        if variable["source"] == "NASA POWER":   # one call returns every cell of the variable
            found = sources.power_cells(vid.removeprefix("power:"))
            for cell, (first, values) in found.items():
                saving.put(vid, cell, first, values)
            for cell in cells:
                if cell not in found:
                    saving.put(vid, cell, variable["first_month"], np.array([]))
        else:
            calls = {_pool.submit(sources.giovanni_cell, variable, *cell): cell for cell in cells}
            for call in as_completed(calls):
                try:
                    _, first, values = call.result()
                    saving.put(vid, calls[call], first or variable["first_month"], values)   # an empty record counts as saved
                except NasaError as exc:
                    fetch.error = fetch.error or str(exc)
                except Exception:
                    fetch.error = fetch.error or "The series could not be fetched. Please try again."
                landed([calls[call]])
    except NasaError as exc:
        fetch.error = str(exc)
    except Exception:
        fetch.error = "The series could not be fetched. Please try again."
    finally:
        with _changed:
            _active.discard(fetch)
        landed(cells)


def _start(variable, cells):
    """Called with _changed held: refuse or start a fetch for cells nobody is getting yet."""
    if variable["source"] != "NASA POWER":   # POWER needs no login and is one call, so it is not rationed
        if not sources.has_login():
            raise HTTPException(503, "This series is not saved yet and the server has no NASA login.")
        if len(cells) > MAX_UNSAVED_CELLS:
            raise HTTPException(422, f"This box needs {len(cells)} grid cells that are not saved yet. Draw a smaller box.")
        if _spent["day"] != date.today():
            _spent.update(day=date.today(), calls=0)
        if _spent["calls"] + len(cells) > DAILY_GIOVANNI_CALLS:
            raise HTTPException(429, "Today's NASA budget is used up. Saved places still work.")
    if len(_active) >= MAX_FETCHES and not any(fetch.variable == variable["id"] for fetch in _active):
        raise HTTPException(429, "Two fetches are already running. Try again in a minute.")
    if variable["source"] != "NASA POWER":
        _spent["calls"] += len(cells)
    fetch = _Fetch(variable["id"])
    _active.add(fetch)
    for cell in cells:
        _inflight[(variable["id"], cell)] = fetch
    threading.Thread(target=_fetch, args=(fetch, variable, cells, store), daemon=True).start()
    return fetch


def _box(text):
    try:
        west, south, east, north = (float(part) for part in text.split(","))
    except ValueError:
        raise HTTPException(400, BOX_HELP) from None
    if not all(map(math.isfinite, (west, south, east, north))) or not (west < east and south < north):
        raise HTTPException(400, BOX_HELP)
    return west, south, east, north


def _digits(value):
    return None if value is None else float(f"{value:.9g}")


def answer(variable, bbox, place, start, end):
    """(HTTP status, body) for one series request; raises HTTPException with a plain sentence."""
    if variable not in series.VARIABLES:
        raise HTTPException(404, "Unknown variable.")
    variable = series.VARIABLES[variable]
    vid, grid, saving = variable["id"], variable["grid"], store
    if (bbox is None) == (place is None):
        raise HTTPException(400, "Give either a box or a place.")
    box = _box(bbox) if bbox is not None else None
    if any(month is not None and not MONTH.match(month) for month in (start, end)):
        raise HTTPException(400, "Months are written YYYY-MM.")
    if start and end and start > end:
        raise HTTPException(400, "The start month is after the end month.")
    if place is not None:
        town = series.place(place)
        if town is None:
            raise HTTPException(404, "Unknown place.")
        cells = [series.cell_of(grid, town["lat"], town["lon"])]
        location = {"type": "place", "bbox": None, "name": town["name"], "county": town["county"]}
    else:
        box = series.clip(box)
        if box is None:
            raise HTTPException(422, "This box is outside Romania. Time series cover Romania only.")
        cells = series.cells_in(grid, box)
        location = {"type": "bbox", "bbox": list(box), "name": None, "county": None}

    saved = saving.get(vid, cells)
    from_saved = len(saved) == len(cells)
    if not from_saved:
        with _changed:
            saved = saving.get(vid, cells)   # read again under the lock: a fetch may have just finished
            watched = {_inflight[(vid, cell)] for cell in cells if (vid, cell) in _inflight}
            unclaimed = [cell for cell in cells if cell not in saved and (vid, cell) not in _inflight]
            if unclaimed:
                watched.add(_start(variable, unclaimed))
            deadline = time.monotonic() + WAIT_SECONDS
            while any((vid, cell) in _inflight for cell in cells) and time.monotonic() < deadline:
                _changed.wait(max(deadline - time.monotonic(), 0))
        saved = saving.get(vid, cells)
        if len(saved) < len(cells):
            if not any((vid, cell) in _inflight for cell in cells):   # everything landed, so what is missing failed
                raise HTTPException(502, next((fetch.error for fetch in watched if fetch.error), "The series could not be fetched. Please try again."))
            return 202, {"status": "fetching", "cells_total": len(cells), "cells_saved": len(saved)}

    months, values, used = series.window(*series.average(saved), start, end)
    trend, reason = series.trend(months, values)
    if trend:
        trend = {**trend, **{key: _digits(trend[key]) for key in ("slope_per_decade", "p_value", "mann_kendall_p")},
                 "ci95": [_digits(v) for v in trend["ci95"]], "line": [[year, _digits(v)] for year, v in trend["line"]]}
    return 200, {
        "variable": series.public_variable(variable),
        "location": {**location, "grid": series.grid_label(grid), "cells": len(cells)},
        "period": {"start": months[0] if months else start, "end": months[-1] if months else end},
        "points": [{"month": month, "value": _digits(value), "cells_used": count} for month, value, count in zip(months, values, used)],
        "yearly": [{"year": year, "value": _digits(value)} for year, value in series.yearly(months, values)],
        "trend": trend, "trend_reason": reason, "from_saved": from_saved,
    }
