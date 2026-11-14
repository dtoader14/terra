import os
import secrets
import threading
import time
from datetime import date, datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, SecretStr, model_validator
from starlette.middleware.trustedhost import TrustedHostMiddleware

import fetching
import series
import sources
from nasa import VARIABLES, NasaClient, NasaError, available, task_payload
from processing import process_bundle
from platform_settings import Settings, utc_now

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")  # the NASA login when running without Docker; Compose passes it in otherwise
PUBLIC = os.environ.get("TERRA_PUBLIC") == "1"  # on a public link the admin pages are switched off
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)


def scheme(request):
    """A host's HTTPS proxy talks plain http to this server; on a public link, believe the scheme it reports."""
    forwarded = request.headers.get("x-forwarded-proto", "").split(",")[0].strip()
    return forwarded if PUBLIC and forwarded in ("http", "https") else request.url.scheme


def allowed_hosts(extra):
    return ["localhost", "127.0.0.1", "testserver"] + [host.strip() for host in extra.split(",") if host.strip()]


app = FastAPI(title="Earth Data Explorer", docs_url=None, redoc_url=None)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts(os.environ.get("ALLOWED_HOSTS", "")))
settings = Settings(DATA / "platform-settings.json")
sessions, jobs, admin_sessions = {}, {}, {}
login_failures = []
lock = threading.Lock()


@app.middleware("http")
async def local_guard(request: Request, call_next):
    if PUBLIC and (request.url.path == "/admin" or request.url.path.startswith("/api/admin")):
        return JSONResponse({"detail": "Not found."}, status_code=404)
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


class AdminPassword(BaseModel):
    password: SecretStr


class TokenSettings(BaseModel):
    token: SecretStr
    expires_at: datetime | None = None


class ConnectionState(BaseModel):
    enabled: bool


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


def provider():
    """Heat-map availability: the login from the environment when there is one, else the token saved in /admin."""
    return dict(READY) if sources.has_login() else settings.status()


@app.get("/api/config")
def config(request: Request, response: Response):
    sid, current = session(request, False)
    if current is None:
        sid = secrets.token_urlsafe(32)
        sessions[sid] = {"created": time.time()}
        response.set_cookie("earth_session", sid, httponly=True, samesite="strict", secure=scheme(request) == "https", path="/")
    return {"variables": VARIABLES, "provider": provider(), "series_login": sources.has_login(), "public": PUBLIC}


@app.get("/api/health")
def health():
    return {"status": "ok"}


def require_admin(request):
    sid = request.cookies.get("earth_admin")
    if admin_sessions.get(sid, 0) <= time.time():
        admin_sessions.pop(sid, None)
        raise HTTPException(401, "Sign in to the admin dashboard to continue.")
    return sid


def admin_cookie(request, response):
    admin_sessions.pop(request.cookies.get("earth_admin"), None)
    sid = secrets.token_urlsafe(32)
    admin_sessions[sid] = time.time() + 8 * 3600
    response.set_cookie("earth_admin", sid, httponly=True, samesite="strict", secure=scheme(request) == "https", max_age=8 * 3600, path="/")


@app.get("/api/admin/session")
def admin_session(request: Request):
    return {"setup_required": not settings.has_admin(), "authenticated": admin_sessions.get(request.cookies.get("earth_admin"), 0) > time.time()}


@app.post("/api/admin/setup")
def admin_setup(body: AdminPassword, request: Request, response: Response):
    password = body.password.get_secret_value()
    if not 12 <= len(password) <= 256:
        raise HTTPException(400, "Choose an administrator password with 12–256 characters.")
    if not settings.create_admin(password):
        raise HTTPException(409, "An administrator is already configured. Sign in instead.")
    admin_cookie(request, response)
    return {"authenticated": True}


