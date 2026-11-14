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
REFUSAL_MEMORY = 120            # seconds a refused NASA login is remembered, so it is not sent again for every request
BOX_HELP = "A box is west,south,east,north in degrees."
FAILED = "The series could not be fetched. Please try again."


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

    def count(self, variable, cells):
        """How many of these cells are saved. The cells are a full rectangle of the grid, so a range count is exact."""
        keys = [self._key(cell) for cell in cells]
        lats, lons = [key[0] for key in keys], [key[1] for key in keys]
        return self._run("SELECT COUNT(*) FROM cell WHERE variable = ? AND lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?",
                         (variable, min(lats), max(lats), min(lons), max(lons)))[0][0]

    def put(self, variable, cell, first, values):
        self._run("INSERT OR REPLACE INTO cell VALUES (?, ?, ?, ?, ?)", (variable, *self._key(cell), first, np.asarray(values, dtype="float64").tobytes()))


class _Fetch:
    def __init__(self, variable):
        self.variable, self.error, self.refused = variable, None, False  # refused: NASA turned the login down


store = Store(Path(__file__).resolve().parent / "data" / "series.sqlite")
_changed = threading.Condition()   # guards the three structures below; notified when a cell lands
_inflight = {}                     # (variable id, cell) -> the fetch that is getting it
_active = set()                    # fetches still running
_spent = {"day": None, "calls": 0}
_refused = {"at": None, "message": ""}   # the last time NASA turned the login down
_pool = ThreadPoolExecutor(GIOVANNI_WORKERS)


def reset():
    with _changed:
        _inflight.clear()
        _active.clear()
        _spent.update(day=None, calls=0)
        _refused.update(at=None, message="")
        _changed.notify_all()


def _landed(fetch, cells):
    """Un-mark cells this fetch was getting. A cell that a newer fetch has taken over stays marked."""
    with _changed:
        for cell in cells:
            if _inflight.get((fetch.variable, cell)) is fetch:
                del _inflight[(fetch.variable, cell)]
        _changed.notify_all()


def _failed(fetch, exc):
    fetch.error = fetch.error or (str(exc) if isinstance(exc, NasaError) else FAILED)
    fetch.refused = fetch.refused or bool(getattr(exc, "authentication", False))


def _fetch(fetch, variable, cells, saving):
    """Runs in its own thread: get the cells from NASA and save each one as it arrives."""
    vid = variable["id"]
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
                if not call.cancelled():
                    try:
                        _, first, values = call.result()
                        saving.put(vid, calls[call], first or variable["first_month"], values)   # an empty record counts as saved
                    except Exception as exc:
                        _failed(fetch, exc)
                        if fetch.refused:   # NASA turned the login down: do not send it again for every other cell
                            for other in calls:
                                other.cancel()
                _landed(fetch, [calls[call]])
    except Exception as exc:
        _failed(fetch, exc)
    finally:
        with _changed:   # one step: nobody may see this fetch gone while its cells still look taken
            _active.discard(fetch)
            if fetch.refused:
                _refused.update(at=time.monotonic(), message=fetch.error)
        _landed(fetch, cells)


def _start(variable, cells):
    """Called with _changed held: refuse or start a fetch for cells nobody is getting yet."""
    if variable["source"] != "NASA POWER":   # POWER needs no login and is one call, so it is not rationed
        if not sources.has_login():
            raise HTTPException(503, "This series is not saved yet and the server has no NASA login.")
        if _refused["at"] is not None and time.monotonic() - _refused["at"] < REFUSAL_MEMORY:
            raise HTTPException(503, _refused["message"])
        if len(cells) > MAX_UNSAVED_CELLS:
            raise HTTPException(422, f"This box needs {len(cells)} grid cells that are not saved yet. Draw a smaller box.")
        if _spent["day"] != date.today():
            _spent.update(day=date.today(), calls=0)
        if _spent["calls"] + len(cells) > DAILY_GIOVANNI_CALLS:
            raise HTTPException(429, "Today's NASA budget is used up. Saved places still work.")
    if len(_active) >= MAX_FETCHES:
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
    return None if value is None or not math.isfinite(value) else float(f"{value:.9g}")


def _period(start, end):
    if any(month is not None and not MONTH.match(month) for month in (start, end)):
        raise HTTPException(400, "Months are written YYYY-MM.")
    if start and end and start > end:
        raise HTTPException(400, "The start month is after the end month.")


