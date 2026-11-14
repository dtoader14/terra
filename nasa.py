"""Small, explicit client for the official NASA AppEEARS API."""
import time
from pathlib import Path

import httpx

BASE = "https://appeears.earthdatacloud.nasa.gov/api"
# The heat-map variables. Each is one layer of a NASA product, drawn only where its quality layer passes the
# rule named in "quality" (processing.QUALITY). "first" and "last" are the days NASA's catalogue (CMR) held over
# Romania on 2026-11-14; None is a record still growing. NASA publishes the gap-filled products (MOD16A2GF,
# MOD17A2HGF) after each year ends, so those stop at the last finished year.
TEMPERATURE = dict(theme="Temperature", short="Surface temperature", quality="temperature", unit="°C", digits=1, cadence="8-day composite", resolution="1 km", palette="thermal", last=None)
GREENNESS = dict(theme="Vegetation", quality="reliability", unit="index", digits=3, cadence="16-day composite", resolution="1 km", palette="vegetation", last=None)
LEAVES = dict(theme="Vegetation", product="MOD15A2H.061", qa="FparLai_QC", quality="good_bit", cadence="8-day composite", resolution="500 m", palette="vegetation", first="2000-02-18", last=None)
YEARLY = dict(quality="good_bit", cadence="8-day total", resolution="500 m", first="2000-01-01", last="2025-12-31")
GROUND = "Surface temperature measures the ground, not the air above it. "
INDEX = "Satellite reflectance-derived index of vegetation condition. Unitless; not a percentage. "
ESTIMATE = " An estimate computed from satellite and weather-model data. NASA publishes it after each year ends."
VARIABLES = {
    "lst_day": TEMPERATURE | dict(label="Land surface temperature · Day · Terra", product="MOD11A2.061", layer="LST_Day_1km", qa="QC_Day", first="2000-02-18", note=GROUND + "Terra passes at about 10:30."),
    "lst_night": TEMPERATURE | dict(label="Land surface temperature · Night · Terra", product="MOD11A2.061", layer="LST_Night_1km", qa="QC_Night", first="2000-02-18", note=GROUND + "Terra passes at about 22:30."),
    "lst_day_aqua": TEMPERATURE | dict(label="Land surface temperature · Day · Aqua", product="MYD11A2.061", layer="LST_Day_1km", qa="QC_Day", first="2002-07-04", note=GROUND + "Aqua passes at about 13:30, near the warmest hour."),
    "lst_night_aqua": TEMPERATURE | dict(label="Land surface temperature · Night · Aqua", product="MYD11A2.061", layer="LST_Night_1km", qa="QC_Night", first="2002-07-04", note=GROUND + "Aqua passes at about 01:30."),
    "ndvi": GREENNESS | dict(label="Vegetation greenness · NDVI · Terra", short="NDVI", product="MOD13A2.061", layer="_1_km_16_days_NDVI", qa="_1_km_16_days_pixel_reliability", first="2000-02-18", note=INDEX + "From the Terra satellite."),
    "evi": GREENNESS | dict(label="Enhanced vegetation index · EVI · Terra", short="EVI", product="MOD13A2.061", layer="_1_km_16_days_EVI", qa="_1_km_16_days_pixel_reliability", first="2000-02-18", note=INDEX + "From the Terra satellite."),
    "ndvi_aqua": GREENNESS | dict(label="Vegetation greenness · NDVI · Aqua", short="NDVI", product="MYD13A2.061", layer="_1_km_16_days_NDVI", qa="_1_km_16_days_pixel_reliability", first="2002-07-04", note=INDEX + "From the Aqua satellite."),
    "evi_aqua": GREENNESS | dict(label="Enhanced vegetation index · EVI · Aqua", short="EVI", product="MYD13A2.061", layer="_1_km_16_days_EVI", qa="_1_km_16_days_pixel_reliability", first="2002-07-04", note=INDEX + "From the Aqua satellite."),
    "lai": LEAVES | dict(label="Leaf area index", short="Leaf area index", layer="Lai_500m", unit="m²/m²", digits=1, note="Green leaf area per unit of ground area."),
    "fpar": LEAVES | dict(label="Light absorbed by plants · FPAR", short="FPAR", layer="Fpar_500m", unit="fraction", digits=2, note="Share of the sunlight plants can use that the canopy absorbs, from 0 to 1."),
    "gpp": YEARLY | dict(label="Plant growth · GPP", short="GPP", theme="Vegetation", product="MOD17A2HGF.061", layer="Gpp_500m", qa="Psn_QC_500m", unit="kgC/m²/8 days", digits=3, palette="vegetation", note="Carbon taken up by plants through photosynthesis, summed over 8 days." + ESTIMATE),
    "et": YEARLY | dict(label="Evapotranspiration", short="Evapotranspiration", theme="Water", product="MOD16A2GF.061", layer="ET_500m", qa="ET_QC_500m", unit="kg/m²/8 days", digits=1, palette="water", note="Water leaving soil and plants as vapour, summed over 8 days; 1 kg/m² is 1 mm of water." + ESTIMATE),
    "pet": YEARLY | dict(label="Potential evapotranspiration", short="Potential evapotranspiration", theme="Water", product="MOD16A2GF.061", layer="PET_500m", qa="ET_QC_500m", unit="kg/m²/8 days", digits=1, palette="water", note="The evapotranspiration there would be with unlimited water, summed over 8 days; 1 kg/m² is 1 mm of water." + ESTIMATE),
}


def available(start=None, end=None):
    """The variables whose record overlaps the period. Either day may be left open."""
    return [{"id": key, **variable} for key, variable in VARIABLES.items()
            if (end is None or variable["first"] <= end.isoformat()) and (start is None or variable["last"] is None or start.isoformat() <= variable["last"])]


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
