"""Small, explicit client for the official NASA AppEEARS API."""
import time
from pathlib import Path

import httpx

BASE = "https://appeears.earthdatacloud.nasa.gov/api"
VARIABLES = {
    "lst_day": dict(label="Land surface temperature · Day", product="MOD11A2.061", layer="LST_Day_1km", qa="QC_Day", unit="°C", cadence="8-day composite", palette="thermal"),
    "lst_night": dict(label="Land surface temperature · Night", product="MOD11A2.061", layer="LST_Night_1km", qa="QC_Night", unit="°C", cadence="8-day composite", palette="thermal"),
    "ndvi": dict(label="Vegetation greenness · NDVI", product="MOD13A2.061", layer="_1_km_16_days_NDVI", qa="_1_km_16_days_pixel_reliability", unit="index", cadence="16-day composite", palette="vegetation"),
    "evi": dict(label="Enhanced vegetation index · EVI", product="MOD13A2.061", layer="_1_km_16_days_EVI", qa="_1_km_16_days_pixel_reliability", unit="index", cadence="16-day composite", palette="vegetation"),
}


class NasaError(Exception):
    def __init__(self, message, authentication=False):
        super().__init__(message)
        self.authentication = authentication


def check(response):
    if response.status_code in (401, 403):
        raise NasaError("NASA rejected the token or it expired. Replace it with a fresh AppEEARS bearer token.", authentication=True)
    if response.status_code == 429:
        raise NasaError("NASA's request limit was reached. Wait a minute and try again.")
    if not response.is_success:
        # Never echo an upstream request, credentials, or signed download URL.
        raise NasaError(f"NASA returned HTTP {response.status_code}. Check your selection and NASA service availability.")


class NasaClient:
    def __init__(self, token=""):
        self.token = token

    def request(self, method, path, **kwargs):
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        try:
            with httpx.Client(timeout=60, follow_redirects=True) as client:
                for attempt in range(3):
                    response = client.request(method, BASE + path, headers=headers, **kwargs)
                    # POST /task is intentionally not retried: duplicate submissions cost work.
                    if method == "GET" and response.status_code in (429, 502, 503, 504) and attempt < 2:
                        time.sleep(2 ** (attempt + 1))
                        continue
                    check(response)
                    return response.json()
        except httpx.RequestError:
            raise NasaError("Cannot reach NASA. Check your internet connection and try again.") from None

    @staticmethod
    def login(username, password):
        return NasaClient().request("POST", "/login", auth=(username, password), content=b"")

    def download(self, task_id, item, directory):
        target = Path(directory) / (item["file_id"] + ".tif")
        if item.get("file_size", 0) > 150_000_000:
            raise NasaError("A NASA file exceeds the local 150 MB limit. Select a smaller region or shorter period.")
        try:
            # httpx strips Authorization when following a cross-origin S3 redirect.
            with httpx.Client(timeout=120, follow_redirects=True) as client:
                with client.stream("GET", f"{BASE}/bundle/{task_id}/{item['file_id']}", headers={"Authorization": f"Bearer {self.token}"}) as response:
                    check(response)
                    size = 0
                    with target.open("wb") as output:
                        for chunk in response.iter_bytes():
                            size += len(chunk)
                            if size > 150_000_000:
                                raise NasaError("Downloaded file is too large. Select a smaller region.")
                            output.write(chunk)
        except httpx.RequestError:
            target.unlink(missing_ok=True)
            raise NasaError("NASA download was interrupted. Please retry the extraction.") from None
        if item.get("sha256"):
            import hashlib
            with target.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            if digest != item["sha256"]:
                target.unlink(missing_ok=True)
                raise NasaError("NASA download checksum did not match. Please retry.")
        return target


def task_payload(query):
    variable = VARIABLES[query.variable]
    west, south, east, north = query.bbox
    ring = [[west, south], [east, south], [east, north], [west, north], [west, south]]
    return {
        "task_type": "area", "task_name": f"Earth Explorer {query.variable} {query.start}",
        "params": {
            "dates": [{"startDate": query.start.strftime("%m-%d-%Y"), "endDate": query.end.strftime("%m-%d-%Y")}],
            "layers": [{"product": variable["product"], "layer": layer} for layer in (variable["layer"], variable["qa"])],
            "geo": {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {}, "geometry": {"type": "Polygon", "coordinates": [ring]}}]},
            "output": {"format": {"type": "geotiff"}, "projection": "geographic"},
        },
    }
