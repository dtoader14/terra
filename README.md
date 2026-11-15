# Terra — Earth Data Explorer

A local web app that shows how Romania's climate is changing, from NASA open data only: what is changing, where, how much, and whether the change is significant. Built during the NASA Space Apps Challenge 2026 for the challenge *Be An Earth System Trend Detective!*

## What it does

- **Time series.** Pick a town, county or box in Romania, a period and one of 354 monthly variables. Terra draws the series and gives the rate of change per decade, its 95% range, the p-value and the Mann-Kendall test.
- **Trend map.** For the chosen variable, the 42 county seats are coloured by their trend over the whole record: red rising, blue falling, grey no clear change. The ten fastest changes are listed.
- **Heat map.** Real MODIS satellite observations for a box and up to 93 days, drawn only where NASA's quality flag is good.
- **Ask Terra.** A question in plain English, such as "How did air temperature change in Cluj-Napoca since 1990?", fills in the place, period and variable and opens the chart.

No forecasts, no synthetic data, no gap filling. Every variable is labelled as a satellite measurement or a model product.

## Run it

```bash
cp .env.example .env
docker compose up --build -d --wait
```

Open **http://127.0.0.1:8000**. Without Docker: `./start.sh`.

| In `.env` | Needed for |
| --- | --- |
| `EARTHDATA_USERNAME`, `EARTHDATA_PASSWORD` | heat maps and Giovanni time series. A free [NASA Earthdata](https://urs.earthdata.nasa.gov/users/new) login that has accepted the "NASA GESDISC DATA ARCHIVE" application |
| `ANTHROPIC_API_KEY` | Ask Terra |

Without the login, POWER time series still work. Without the key, only the chat is off. `.env` is ignored by Git and never copied into the image.

## NASA data

| Source | Used for | What |
| --- | --- | --- |
| [Giovanni](https://giovanni.gsfc.nasa.gov/) (GES DISC) | time series | 141 variables: AIRS, GPM IMERG, TRMM (satellite); GLDAS, FLDAS, MERRA-2 (model) |
| [NASA POWER](https://power.larc.nasa.gov/) | time series | 213 variables: MERRA-2 weather (model); CERES sunlight and cloud (satellite) |
| [AppEEARS](https://appeears.earthdatacloud.nasa.gov/) (LP DAAC) | heat maps | 13 MODIS Terra and Aqua layers at 500 m to 1 km: surface temperature, NDVI, EVI, leaf area, FPAR, plant growth, evapotranspiration |

Model records start in 1948, satellite time series in 1984, MODIS in 2000.

## How the numbers are made

- **Place.** A town is the grid cell that contains it. A county is the box around its towns. A box is the mean of the grid cells inside it, weighted by area.
- **Trend.** A least-squares line through the yearly means of complete years, at least 10 of them. "Significant" means the p-value of the slope is below 0.05.
- **Heat map.** The mean of the good-quality composites in the period. Missing pixels stay empty.
- **Ask Terra.** A Claude model (`claude-opus-5-5`) only picks the place, period and variable. The numbers come from the same code as the chart.

## Limits

- Time series cover Romania only. Heat maps take up to 93 days and 100 square degrees.
- It runs on one laptop: sessions and job history are kept in memory and lost on restart.
- Two fetches at a time, 20,000 Giovanni calls and 300 chat questions a day.
- To host it, also set `TERRA_PUBLIC=1` and `ALLOWED_HOSTS=<your host>`; the image listens on `PORT`.

## Files

```
app.py          addresses, visitor sessions, heat-map jobs
nasa.py         AppEEARS client and the 13 heat-map layers
processing.py   quality filter, averaging, PNG and GeoTIFF
sources.py      Giovanni and POWER calls, the NASA login
fetching.py     saved grid cells (SQLite) and fetching limits
series.py       places, variables and the trend maths
chat.py         Ask Terra
static/         the page: plain JavaScript and Leaflet
variables.csv   the 354 variables     places.tsv   15,429 towns     trends.csv   ready-made trends
tests/          14 checks: .venv/bin/python -m pytest -q
```

## Credits

Data: NASA AppEEARS (LP DAAC), NASA Giovanni (GES DISC), NASA POWER (Langley Research Center). Town names: [GeoNames](https://www.geonames.org/), CC BY 4.0. Map: Leaflet, with tiles from OpenStreetMap contributors and Esri.

Use of AI: parts of the code were written with an AI coding assistant, and Ask Terra calls a Claude model.
