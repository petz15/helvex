import asyncio
import os
import pathlib
import sys
import time
from contextlib import asynccontextmanager

# ── Python 3.12 compatibility patch ───────────────────────────────────────────
# pydantic.v1 (bundled inside pydantic v2) calls ForwardRef._evaluate() without
# the `recursive_guard` keyword argument required by Python 3.12.  Patch it once
# at startup so any library that uses pydantic.v1 (e.g. spaCy) works correctly.
if sys.version_info >= (3, 12):
    from typing import ForwardRef
    _orig_evaluate = ForwardRef._evaluate

    def _patched_evaluate(self, globalns, localns, *args, **kwargs):
        kwargs.setdefault("recursive_guard", frozenset())
        return _orig_evaluate(self, globalns, localns, *args, **kwargs)

    ForwardRef._evaluate = _patched_evaluate  # type: ignore[method-assign]
# ──────────────────────────────────────────────────────────────────────────────

from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
from fastapi import Depends, FastAPI, Form, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import Session

from app import crud
from app.auth import COOKIE_NAME, check_login_rate_limit, create_session_cookie, decode_session_cookie
from app.config import settings
from app.database import Base, engine, get_db
from app.services.job_worker import kick_job_worker
from app.services.scoring import get_default_scoring_config
from app.api.routes import companies_router, notes_router, jobs_router, map_router, settings_router

# Paths that do NOT require authentication
_PUBLIC_PREFIXES = ("/static", "/login", "/health", "/metadata")
_PUBLIC_EXACT = {"/login", "/logout", "/health", "/metadata", "/"}

_REPO_ROOT = pathlib.Path(__file__).parent.parent


def _read_version_info() -> tuple[str, str, str]:
    try:
        version = (_REPO_ROOT / "VERSION").read_text().strip()
    except FileNotFoundError:
        version = "dev"

    build_date = (os.getenv("APP_BUILD_DATE") or "").strip()
    if not build_date:
        try:
            build_date = (_REPO_ROOT / "BUILD_DATE").read_text().strip()
        except FileNotFoundError:
            build_date = "unknown"

    build_git_sha = (os.getenv("APP_GIT_SHA") or "unknown").strip() or "unknown"
    return version, build_date, build_git_sha


APP_VERSION, BUILD_DATE, BUILD_GIT_SHA = _read_version_info()


# ── Startup helpers ───────────────────────────────────────────────────────────

def _database_has_tables() -> bool:
    with engine.connect() as conn:
        return bool(sa_inspect(conn).get_table_names())


def _run_migrations(app_state) -> None:
    cfg = AlembicConfig("alembic.ini")

    app_state.startup_message = "Connecting to database…"
    try:
        has_tables = _database_has_tables()
    except Exception as exc:
        raise RuntimeError(f"Cannot connect to database: {exc}") from exc

    if not has_tables:
        app_state.startup_message = "Empty database — creating schema…"
        try:
            Base.metadata.create_all(engine)
        except Exception as exc:
            raise RuntimeError(f"Failed to create schema: {exc}") from exc

        app_state.startup_message = "Schema created — stamping Alembic version…"
        try:
            alembic_command.stamp(cfg, "head")
        except Exception as exc:
            raise RuntimeError(f"Failed to stamp Alembic version: {exc}") from exc

        app_state.startup_message = "Database initialised ✓"
        return

    app_state.startup_message = "Applying pending migrations…"
    try:
        alembic_command.upgrade(cfg, "head")
    except Exception as exc:
        raise RuntimeError(f"Database migration failed: {exc}") from exc

    app_state.startup_message = "Database schema is up to date ✓"


def _seed_settings(app_state) -> None:
    app_state.startup_message = "Seeding application settings…"
    from app.crud import seed_defaults
    from app.database import SessionLocal

    defaults = {
        "google_search_enabled": "true" if settings.google_search_enabled else "false",
        "google_daily_quota": str(settings.google_daily_quota),
    }
    defaults.update(get_default_scoring_config())
    try:
        with SessionLocal() as db:
            seed_defaults(db, defaults)
    except Exception as exc:
        raise RuntimeError(f"Failed to seed settings: {exc}") from exc


def _maybe_enqueue_geocode_upgrade(app, app_state) -> None:
    from app.crud import create_event, create_job, get_setting, list_jobs
    from app.database import SessionLocal

    queued_job = False
    with SessionLocal() as db:
        if get_setting(db, "geocoding_building_level_done", "false") == "true":
            return

        already_queued = any(
            j.job_type == "re_geocode" and j.status in ("queued", "running", "paused")
            for j in list_jobs(db, limit=50)
        )
        if already_queued:
            return

        job = create_job(
            db,
            job_type="re_geocode",
            label="One-time re-geocode — upgrade to building-level coordinates",
            params={},
        )
        create_event(db, job_id=job.id, level="info", message="Auto-queued by startup")
        queued_job = True

    if queued_job:
        kick_job_worker(app)
    app_state.startup_message = "Queued one-time geocoding upgrade"


