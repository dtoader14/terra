"""Every NASA call for time series, and the two logins. Nothing here logs or returns a password or a token."""
import os
import time

import httpx
import numpy as np

from nasa import NasaClient, NasaError

GIOVANNI = "https://api.giovanni.earthdata.nasa.gov/timeseries"
EARTHDATA_LOGIN = "https://urs.earthdata.nasa.gov/api/users/find_or_create_token"
POWER = "https://power.larc.nasa.gov/api/temporal/monthly/regional"
# Romania's box padded to POWER's 10 degree limit, so the cells on its edges come back too.
POWER_BOX = {"latitude-min": "43.25", "latitude-max": "48.75", "longitude-min": "20.0", "longitude-max": "30.0"}
now = time.time
_tokens = {}


def _login():
    return os.environ.get("EARTHDATA_USERNAME", ""), os.environ.get("EARTHDATA_PASSWORD", "")


def has_login():
    return all(_login())


def forget(name):
    _tokens.pop(name, None)


def earthdata_token():
    """The Earthdata bearer token Giovanni wants, made from the login and kept in memory."""
    if "earthdata" not in _tokens:
        try:
            with httpx.Client(timeout=120, follow_redirects=False) as client:
                response = client.post(EARTHDATA_LOGIN, auth=_login())
        except httpx.RequestError:
            raise NasaError("Cannot reach NASA Earthdata. Please try again shortly.") from None
        if response.status_code in (401, 403):
            raise NasaError("NASA refused the Earthdata username or password.", authentication=True)
        if not response.is_success:
            raise NasaError(f"NASA Earthdata returned HTTP {response.status_code}.")
        _tokens["earthdata"] = (response.json()["access_token"], now())
    return _tokens["earthdata"][0]


def appeears_token():
    """AppEEARS has its own login and its tokens last a day or two: get a fresh one every 12 hours."""
    if "appeears" not in _tokens or now() - _tokens["appeears"][1] >= 12 * 3600:
        _tokens["appeears"] = (NasaClient.login(*_login())["token"], now())
    return _tokens["appeears"][0]


def _index(month):
    return int(month[:4]) * 12 + int(month[5:7]) - 1


def _run(months):
    """A NaN-filled array running month by month from the first to the last of {month: value}."""
    first = min(months)
    values = np.full(_index(max(months)) - _index(first) + 1, np.nan)
    for month, value in months.items():
        values[_index(month) - _index(first)] = value
    return first, values


def parse_giovanni(text):
    """((lat, lon) of the cell centre, first month, values) from Giovanni's CSV answer."""
    header, months, in_data = {}, {}, False
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("Timestamp"):
            in_data = True
        elif in_data:
            stamp, _, value = line.partition(",")
            months[stamp[:7]] = float(value) if value.strip() else float("nan")
        else:
            key, _, value = line.partition(",")
            header[key] = value
    cell = (float(header["lat"]), float(header["lon"])) if header.get("lat") and header.get("lon") else None
    if not months:
        return cell, "", np.array([])
    if header.get("undef"):
        undef = float(header["undef"])
        months = {month: float("nan") if abs(value - undef) <= 1e-6 * max(1.0, abs(undef)) else value for month, value in months.items()}
    return cell, *_run(months)


def _refused(response):
    # Giovanni answers an unusable token with 401/403, or with a 500 that carries this header.
    return response.status_code in (401, 403) or response.headers.get("x-amzn-errortype") == "AuthorizerConfigurationException"


def giovanni_cell(variable, lat, lon):
    """One grid cell's whole record. A refused token is renewed once; trouble is tried three times."""
    params = {"data": variable["id"].removeprefix("gio:"), "location": f"[{lat},{lon}]",
              "time": f"{variable['begin']}T00:00:00/{variable['end']}T23:59:59"}
    refusals = trouble = 0
    while True:
        token = earthdata_token()
        try:
            with httpx.Client(timeout=120, follow_redirects=False) as client:
                response = client.get(GIOVANNI, params=params, headers={"Authorization": f"Bearer {token}"})
        except httpx.RequestError:
            response = None
        if response is not None and _refused(response):
            forget("earthdata")
            refusals += 1
            if refusals == 2:
                raise NasaError("NASA refused the login for Giovanni.", authentication=True)
            continue
        if response is not None and response.status_code == 200:
            cell, first, values = parse_giovanni(response.text)
            if not len(values) or cell is None:
                return (lat, lon), variable["first_month"], np.array([])
            return cell, first, values
        if response is not None and response.status_code not in (429, 500, 502, 503, 504):
            raise NasaError(f"NASA Giovanni returned HTTP {response.status_code}.")
        trouble += 1
        if trouble == 3:
            raise NasaError("NASA Giovanni did not answer. Please try again shortly.")
        time.sleep(2 * trouble)


def power_cells(parameter):
    """Every grid cell of one POWER variable around Romania, in one call: {(lat, lon): (first month, values)}."""
    params = {**POWER_BOX, "parameters": parameter, "community": "AG", "start": "1981", "end": "2025", "format": "JSON"}
    try:
        with httpx.Client(timeout=120, follow_redirects=False) as client:
            response = client.get(POWER, params=params)
    except httpx.RequestError:
        response = None
    if response is None or response.status_code != 200:
        raise NasaError("NASA POWER did not answer. Please try again shortly.")
    cells = {}
    for feature in response.json().get("features", []):
        lon, lat = feature["geometry"]["coordinates"][:2]
        # Keys are YYYYMM; YYYY13 is the yearly mean; -999 marks a missing month.
        months = {f"{key[:4]}-{key[4:]}": float("nan") if value == -999 else value
                  for key, value in feature["properties"]["parameter"][parameter].items() if not key.endswith("13")}
        if months:
            cells[(round(lat, 4), round(lon, 4))] = _run(months)
    return cells
