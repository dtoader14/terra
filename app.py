import os
import secrets
import threading
import time
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, model_validator
from starlette.middleware.trustedhost import TrustedHostMiddleware

import fetching
import series
import sources
from nasa import VARIABLES, NasaClient, NasaError, available, task_payload
from processing import process_bundle

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
PUBLIC = os.environ.get("TERRA_PUBLIC") == "1"
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)


def scheme(request):
    forwarded = request.headers.get("x-forwarded-proto", "").split(",")[0].strip()
    return forwarded if PUBLIC and forwarded in ("http", "https") else request.url.scheme


def allowed_hosts(extra):
    return ["localhost", "127.0.0.1", "testserver"] + [host.strip() for host in extra.split(",") if host.strip()]


app = FastAPI(title="Earth Data Explorer", docs_url=None, redoc_url=None)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts(os.environ.get("ALLOWED_HOSTS", "")))
sessions, jobs = {}, {}
lock = threading.Lock()


@app.middleware("http")
async def local_guard(request: Request, call_next):
    if request.method in ("POST", "DELETE", "PUT"):
        if request.headers.get("x-earth-request") != "1":
            return JSONResponse({"detail": "Same-origin app request required."}, status_code=403)
        origin = request.headers.get("origin")
        if origin and origin != f"{scheme(request)}://{request.headers.get('host')}":
            return JSONResponse({"detail": "Cross-origin requests are disabled."}, status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["X-Frame-Options"] = "DENY"
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(NasaError)
async def nasa_error(request, exc):
    return JSONResponse({"detail": str(exc)}, status_code=502)


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    # Do not echo input values (including passwords or API tokens) in errors.
    return JSONResponse({"detail": [{"loc": error["loc"], "msg": error["msg"]} for error in exc.errors()]}, status_code=422)


class Query(BaseModel):
    variable: str
    start: date
    end: date
    bbox: tuple[float, float, float, float]

    @model_validator(mode="after")
    def validate_query(self):
        import math
        if self.variable not in VARIABLES:
            raise ValueError("Choose one of the listed variables.")
        w, s, e, n = self.bbox
        if not all(math.isfinite(x) for x in self.bbox) or not (-180 <= w < e <= 180 and -85 <= s < n <= 85):
            raise ValueError("Use west < east and south < north; latitude must be within ±85°. Split regions crossing the date line.")
        if (e - w) * (n - s) > 100 or e - w > 20 or n - s > 20:
            raise ValueError("Choose a region up to 100 square degrees, at most 20° per side.")
        if e - w < .01 or n - s < .01:
            raise ValueError("Each side must span at least 0.01°.")
        if self.start > self.end or (self.end - self.start).days > 92:
            raise ValueError("Select an ordered date range of at most 93 inclusive days.")
        if self.start < date(2000, 2, 24) or self.end > date.today():
            raise ValueError("Use dates between 24 February 2000 and today. Recent observations may not yet be published.")
        if all(variable["id"] != self.variable for variable in available(self.start, self.end)):
            variable = VARIABLES[self.variable]
            raise ValueError(f"{variable['label']} has no data in this period. NASA's record runs from {variable['first']} to {variable['last'] or 'today'}.")
        return self


def session(request, required=True):
    sid = request.cookies.get("earth_session")
    value = sessions.get(sid)
    if required and value is None:
        raise HTTPException(401, "Your browsing session has expired. Reload the explorer to continue.")
    return sid, value


READY = {"state": "ready", "available": True, "message": "NASA observations are available. Choose your selection to create a heat map."}


NO_LOGIN = "Heat maps need the NASA login. Set EARTHDATA_USERNAME and EARTHDATA_PASSWORD on the server."


def provider():
    return dict(READY) if sources.has_login() else {"state": "unconfigured", "available": False, "message": NO_LOGIN}


@app.get("/api/config")
def config(request: Request, response: Response):
    sid, current = session(request, False)
    if current is None:
        sid = secrets.token_urlsafe(32)
        sessions[sid] = {"created": time.time()}
        response.set_cookie("earth_session", sid, httponly=True, samesite="strict", secure=scheme(request) == "https", path="/")
    return {"variables": VARIABLES, "provider": provider()}


@app.get("/api/health")
def health():
    return {"status": "ok"}


def public_job(job):
    return {key: value for key, value in job.items() if key not in ("owner", "directory")}


def worker(job_id, query, token):
    job = jobs[job_id]
    def update(message):
        job["message"] = message
    try:
        client = NasaClient(token)
        catalog = client.request("GET", f"/product/{VARIABLES[query.variable]['product']}")
        for layer in (VARIABLES[query.variable]["layer"], VARIABLES[query.variable]["qa"]):
            if layer not in catalog or not catalog[layer].get("Available"):
                raise NasaError("The selected NASA layer is currently unavailable.")
        task = client.request("POST", "/task", json=task_payload(query))
        job["task_id"] = task["task_id"]
        job["status"] = "pending"
        for _ in range(720):
            task = client.request("GET", f"/task/{job['task_id']}")
            status = task.get("status")
            if status == "error":
                raise NasaError("NASA could not process this extraction. Review it in AppEEARS or try a smaller selection.")
            if status == "done":
                if task.get("summary", {}).get("empty"):
                    raise NasaError("NASA found no observations for this selection. Try a different region or date range.")
                break
            job["status"] = "processing" if status == "processing" else "pending"
            update("NASA is processing your area extraction." if status == "processing" else "Queued at NASA. Small requests usually finish sooner; keep the server running.")
            time.sleep(10)
        else:
            raise NasaError("NASA is still processing after two hours. Your task remains available in AppEEARS; its ID is shown below.")
        job["status"] = "rendering"
        directory = DATA / job_id
        directory.mkdir(exist_ok=True)
        job["result"] = process_bundle(client, job, directory, update)
        job["status"] = "done"
        update("Heat map ready. Only good-quality satellite observations are displayed.")
    except NasaError as exc:
        if getattr(exc, "authentication", False):
            sources.forget("appeears")
            job["status"], job["message"] = "error", "NASA refused the server's login. Please try again shortly."
        else:
            job["status"], job["message"] = "error", str(exc)
    except Exception:
        job["status"], job["message"] = "error", "The extraction could not be rendered. Retry or inspect the NASA task in AppEEARS. No replacement data were generated."


@app.post("/api/jobs", status_code=202)
def create_job(query: Query, request: Request):
    sid, _ = session(request)
    if not sources.has_login():
        raise HTTPException(503, NO_LOGIN)
    token = sources.appeears_token()
    with lock:
        if sum(job["status"] not in ("done", "error") for job in jobs.values()) >= 2:
            raise HTTPException(429, "Two extractions are already running. Wait for one to finish.")
        job_id = secrets.token_hex(16)
        job = {"id": job_id, "owner": sid, "query": query.model_dump(mode="json"), "variable_info": VARIABLES[query.variable], "status": "submitting", "message": "Checking NASA's catalog and submitting your extraction…", "task_id": None}
        jobs[job_id] = job
    threading.Thread(target=worker, args=(job_id, query, token), daemon=True).start()
    return public_job(job)


@app.get("/api/jobs")
def list_jobs(request: Request):
    sid, _ = session(request)
    return [public_job(job) for job in list(jobs.values()) if job["owner"] == sid]


def owned_job(job_id, request):
    sid, _ = session(request)
    job = jobs.get(job_id)
    if not job or job["owner"] != sid:
        raise HTTPException(404, "Extraction not found in this session.")
    return job


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str, request: Request):
    return public_job(owned_job(job_id, request))