@app.post("/api/admin/login")
def admin_login(body: AdminPassword, request: Request, response: Response):
    password = body.password.get_secret_value()
    with lock:
        login_failures[:] = [stamp for stamp in login_failures if stamp > time.time() - 900]
        if len(login_failures) >= 10:
            raise HTTPException(429, "Too many sign-in attempts. Wait 15 minutes before trying again.")
        if len(password) > 256 or not settings.authenticate(password):
            login_failures.append(time.time())
            raise HTTPException(401, "Incorrect administrator password.")
        login_failures.clear()
    admin_cookie(request, response)
    return {"authenticated": True}


@app.post("/api/admin/logout")
def admin_logout(request: Request, response: Response):
    admin_sessions.pop(request.cookies.get("earth_admin"), None)
    response.delete_cookie("earth_admin")
    return {"authenticated": False}


@app.get("/api/admin/overview")
def admin_overview(request: Request):
    require_admin(request)
    all_jobs = list(jobs.values())
    return {"connection": settings.status(admin=True), "metrics": {"total": len(all_jobs), "running": sum(job["status"] not in ("done", "error") for job in all_jobs), "completed": sum(job["status"] == "done" for job in all_jobs), "failed": sum(job["status"] == "error" for job in all_jobs)}, "activity": [{"variable": job["variable_info"]["label"], "start": job["query"]["start"], "end": job["query"]["end"], "status": job["status"]} for job in reversed(all_jobs[-20:])]}


@app.put("/api/admin/nasa")
def save_nasa(body: TokenSettings, request: Request):
    require_admin(request)
    token = body.token.get_secret_value().strip()
    if not token or len(token) > 4096 or any(char.isspace() for char in token):
        raise HTTPException(400, "Enter the AppEEARS token only, without spaces or the Bearer prefix.")
    expiration = body.expires_at
    if expiration and (expiration.tzinfo is None or expiration <= datetime.now(timezone.utc)):
        raise HTTPException(400, "Expiration must be a future date and time with a timezone.")
    # Verify first: a rejected replacement must never overwrite a working token.
    NasaClient(token).request("GET", "/task", params={"limit": 1})
    settings.save_token(token, expiration.isoformat() if expiration else None)
    return settings.status(admin=True)


@app.post("/api/admin/nasa/verify")
def verify_nasa(request: Request):
    require_admin(request)
    token = settings.token()
    if not token:
        raise HTTPException(400, "Save a NASA token first.")
    try:
        NasaClient(token).request("GET", "/task", params={"limit": 1})
    except NasaError as exc:
        if getattr(exc, "authentication", False):
            settings.mark_invalid(token)
        raise
    with settings.lock:
        if settings.token() == token:
            settings.update_connection(verified=True, verified_at=utc_now(), error=None)
    return settings.status(admin=True)


@app.put("/api/admin/nasa/state")
def set_nasa_state(body: ConnectionState, request: Request):
    require_admin(request)
    if not settings.token():
        raise HTTPException(400, "Save a NASA token first.")
    settings.update_connection(enabled=body.enabled)
    return settings.status(admin=True)


@app.delete("/api/admin/nasa")
def remove_nasa(request: Request):
    require_admin(request)
    settings.clear_token()
    return settings.status(admin=True)


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
            if sources.has_login():
                sources.forget("appeears")  # the next heat map logs in again
            else:
                settings.mark_invalid(token)
            job["status"], job["message"] = "error", "The platform's data connection needs an administrator update. Please retry after it has been renewed."
        else:
            job["status"], job["message"] = "error", str(exc)
    except Exception:
        job["status"], job["message"] = "error", "The extraction could not be rendered. Retry or inspect the NASA task in AppEEARS. No replacement data were generated."


@app.post("/api/jobs", status_code=202)
def create_job(query: Query, request: Request):
    sid, _ = session(request)
    if sources.has_login():
        token = sources.appeears_token()
    else:
        with settings.lock:
            status = settings.status()
            if not status["available"]:
                raise HTTPException(503, status["message"])
            token = settings.token()
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
    """The heat-map variables that have data in the period; all of them when no period is given."""
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


@app.get("/admin", include_in_schema=False)
def admin_page():
    return FileResponse(ROOT / "static" / "admin.html")


app.mount("/", StaticFiles(directory=ROOT / "static", html=True), name="static")
