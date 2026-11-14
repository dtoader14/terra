# Terra — Earth Data Explorer

A local web application with two views of NASA Earth data. **Heat map** draws real satellite observations for a region and period. **Time series** shows how a variable changed over the years at a city, county or box in Romania, with its rate of change and whether that change is statistically significant. Python/FastAPI handles the NASA login, fetching and processing; Leaflet provides the interactive map. No forecast, synthetic demo data or gap filling is used. Time series also offer NASA model products, and every variable is labelled as a satellite measurement or a model product.

## Run on this laptop

### Docker (recommended for a background local server)

With Docker Desktop running, open a terminal in this project folder:

```bash
docker compose up --build -d --wait
```

Open **http://127.0.0.1:8000**. The container runs in the background and restarts with Docker unless explicitly stopped. The port is published only on this laptop's loopback interface. The image runs as an unprivileged user and includes a health check.

```bash
docker compose ps             # Check health and the local port
docker compose logs -f web    # Follow server logs (Ctrl+C exits log viewing)
docker compose stop          # Stop the local server
docker compose up -d --wait   # Start it again
docker compose down          # Remove the container, keeping result files
```

The NASA login comes from a `.env` file beside `compose.yaml` (see [NASA login](#nasa-login)); Docker Compose passes it to the container when it starts. Docker excludes `.env`, the local virtual environment, tests, and generated files from its build context, so the login is never baked into the image. With no login in `.env`, heat maps fall back to a token saved in the [admin dashboard](http://127.0.0.1:8000/admin).

Generated results persist in the Docker volume `earth-data-explorer_results` at `/app/data`. The grid cells saved for time series (`series.sqlite`) live there too. Sessions and task history remain in memory, so download results before restarting. Do not use `docker compose down -v` unless you intend to delete the saved Docker results. The host's `data/` folder is separate from this Docker volume.

### Without Docker

Open a terminal in this folder and run:

```bash
./start.sh
```

Then open **http://127.0.0.1:8000**. Keep the terminal open; Ctrl+C stops the server. The project already has an installed `.venv`. On another machine, `start.sh` creates the environment and installs requirements automatically (Python 3.11+ and internet access required).

If port 8000 is in use:

```bash
.venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 8001
```

Use `127.0.0.1` consistently; `localhost` is a separate browser cookie origin.

## NASA login

Copy `.env.example` to `.env` and fill in your free [NASA Earthdata](https://urs.earthdata.nasa.gov/users/new) username and password:

```
EARTHDATA_USERNAME=...
EARTHDATA_PASSWORD=...
```

That one login serves both views. The server uses it to log in to AppEEARS for heat maps, and to ask Earthdata for the bearer token Giovanni needs for time series. Both tokens are kept in memory only. The login is never logged, never returned by any address and never written to disk by the app. `.env` is ignored by Git and left out of the Docker image.

- Use a password you use nowhere else: it opens your whole Earthdata account.
- The account must have accepted the "NASA GESDISC DATA ARCHIVE" application once ([how](https://disc.gsfc.nasa.gov/earthdata-login)), or Giovanni refuses it.
- With no login set, POWER time series still work (POWER needs no login), Giovanni time series answer only from grid cells already saved, and heat maps use the admin dashboard described next.

## Administrator setup and shared NASA access

Only needed when no login is set in `.env`.


1. Open **http://127.0.0.1:8000/admin**. On the first visit, create the local administrator password (at least 12 characters). The one-time setup closes after the first administrator is created.
2. In **Data connection**, paste a token returned by the official [NASA AppEEARS `/api/login` service](https://appeears.earthdatacloud.nasa.gov/api/), without the `Bearer` prefix. A generic Earthdata user token is not interchangeable. If needed, create an [Earthdata account](https://urs.earthdata.nasa.gov/users/new) and complete the initial [AppEEARS](https://appeears.earthdatacloud.nasa.gov/) account setup first.
3. Optionally enter the expiration returned by NASA in your local time. Click **Verify & save token**. The server checks NASA before replacing the active token. An invalid replacement leaves the previous token intact.
4. All visitors can now create heat maps from the explorer without entering credentials. Each browser has its own anonymous session and private extraction history. The platform's shared bearer token is attached only to server-to-NASA requests.
5. Use **Test connection**, **Replace token**, **Pause requests**, or **Remove token** to manage access. The dashboard also shows current-server extraction counts and recent activity. Visitor-facing availability refreshes every 30 seconds and is checked again before a request is submitted.

Admin sessions last up to eight hours and end on server restart. Ten failed sign-ins within 15 minutes temporarily block further login attempts. The administrator password is stored as a salted scrypt hash. There is one administrator role on this local instance, with no registration for ordinary visitors.

Tokens are persisted in `/app/data/platform-settings.json` in Docker, or `data/platform-settings.json` when running directly. The file has owner-only permissions (`0600`); it is outside the static web directory and never exposed by download routes. The token is stored as a server-side secret **without encryption at rest**. Protect Docker volume backups accordingly. Even authenticated admin API responses do not return the saved token. Browser storage contains only HttpOnly session cookies, not NASA tokens or passwords.

The admin password hash and connection settings survive container recreation in the Docker results volume. If you forget the local admin password, stop the server and reset only the `admin` entry in this settings file using trusted local filesystem access, then restart and complete the one-time setup. Do not delete the results volume to reset a password.

AppEEARS tokens typically expire after approximately 48 hours. Replace them through the dashboard. If NASA rejects the saved token, new user requests are disabled with a service-status message. A failed old request cannot invalidate a replacement token. Expiration is shown as unknown unless supplied by the administrator; the app does not infer token issuance time. Pausing or removing a token stops new requests; already-running extractions retain their original token.

## Use the explorer

1. Set your place: search a city or county in Romania, select two corners on the map, or type coordinates.
2. Choose a date range and a variable, and click **Generate heat map**.
3. Watch processing status while NASA prepares the data. Requests may take several minutes. If the data service is unavailable, contact the administrator; no user credential form is shown.
4. View the period mean or individual composites, adjust opacity, and download PNG, GeoTIFF, or metadata.

For a time series, switch to **Time series**:

1. Set your place: a city, a county or a box.
2. Optionally set a first and last month.
3. Pick a variable. The list shows only variables with data for that place and period, and each one says whether it is a satellite measurement or a model product. Then click **Show time series**.
4. Read the chart: the thin line is the monthly values, the dots are yearly means, the straight line is the trend. Below it are the rate of change per decade, its p-value and whether it is significant.

Browser reloads reconnect to the current session's latest extraction. Download results before the server restarts, because job history is still kept in memory.

## Implemented data source

Heat maps use the official [NASA AppEEARS API](https://appeears.earthdatacloud.nasa.gov/api/), one of the requested NASA services. The app checks its public catalog before submitting tasks.

| Variable | Product | Layer | Units | Source composite |
| --- | --- | --- | --- | --- |
| Day surface temperature | MOD11A2.061 | LST_Day_1km | °C | 8 days |
| Night surface temperature | MOD11A2.061 | LST_Night_1km | °C | 8 days |
| Vegetation greenness | MOD13A2.061 | _1_km_16_days_NDVI | index | 16 days |
| Enhanced vegetation index | MOD13A2.061 | _1_km_16_days_EVI | index | 16 days |

These are approximately 1 km satellite retrieval/composite products, not raw sensor radiances and not forecasts. Land-surface temperature is different from air temperature. Heat maps intentionally use an allowlist of observation-derived products.

Time series come from [Giovanni](https://giovanni.gsfc.nasa.gov/) and [NASA POWER](https://power.larc.nasa.gov/); see [Time series](#time-series). [NOAA CDO](https://www.ncei.noaa.gov/cdo-web/) is linked as an additional resource, **not an implemented provider**. NOAA's [CDO API](https://www.ncei.noaa.gov/cdo-web/webservices/v2) requires its own `token` header and provides station records; it does not share AppEEARS bearer authentication.

### API workflow

```
Browser → local FastAPI server → NASA AppEEARS
  Admin         Verify and save shared token in /admin
  Request       GET /product/{product}; POST /task
  Progress      GET /task/{task_id}
  Result        GET /bundle/{task_id}
  Download      GET /bundle/{task_id}/{file_id} → signed S3 download
  Render        GeoTIFF → quality mask → scale → temporal mean → PNG/GeoTIFF
```

All protected NASA calls use `Authorization: Bearer <token>`. Authorization is stripped on cross-origin redirects to S3. Downloads are checked against NASA's supplied SHA-256 checksum when present. GET calls retry transient errors; task submission is not automatically retried to avoid creating duplicate requests.

## Time series

Monthly series for Romania (43.6–48.3°N, 20.2–29.7°E) from two NASA sources:

| Source | Variables | Login |
| --- | --- | --- |
| [Giovanni](https://giovanni.gsfc.nasa.gov/) point time-series API | 141 monthly variables: AIRS, GPM IMERG and TRMM (satellite); GLDAS, FLDAS and MERRA-2 (model) | Earthdata login |
| [NASA POWER](https://power.larc.nasa.gov/) monthly regional API | 213 monthly variables: weather values from MERRA-2 (model); sunlight and cloud values from CERES (satellite) | none |

```
GET /api/series/variables                    the offered variables
GET /api/series/variables?place=county:Cluj&start=1990-01&end=2020-12
                                             only those with data for that place and period
GET /api/places?q=alba                       Romanian towns and counties matching a text
GET /api/series?variable=power:T2MDEW&place=686578&start=2000-01&end=2025-12
GET /api/series?variable=gio:AIRS3STM_7_0_SurfAirTemp_A&bbox=23.0,45.8,24.2,46.6
```

- A town is the one grid cell that contains it. A county is the box around all of its towns, not its exact outline. A box (`west,south,east,north`) is the mean of every grid cell whose centre lies inside it, each weighted by the cosine of its latitude. The answer says how many cells were used and on which grid.
- The server keeps every grid cell it fetched in `data/series.sqlite`, so each cell is fetched from NASA once. While cells are being fetched the address answers 202 with a count, and the page asks again.
- The trend is a least-squares line through the yearly means of complete calendar years, and needs at least 10 of them. The answer gives the slope per decade, its 95% range, the p-value of the slope (t-test) and the Mann-Kendall p-value. "Significant" means the slope's p-value is below 0.05. Yearly means are used so the seasonal cycle does not drive the result.
- Values are in NASA's own units (kelvin, kg m-2 s-1, and so on). The variable list carries a plain-language hint for each.
- Limits: Romania only; at most 800 unsaved grid cells per request; two fetches at a time; 20,000 Giovanni calls a day.

## How the heat map is calculated

- The requested box uses longitude/latitude in WGS84. AppEEARS delivers geographic GeoTIFFs. The app resamples them using **nearest neighbors** to a Web Mercator display grid, at most 640 pixels on its longest side. Overlay geometry therefore matches Leaflet's map projection.
- Rasterio reads packed values. The app applies the GeoTIFF scale/offset once, falling back to NASA catalog metadata if needed. It removes source fill/out-of-range values and converts Kelvin to Celsius for temperature. NASA documents [packed area outputs](https://forum.earthdata.nasa.gov/viewtopic.php?p=10386) and [GeoTIFF scale/offset metadata](https://forum.earthdata.nasa.gov/viewtopic.php?t=3934).
- Each science raster must have its matching QA raster. MOD11 retains pixels with mandatory QA bits=0, data quality bits=0, and LST error bits=0 (≤1 K). MOD13 retains pixel reliability=0 (good). Missing QA causes an error rather than an unfiltered heat map. QA value zero is valid and is not discarded just because a QA TIFF also declares zero as nodata.
- The period mean gives equal weight to each available good-quality composite at a pixel. Missing pixels do not become zero and are not filled or interpolated. A pixel can have fewer contributing composites than its neighbors.
- Source composite start dates label the frames. An 8-day/16-day observation window can extend outside the selected endpoints. This is not a daily, duration-weighted, or area-weighted mean.
- A fixed color scale spanning the available frames makes comparisons consistent. Transparent areas have no retained data. Summary numbers describe the **display grid**.
- The GeoTIFF export is the period mean on that reduced EPSG:3857 grid, not the full native-resolution source. Metadata records the original source names, file IDs, checksums, task ID, variable, dates, bounds, units, method, and QA rules. Original files can be obtained through the user's AppEEARS task. The local `data/<job-id>/count.tif` records the number of contributing composites at each pixel and can also be downloaded at `/api/jobs/<job-id>/files/count.tif?download=true` in the same authenticated session.

## Limits and local storage

- Up to **93 inclusive days**, starting no earlier than 2000-02-24. NASA may have narrower product-specific availability or publication delays.
- Up to **100 square degrees**, no side over 20°, minimum side 0.01°, latitude within ±85°. Split regions crossing the antimeridian into separate requests.
- Two concurrent local extractions; 24 source composites per result; 150 MB per downloaded file; 500 MB declared total per result. Large or cloudy selections may return no usable pixels.
- Polling stops after two hours; the NASA task ID remains visible for checking in AppEEARS.
- Browser sessions and task history are in memory. Restarting the server removes browser access to that session's tasks. The admin password hash and shared NASA token persist separately. Download results first. Completed PNG/GeoTIFF/metadata files remain under `data/` until you remove them. Intermediate source rasters are removed after processing. A failed download may leave partial intermediate files under its job folder.
- This is a **single-laptop app**, bound to `127.0.0.1`. It has same-origin mutation checks, host validation, and session ownership checks. Public deployment would need HTTPS, durable authentication/task storage, resource quotas, and a production worker queue.
- Leaflet is bundled locally with its license. OpenStreetMap or Esri basemap tiles and Google Fonts require internet. Basemap failure does not substitute data or prevent manual coordinate entry. Standard OSM tiles are for interactive local viewing under the [tile usage policy](https://operations.osmfoundation.org/policies/tiles/); there is no bulk download or offline prefetch.

## Deploying

The image runs on any host that builds a `Dockerfile`. Set these on the host, never in the repository:

| Setting | Value |
| --- | --- |
| `EARTHDATA_USERNAME`, `EARTHDATA_PASSWORD` | the NASA login |
| `TERRA_PUBLIC` | `1` |
| `ALLOWED_HOSTS` | the public host name, for example `terra.onrender.com` |
| `PORT` | only if the host does not set it; the server listens on it (default 8000) |

`TERRA_PUBLIC=1` switches `/admin` and every `/api/admin/` address off, because on a public link the first visitor could otherwise set the admin password. Heat maps and time series stay on and use the login from the environment. A public link lets strangers use that NASA login through the app, within the limits listed above. On a host without a persistent disk, saved series and finished heat maps are lost on every restart.

## Development and checks

```bash
.venv/bin/python -m pip install pytest
.venv/bin/python -m pytest -q
node --check static/app.js
```

`requirements-lock.txt` records the exact environment used for validation, including the test dependency. `requirements.txt` contains the supported runtime ranges.

Tests cover query bounds/dates, NASA payload formatting, scaling exactly once, QA rejection, QA zero handling, missing-pixel averaging, transparent rendering, the download-to-GeoTIFF pipeline, bearer headers, cross-origin header stripping, expiration errors, CSRF checks, and session isolation. Artificial fixtures exist only in tests and are never offered as NASA observations. Time-series tests cover grid cells, area-weighted averaging, the trend numbers against a hand-checked example, town matching, the format of both NASA answers (one real Giovanni answer is kept as a fixture), fetching, the limits, the environment login and public mode. No test calls NASA, and the test run never holds the real login: `tests/conftest.py` blanks both names before `.env` is loaded.

Validation performed during development: The original raster suite passed; public NASA product metadata was fetched live. Admin access controls, shared-token routing, persistence, credential replacement, and visitor isolation are covered by the current test suite. **Checked live against NASA on 2026-11-14** with the login from `.env`: a heat map completed end to end (NDVI, southern Romania, July 2024, three composites), and time series came back from Giovanni and POWER. For Romania's whole box, Terra's own box average matched NASA's area-averaged series on AIRS air temperature and GLDAS soil moisture. There is no demo fallback.

## Project structure

```
app.py           Local API, visitor/admin sessions, shared-token jobs
platform_settings.py  Persistent administrator hash and NASA connection
nasa.py          Official NASA API client and variable allowlist
series.py        Time-series maths, the offered variables, town lookup
sources.py       Giovanni and POWER calls, the two NASA logins
fetching.py      Saved grid cells, fetching and its limits
variables.csv    The offered time-series variables
places.tsv       Romanian towns, from GeoNames
processing.py    Quality filtering, scaling, projection, compositing, export
static/          Responsive frontend and bundled Leaflet
tests/           Isolated calculation and integration tests
start.sh         Local launcher
data/            Generated results (ignored by Git)
```

## Data credits

- Heat maps: NASA AppEEARS (LP DAAC), MODIS Terra Collection 6.1.
- Time series: NASA Giovanni (GES DISC) and NASA POWER (Langley Research Center).
- Town names: [GeoNames](https://www.geonames.org/), licensed [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
- Map: Leaflet (bundled with its licence); basemap tiles from OpenStreetMap contributors and Esri.
