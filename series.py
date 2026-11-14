"""Time series for Romania: the offered variables, town lookup, grid cells, area-weighted averages and linear trend."""
import csv
import math
import unicodedata
from collections import namedtuple
from pathlib import Path

import numpy as np
from scipy import stats

HERE = Path(__file__).resolve().parent
ROMANIA = (20.2, 43.6, 29.7, 48.3)  # west, south, east, north
Grid = namedtuple("Grid", "dlat dlon plat plon")  # cell size and phase: a centre is phase + k * size
EPS = 1e-9
TOO_SHORT = "Fewer than 10 complete years in this period."
PUBLIC_FIELDS = ("id", "name", "theme", "unit", "unit_hint", "source", "kind", "product", "first_month", "last_month")


def grid_label(grid):
    return f"{grid.dlat:g} x {grid.dlon:g} degree"


def _variables():
    with open(HERE / "variables.csv", encoding="utf-8", newline="") as stream:
        return {row["id"]: {**{key: row[key] for key in PUBLIC_FIELDS + ("begin", "end")},
                            "grid": Grid(*(float(row[key]) for key in Grid._fields))} for row in csv.DictReader(stream)}


VARIABLES = _variables()


def public_variable(variable):
    return {**{key: variable[key] for key in PUBLIC_FIELDS}, "grid": grid_label(variable["grid"])}


def _plain(text):
    """Lower case, no diacritics, hyphens as spaces: 'Alba-Iulia' and 'ALBA IULIA' compare equal."""
    text = unicodedata.normalize("NFKD", text.lower())
    return " ".join("".join(c for c in text if not unicodedata.combining(c)).replace("-", " ").split())


def _places():
    places, names = {}, []
    with open(HERE / "places.tsv", encoding="utf-8") as stream:
        next(stream)
        for line in stream:  # already ordered by population, largest first
            pid, name, ascii_name, alt, county, lat, lon, population = line.rstrip("\n").split("\t")
            places[pid] = {"id": pid, "name": name, "county": county, "lat": float(lat), "lon": float(lon), "population": int(population)}
            names.append((pid, {_plain(n) for n in (name, ascii_name, *alt.split(","))} - {""}))
    return places, names


PLACES, _NAMES = _places()


def find_places(q, limit=10):
    """Towns whose name starts with q, then towns whose name contains it; larger towns first."""
    q, limit = _plain(q), min(max(limit, 1), 25)
    if not q or len(q) > 80:
        return []
    starts, contains = [], []
    for pid, names in _NAMES:
        if any(name.startswith(q) for name in names):
            starts.append(pid)
            if len(starts) == limit:
                break
        elif len(contains) < limit and any(q in name for name in names):
            contains.append(pid)
    return [PLACES[pid] for pid in (starts + contains)[:limit]]


def place(pid):
    return PLACES.get(pid)


def clip(bbox):
    west, south, east, north = max(bbox[0], ROMANIA[0]), max(bbox[1], ROMANIA[1]), min(bbox[2], ROMANIA[2]), min(bbox[3], ROMANIA[3])
    return (west, south, east, north) if west < east and south < north else None


def _snap(value, size, phase):
    return round(phase + round((value - phase) / size) * size, 4)


def cell_of(grid, lat, lon):
    return _snap(lat, grid.dlat, grid.plat), _snap(lon, grid.dlon, grid.plon)


def _centres(low, high, size, phase):
    first, last = math.ceil((low - phase - EPS) / size), math.floor((high - phase + EPS) / size)
    return [round(phase + k * size, 4) for k in range(first, last + 1)]


def cells_in(grid, bbox):
    west, south, east, north = bbox
    cells = [(lat, lon) for lat in _centres(south, north, grid.dlat, grid.plat) for lon in _centres(west, east, grid.dlon, grid.plon)]
    # A box smaller than a cell holds no centre: use the cell that holds the box's centre.
    return cells or [cell_of(grid, (south + north) / 2, (west + east) / 2)]


def _index(month):
    return int(month[:4]) * 12 + int(month[5:7]) - 1


def _month(index):
    return f"{index // 12}-{index % 12 + 1:02d}"


def average(records):
    """Monthly mean over cells, weighted by cos(latitude). records: {(lat, lon): (first_month, values, NaN = missing)}."""
    spans = [(_index(first), cell[0], values) for cell, (first, values) in records.items() if len(values)]
    if not spans:
        return [], [], []
    start, stop = min(s for s, _, _ in spans), max(s + len(values) for s, _, values in spans)
    stack = np.full((len(spans), stop - start), np.nan)
    for row, (s, _, values) in enumerate(spans):
        stack[row, s - start:s - start + len(values)] = values
    present = np.isfinite(stack)
    weights = np.cos(np.radians([lat for _, lat, _ in spans]))[:, None] * present
    total, used = weights.sum(axis=0), present.sum(axis=0)
    sums = np.nansum(stack * weights, axis=0)
    values = [float(sums[i] / total[i]) if used[i] else None for i in range(stop - start)]
    return [_month(start + i) for i in range(stop - start)], values, [int(n) for n in used]


def window(months, values, used, start, end):
    keep = [i for i, month in enumerate(months) if (start is None or month >= start) and (end is None or month <= end)]
    return [months[i] for i in keep], [values[i] for i in keep], [used[i] for i in keep]


def yearly(months, values):
    """(year, mean) for every calendar year with all 12 months present."""
    years = {}
    for month, value in zip(months, values):
        if value is not None:
            years.setdefault(int(month[:4]), []).append(value)
    return [(year, sum(found) / 12) for year, found in sorted(years.items()) if len(found) == 12]


def mann_kendall(y):
    """Two-sided p-value of the Mann-Kendall trend test, normal approximation with tie correction."""
    y = np.asarray(y, dtype=float)
    n = len(y)
    s = float(np.sign(y[None, :] - y[:, None])[np.triu_indices(n, 1)].sum())
    ties = np.unique(y, return_counts=True)[1]
    variance = (n * (n - 1) * (2 * n + 5) - float((ties * (ties - 1) * (2 * ties + 5)).sum())) / 18
    if s == 0 or variance <= 0:
        return 1.0
    return math.erfc((abs(s) - 1) / math.sqrt(variance) / math.sqrt(2))


def trend(months, values):
    """Least-squares line through yearly means of complete years. Returns (trend, None) or (None, reason)."""
    points = yearly(months, values)
    if len(points) < 10:
        return None, TOO_SHORT
    years, means = [year for year, _ in points], [mean for _, mean in points]
    fit = stats.linregress(years, means)
    margin = float(stats.t.ppf(0.975, len(points) - 2) * fit.stderr)
    slope, intercept = float(fit.slope), float(fit.intercept)
    return {
        "basis": "yearly means of complete years", "years": len(points), "slope_per_decade": slope * 10,
        "ci95": [(slope - margin) * 10, (slope + margin) * 10], "p_value": float(fit.pvalue),
        "mann_kendall_p": mann_kendall(means), "significant": bool(fit.pvalue < 0.05),
        "line": [[year, intercept + slope * year] for year in (years[0], years[-1])],
    }, None