def _where(bbox, place):
    """What was asked for: (the answer's location, the box to average or None, the town or None)."""
    if place is not None:
        found = series.place(place)
        if found is None:
            raise HTTPException(404, "Unknown place.")
        if found["kind"] == "town":   # a town is the one grid cell that contains it
            return {"type": "place", "bbox": None, "name": found["name"], "county": found["county"]}, None, found
        box, location = tuple(found["bbox"]), {"type": "county", "name": found["name"], "county": found["county"]}
    else:
        box, location = _box(bbox), {"type": "bbox", "name": None, "county": None}
    box = series.clip(box)
    if box is None:
        raise HTTPException(422, "This box is outside Romania. Time series cover Romania only.")
    return {**location, "bbox": list(box)}, box, None


def _cells(grid, box, town):
    return [series.cell_of(grid, town["lat"], town["lon"])] if town else series.cells_in(grid, box)


def _full_years(first, last):
    """Calendar years that lie wholly between two months, both included."""
    return max(0, (int(last[:4]) - (last[5:] != "12")) - (int(first[:4]) + (first[5:] != "01")) + 1)


def available(bbox, place, start, end):
    """The variables that have something to show for this place and period, with what each can offer."""
    if bbox is not None and place is not None:
        raise HTTPException(400, "Give either a box or a place.")
    _period(start, end)
    box = town = None
    if bbox is not None or place is not None:
        try:
            _, box, town = _where(bbox, place)
        except HTTPException as outside:
            if outside.status_code != 422:
                raise
            return {"variables": [], "reason": outside.detail}
    listed, cells_on = [], {}
    for variable in series.VARIABLES.values():
        first, last = max(start or "", variable["first_month"]), min(end or "9999-12", variable["last_month"])
        if first > last:   # the record and the period do not overlap
            continue
        entry = {**series.public_variable(variable), "available_from": first, "available_to": last,
                 "full_years": _full_years(first, last), "cells": None, "too_large": False}
        if box or town:
            grid = variable["grid"]
            cells = cells_on[grid] = cells_on.get(grid) or _cells(grid, box, town)
            entry["cells"] = len(cells)
            if variable["source"] != "NASA POWER" and len(cells) > MAX_UNSAVED_CELLS:   # the same limit a fetch would hit
                entry["too_large"] = len(cells) - store.count(variable["id"], cells) > MAX_UNSAVED_CELLS
        listed.append(entry)
    return {"variables": listed, "reason": None}


def answer(variable, bbox, place, start, end):
    """(HTTP status, body) for one series request; raises HTTPException with a plain sentence."""
    if variable not in series.VARIABLES:
        raise HTTPException(404, "Unknown variable.")
    variable = series.VARIABLES[variable]
    vid, grid, saving = variable["id"], variable["grid"], store
    if (bbox is None) == (place is None):
        raise HTTPException(400, "Give either a box or a place.")
    if bbox is not None:
        _box(bbox)   # a malformed box is reported before anything else about the request
    _period(start, end)
    location, box, town = _where(bbox, place)
    cells = _cells(grid, box, town)

    saved = saving.get(vid, cells)
    from_saved = len(saved) == len(cells)
    if not from_saved:
        with _changed:
            # One fetch per variable at a time. While one runs, a request only waits: asking again every few
            # seconds must not start a second fetch for cells that have just failed.
            watched = {_inflight[(vid, cell)] for cell in cells if (vid, cell) in _inflight}
            started, checked, deadline = False, False, time.monotonic() + WAIT_SECONDS
            while True:
                busy = any(fetch.variable == vid for fetch in _active)
                mine_landed = not any((vid, cell) in _inflight for cell in cells)
                if not busy or (mine_landed and (started or watched) and not checked):
                    saved, checked = saving.get(vid, cells), busy   # read again under the lock; once while busy
                    unsaved = [cell for cell in cells if cell not in saved]
                    if not unsaved or (not busy and (started or any(fetch.error for fetch in watched))):
                        break
                    if not busy:
                        watched.add(_start(variable, unsaved))   # raises the refusals
                        started = True
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                _changed.wait(remaining)
            busy = any(fetch.variable == vid for fetch in _active)
        saved = saving.get(vid, cells)
        if len(saved) < len(cells):
            if busy:
                return 202, {"status": "fetching", "cells_total": len(cells), "cells_saved": len(saved)}
            failed = next((fetch for fetch in watched if fetch.error), None)   # the fetch is over, so what is missing failed
            raise HTTPException(503 if failed and failed.refused else 502, failed.error if failed else FAILED)

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