def _recover_jobs_and_start_worker(app, app_state) -> None:
    from app.crud import list_active_jobs, requeue_interrupted_jobs
    from app.database import SessionLocal

    app_state.startup_message = "Recovering background jobs…"
    try:
        with SessionLocal() as db:
            recovered = requeue_interrupted_jobs(db)
            active_count = len(list_active_jobs(db))
        kick_job_worker(app)
        app_state.startup_message = (
            f"Background jobs ready — recovered {recovered}, active {active_count}"
        )
    except Exception as exc:
        raise RuntimeError(f"Failed to recover background jobs: {exc}") from exc


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.ready = False
    app.state.startup_message = "Initialising…"
    app.state.startup_error = None
    app.state.startup_started_at = time.time()
    app.state.collection_task = None
    app.state.job_worker_running = False

    async def _startup() -> None:
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, _run_migrations, app.state)
            await loop.run_in_executor(None, _seed_settings, app.state)
            await loop.run_in_executor(None, _recover_jobs_and_start_worker, app, app.state)
            await loop.run_in_executor(None, _maybe_enqueue_geocode_upgrade, app, app.state)
            app.state.ready = True
            app.state.startup_message = "Ready"
        except Exception as exc:  # noqa: BLE001
            app.state.startup_error = str(exc)

    asyncio.create_task(_startup())
    yield


# ── Application ───────────────────────────────────────────────────────────────

app = FastAPI(
    title="Helvex",
    description=(
        "B2B company intelligence platform for Swiss registered companies via the Zefix API, "
        "Google Search enrichment, and AI scoring."
    ),
    version=APP_VERSION,
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.include_router(companies_router, prefix="/api/v1")
app.include_router(notes_router, prefix="/api/v1")
app.include_router(jobs_router, prefix="/api/v1")
app.include_router(map_router, prefix="/api/v1")
app.include_router(settings_router, prefix="/api/v1")


# ── Auth routes (raw HTML — no Jinja2) ───────────────────────────────────────

_LOGIN_HTML = """\
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Helvex — Sign in</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; }}
    body {{
      margin: 0; min-height: 100vh; display: flex; align-items: center; justify-content: center;
      font-family: ui-sans-serif, system-ui, sans-serif;
      background: #f1f5f9; color: #1e293b;
    }}
    .card {{
      background: #fff; border-radius: 12px; box-shadow: 0 4px 24px rgba(0,0,0,.10);
      padding: 2.5rem 2rem; width: 100%; max-width: 380px;
    }}
    .brand {{ font-size: 1.5rem; font-weight: 700; color: #1d4ed8; margin: 0 0 1.5rem; text-align: center; }}
    label {{ display: block; font-size: .875rem; font-weight: 500; margin-bottom: .25rem; color: #374151; }}
    input {{
      width: 100%; padding: .625rem .75rem; border: 1px solid #d1d5db; border-radius: 8px;
      font-size: .95rem; outline: none; transition: border-color .15s;
    }}
    input:focus {{ border-color: #3b82f6; box-shadow: 0 0 0 3px rgba(59,130,246,.15); }}
    .field {{ margin-bottom: 1rem; }}
    .btn {{
      width: 100%; padding: .7rem; border: none; border-radius: 8px;
      background: #1d4ed8; color: #fff; font-size: 1rem; font-weight: 600;
      cursor: pointer; margin-top: .5rem; transition: background .15s;
    }}
    .btn:hover {{ background: #1e40af; }}
    .error {{
      background: #fef2f2; border: 1px solid #fca5a5; color: #b91c1c;
      border-radius: 8px; padding: .625rem .875rem; font-size: .875rem; margin-bottom: 1rem;
    }}
  </style>
</head>
<body>
  <div class="card">
    <p class="brand">Helvex</p>
    {error_html}
    <form method="post" action="/login">
      <input type="hidden" name="next" value="{next}">
      <div class="field">
        <label for="username">Username</label>
        <input id="username" name="username" type="text" autocomplete="username" autofocus required>
      </div>
      <div class="field">
        <label for="password">Password</label>
        <input id="password" name="password" type="password" autocomplete="current-password" required>
      </div>
      <button class="btn" type="submit">Sign in</button>
    </form>
  </div>
</body>
</html>
"""


@app.get("/login", response_class=HTMLResponse, include_in_schema=False)
def login_page(next: str = Query("/app/dashboard"), error: str | None = Query(None)):
    error_html = f'<div class="error">{error}</div>' if error else ""
    return HTMLResponse(_LOGIN_HTML.format(next=next, error_html=error_html))


@app.post("/login", include_in_schema=False)
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form("/app/dashboard"),
    db: Session = Depends(get_db),
):
    ip = request.client.host if request.client else "unknown"
    if not check_login_rate_limit(ip):
        error_html = '<div class="error">Too many login attempts. Try again in 15 minutes.</div>'
        return HTMLResponse(_LOGIN_HTML.format(next=next, error_html=error_html), status_code=429)

    user = crud.authenticate(db, username=username, password=password)
    if not user:
        error_html = '<div class="error">Invalid username or password.</div>'
        return HTMLResponse(_LOGIN_HTML.format(next=next, error_html=error_html), status_code=401)

    safe_next = next if next.startswith("/") and not next.startswith("//") else "/app/dashboard"
    response = RedirectResponse(url=safe_next, status_code=status.HTTP_302_FOUND)
    response.set_cookie(
        key=COOKIE_NAME,
        value=create_session_cookie(user.id),
        httponly=True,
        samesite="lax",
        secure=False,
        max_age=8 * 3600,
    )
    return response