@app.get("/api/jobs/{job_id}/files/{name}")
def get_file(job_id: str, name: str, request: Request, download: bool = False):
    job = owned_job(job_id, request)
    if job["status"] != "done":
        raise HTTPException(409, "The result is not ready.")
    allowed = {"mean.png", "mean.tif", "count.tif", "metadata.json"} | {frame["file"] for frame in job["result"]["frames"]}
    if name not in allowed:
        raise HTTPException(404, "File not found.")
    return FileResponse(DATA / job_id / name, filename=f"earth-{job['query']['variable']}-{name}" if download else None)


@app.get("/api/variables")
def variables(start: date | None = None, end: date | None = None):
    if start and end and start > end:
        raise HTTPException(400, "The start date is after the end date.")
    return {"variables": available(start, end), "total": len(VARIABLES)}


@app.get("/api/series/variables")
def series_variables(bbox: str | None = None, place: str | None = None, start: str | None = None, end: str | None = None):
    return fetching.available(bbox, place, start, end)


@app.get("/api/places")
def places(q: str = "", limit: int = 10):
    return {"places": series.find_places(q, limit)}


@app.get("/api/series")
def get_series(response: Response, variable: str, bbox: str | None = None, place: str | None = None, start: str | None = None, end: str | None = None):
    response.status_code, body = fetching.answer(variable, bbox, place, start, end)
    return body


app.mount("/", StaticFiles(directory=ROOT / "static", html=True), name="static")
