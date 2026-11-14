"""Small, explicit client for the official NASA AppEEARS API."""
import time
from pathlib import Path

import httpx

BASE = "https://appeears.earthdatacloud.nasa.gov/api"
TEMPERATURE = dict(theme="Temperature", short="Surface temperature", summary="How hot the ground itself is, seen from space. This is not the air temperature of a weather report: on a sunny day bare soil, rock and cities run much hotter than the air, while forests, crops and water stay cooler. Day maps show where the land heats up; night maps show where it keeps its heat.", quality="temperature", unit="°C", digits=1, cadence="8-day composite", resolution="1 km", palette="thermal", last=None)
GREENNESS = dict(theme="Vegetation", quality="reliability", unit="index", digits=3, cadence="16-day composite", resolution="1 km", palette="vegetation", last=None)
LEAVES = dict(theme="Vegetation", product="MOD15A2H.061", qa="FparLai_QC", quality="good_bit", cadence="8-day composite", resolution="500 m", palette="vegetation", first="2000-02-18", last=None)
YEARLY = dict(quality="good_bit", cadence="8-day total", resolution="500 m", first="2000-01-01", last="2025-12-31")
GROUND = "Surface temperature measures the ground, not the air above it. "
INDEX = "Satellite reflectance-derived index of vegetation condition. Unitless; not a percentage. "
ESTIMATE = " An estimate computed from satellite and weather-model data. NASA publishes it after each year ends."
NDVI = "A greenness score for plants. Near 0 is bare soil, rock or buildings; around 0.2 to 0.5 is grass, crops or shrubs; above 0.6 is dense, healthy vegetation. It falls in drought, after harvest and in winter."
EVI = "A greenness score like NDVI, corrected for haze and bright soil. Higher means more and healthier vegetation. It keeps telling dense forests and crops apart where NDVI levels off, and its values run lower than NDVI's."
VARIABLES = {
    "lst_day": TEMPERATURE | dict(label="Land surface temperature · Day · Terra", product="MOD11A2.061", layer="LST_Day_1km", qa="QC_Day", first="2000-02-18", note=GROUND + "Terra passes at about 10:30."),
    "lst_night": TEMPERATURE | dict(label="Land surface temperature · Night · Terra", product="MOD11A2.061", layer="LST_Night_1km", qa="QC_Night", first="2000-02-18", note=GROUND + "Terra passes at about 22:30."),
    "lst_day_aqua": TEMPERATURE | dict(label="Land surface temperature · Day · Aqua", product="MYD11A2.061", layer="LST_Day_1km", qa="QC_Day", first="2002-07-04", note=GROUND + "Aqua passes at about 13:30, near the warmest hour."),
    "lst_night_aqua": TEMPERATURE | dict(label="Land surface temperature · Night · Aqua", product="MYD11A2.061", layer="LST_Night_1km", qa="QC_Night", first="2002-07-04", note=GROUND + "Aqua passes at about 01:30."),
    "ndvi": GREENNESS | dict(label="Vegetation greenness · NDVI · Terra", short="NDVI", summary=NDVI, product="MOD13A2.061", layer="_1_km_16_days_NDVI", qa="_1_km_16_days_pixel_reliability", first="2000-02-18", note=INDEX + "From the Terra satellite."),
    "evi": GREENNESS | dict(label="Enhanced vegetation index · EVI · Terra", short="EVI", summary=EVI, product="MOD13A2.061", layer="_1_km_16_days_EVI", qa="_1_km_16_days_pixel_reliability", first="2000-02-18", note=INDEX + "From the Terra satellite."),
    "ndvi_aqua": GREENNESS | dict(label="Vegetation greenness · NDVI · Aqua", short="NDVI", summary=NDVI, product="MYD13A2.061", layer="_1_km_16_days_NDVI", qa="_1_km_16_days_pixel_reliability", first="2002-07-04", note=INDEX + "From the Aqua satellite."),
    "evi_aqua": GREENNESS | dict(label="Enhanced vegetation index · EVI · Aqua", short="EVI", summary=EVI, product="MYD13A2.061", layer="_1_km_16_days_EVI", qa="_1_km_16_days_pixel_reliability", first="2002-07-04", note=INDEX + "From the Aqua satellite."),
    "lai": LEAVES | dict(label="Leaf area index", short="Leaf area index", summary="How much leaf there is above each patch of ground. 1 means the leaves would cover the ground exactly once. Bare land is near 0, crops and grass reach a few, dense forest 5 or more.", layer="Lai_500m", unit="m²/m²", digits=1, note="Green leaf area per unit of ground area."),
    "fpar": LEAVES | dict(label="Light absorbed by plants · FPAR", short="FPAR", summary="The share of usable sunlight that plants absorb. 0 means none of it, as on bare ground; close to 1 means almost all of it, as under a closed, healthy canopy.", layer="Fpar_500m", unit="fraction", digits=2, note="Share of the sunlight plants can use that the canopy absorbs, from 0 to 1."),
    "gpp": YEARLY | dict(label="Plant growth · GPP", short="GPP", summary="How much carbon plants take from the air by photosynthesis in 8 days: a measure of how fast they are growing. High over crops and forests in spring and summer, near zero in winter or drought.", theme="Vegetation", product="MOD17A2HGF.061", layer="Gpp_500m", qa="Psn_QC_500m", unit="kgC/m²/8 days", digits=3, palette="vegetation", note="Carbon taken up by plants through photosynthesis, summed over 8 days." + ESTIMATE),
    "et": YEARLY | dict(label="Evapotranspiration", short="Evapotranspiration", summary="How much water moves from the land to the air in 8 days: evaporation from soil plus what plants release. 1 kg/m² is 1 mm of water. High means moist soil and active plants; low means dry or dormant land.", theme="Water", product="MOD16A2GF.061", layer="ET_500m", qa="ET_QC_500m", unit="kg/m²/8 days", digits=1, palette="water", note="Water leaving soil and plants as vapour, summed over 8 days; 1 kg/m² is 1 mm of water." + ESTIMATE),
    "pet": YEARLY | dict(label="Potential evapotranspiration", short="Potential evapotranspiration", summary="How much water the air could take up in 8 days if the land never ran dry: the atmosphere's demand, set by heat, sun and wind. Where it is far above the actual evapotranspiration, the land is short of water.", theme="Water", product="MOD16A2GF.061", layer="PET_500m", qa="ET_QC_500m", unit="kg/m²/8 days", digits=1, palette="water", note="The evapotranspiration there would be with unlimited water, summed over 8 days; 1 kg/m² is 1 mm of water." + ESTIMATE),
}


def available(start=None, end=None):
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