@app.get("/logout", include_in_schema=False)
def logout():
    response = RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)
    response.delete_cookie(key=COOKIE_NAME)
    return response


# ── Startup gate middleware ───────────────────────────────────────────────────

_LOADING_HTML = """\
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="2">
  <title>Helvex — Starting</title>
  <style>
    body {{ font-family: ui-sans-serif, system-ui, sans-serif; margin: 0; background: #f1f5f9; color: #1e293b; }}
    .box {{ max-width: 520px; margin: 6rem auto; text-align: center; background: #fff; border-radius: 12px; padding: 2.5rem; box-shadow: 0 4px 24px rgba(0,0,0,.08); }}
    .spinner {{ width: 48px; height: 48px; border: 5px solid #dbeafe; border-top-color: #1d4ed8; border-radius: 50%; animation: spin 0.8s linear infinite; margin: 0 auto 1.5rem; }}
    @keyframes spin {{ to {{ transform: rotate(360deg); }} }}
    h2 {{ margin: 0 0 .5rem; font-size: 1.25rem; }}
    .msg {{ color: #475569; font-weight: 500; }}
    .elapsed {{ color: #94a3b8; font-size: .8rem; margin-top: 1rem; }}
  </style>
</head>
<body>
  <div class="box">
    <div class="spinner"></div>
    <h2>Starting up…</h2>
    <p class="msg">{message}</p>
    <p class="elapsed">Elapsed: {elapsed}s · refreshes every 2s</p>
  </div>
</body>
</html>
"""

_ERROR_HTML = """\
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Helvex — Startup failed</title>
  <style>
    body {{ font-family: ui-sans-serif, system-ui, sans-serif; margin: 0; background: #f1f5f9; color: #1e293b; }}
    .box {{ max-width: 600px; margin: 4rem auto; background: #fff; border-radius: 12px; padding: 2rem; box-shadow: 0 4px 24px rgba(0,0,0,.08); border: 2px solid #fca5a5; }}
    h2 {{ color: #b91c1c; margin-top: 0; }}
    pre {{ background: #fef2f2; border: 1px solid #fca5a5; color: #7f1d1d; padding: 1rem; border-radius: 8px; white-space: pre-wrap; word-break: break-word; font-size: .85rem; }}
    .elapsed {{ color: #94a3b8; font-size: .8rem; }}
  </style>
</head>
<body>
  <div class="box">
    <h2>Startup failed</h2>
    <p>Fix the error below and restart the container.</p>
    <pre>{error}</pre>
    <p class="elapsed">Elapsed before failure: {elapsed}s</p>
  </div>
</body>
</html>
"""


@app.middleware("http")
async def startup_gate(request: Request, call_next):
    path = request.url.path
    if path.startswith("/static") or path == "/health":
        return await call_next(request)

    elapsed = int(time.time() - getattr(app.state, "startup_started_at", time.time()))
    error = getattr(app.state, "startup_error", None)

    if error:
        return HTMLResponse(_ERROR_HTML.format(error=error, elapsed=elapsed), status_code=500)

    if not getattr(app.state, "ready", False):
        message = getattr(app.state, "startup_message", "Initialising…")
        return HTMLResponse(_LOADING_HTML.format(message=message, elapsed=elapsed), status_code=503)

    return await call_next(request)


@app.middleware("http")
async def auth_gate(request: Request, call_next):
    """Redirect unauthenticated requests to /login for all protected paths."""
    path = request.url.path

    if path in _PUBLIC_EXACT or any(path.startswith(p) for p in _PUBLIC_PREFIXES):
        return await call_next(request)

    token = request.cookies.get(COOKIE_NAME)
    if token and decode_session_cookie(token) is not None:
        return await call_next(request)

    from urllib.parse import quote
    next_url = quote(path, safe="")
    return RedirectResponse(url=f"/login?next={next_url}", status_code=302)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    return response


@app.get("/health", tags=["health"])
def health():
    ready = getattr(app.state, "ready", False)
    error = getattr(app.state, "startup_error", None)
    elapsed = int(time.time() - getattr(app.state, "startup_started_at", time.time()))
    message = getattr(app.state, "startup_message", "")
    base = {"version": APP_VERSION, "build_date": BUILD_DATE, "git_sha": BUILD_GIT_SHA}
    if error:
        return {**base, "status": "error", "detail": error, "elapsed_s": elapsed}
    return {**base, "status": "ok" if ready else "starting", "step": message, "elapsed_s": elapsed}


@app.get("/metadata", tags=["metadata"])
def metadata():
    return {
        "version": APP_VERSION,
        "build_date": BUILD_DATE,
        "git_sha": BUILD_GIT_SHA,
        "ready": bool(getattr(app.state, "ready", False)),
    }
