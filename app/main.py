import asyncio
import html as _html
import logging
import os
import pathlib
import sys
import time
import traceback
from contextlib import asynccontextmanager

# Configure logging. We attach a stdout handler directly to the "app" logger
# rather than the root logger, because uvicorn calls logging.config.dictConfig()
# during server init which resets the root logger (level=WARNING, no handlers).
# By owning the "app" logger explicitly we survive uvicorn's config pass.
_LOG_LEVEL_NAME = (os.getenv("LOG_LEVEL") or "INFO").upper().strip()
_LOG_LEVEL = getattr(logging, _LOG_LEVEL_NAME, logging.INFO)

_app_logger = logging.getLogger("app")
_app_logger.handlers.clear()
_app_logger.setLevel(_LOG_LEVEL)
_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s - %(message)s"))
_app_logger.addHandler(_handler)
_app_logger.propagate = False

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
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import Session

from app import crud
from app.auth import (
    COOKIE_NAME,
    _user_id_from_request,
    create_session_cookie,
    get_client_ip,
    is_login_allowed,
    record_login_failure,
)
from app.config import settings
from app.database import Base, engine, get_db
from app.services.job_worker import kick_job_worker
from app.services.scoring import get_default_scoring_config
from app.api.routes import admin_router, auth_router, billing_router, clusters_router, companies_router, invites_router, jobs_router, map_router, notes_router, orgs_router, settings_router, views_router, workspace_router

# Paths that do NOT require authentication
_PUBLIC_PREFIXES = (
    "/static",
    "/health",
    "/api/v1/auth",
    "/api/v1/invites/preview",
    "/api/v1/companies/demo",
    "/api/v1/billing/webhooks",
)
_PUBLIC_EXACT = {"/", "/logout", "/health"}

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

logger = logging.getLogger(__name__)
print(
    f"boot.logging level={_LOG_LEVEL_NAME} api_request_logging_enabled={getattr(settings, 'api_request_logging_enabled', True)}",
    flush=True,
)


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

    app_state.startup_message = "Seeding multilingual boilerplate patterns…"
    try:
        from app.services.boilerplate_analysis import seed_multilang_boilerplate
        with SessionLocal() as db:
            result = seed_multilang_boilerplate(db)
        _app_logger.info("boilerplate seed: inserted=%d skipped=%d", result["inserted"], result["skipped"])
    except Exception as exc:
        _app_logger.warning("boilerplate seed failed (non-fatal): %s", exc)

    app_state.startup_message = "Loading NOGA hierarchy cache…"
    try:
        from app.crud.company import _load_noga_hierarchy
        _load_noga_hierarchy()
    except Exception as exc:
        _app_logger.warning("NOGA hierarchy cache failed (non-fatal): %s", exc)


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


def _start_nightly_billing_scheduler(app) -> None:
    """Start a background daemon thread that runs the billing_renewal job each night.

    Wakes up every 10 minutes.  Once the clock enters the 01:00–01:59 window
    in Europe/Zurich time, enqueues a billing_renewal job if one has not already
    run today (checked via the job history).
    """
    import threading
    import time

    def _scheduler_loop():
        while True:
            time.sleep(600)
            try:
                _maybe_enqueue_billing_renewal(app)
            except Exception:  # noqa: BLE001
                pass

    t = threading.Thread(target=_scheduler_loop, daemon=True, name="billing-renewal-scheduler")
    t.start()


def _maybe_enqueue_billing_renewal(app) -> None:
    """Enqueue billing_renewal at 01:00–01:59 Zurich time if not already run today."""
    from datetime import datetime, timedelta, timezone

    try:
        from zoneinfo import ZoneInfo
        tz_zurich = ZoneInfo("Europe/Zurich")
    except Exception:  # noqa: BLE001
        tz_zurich = timezone(timedelta(hours=1))

    now_local = datetime.now(tz=tz_zurich)
    if now_local.hour != 1:
        return  # only run during the 01:00–01:59 window

    from app.crud import create_event, create_job, list_jobs
    from app.database import SessionLocal
    from app.services.job_worker import kick_job_worker

    with SessionLocal() as db:
        # Check if a billing_renewal job has already run or is queued today.
        today_start = datetime.now(tz=timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        recent = [
            j for j in list_jobs(db, limit=20)
            if j.job_type == "billing_renewal"
            and j.queued_at
            and j.queued_at >= today_start
        ]
        if recent:
            return

        job = create_job(
            db,
            job_type="billing_renewal",
            label="Nightly subscription billing renewal (auto)",
            params={},
        )
        create_event(db, job_id=job.id, level="info", message="Auto-queued by billing renewal scheduler")

    kick_job_worker(app)
    logger.info("billing_renewal_scheduler: enqueued billing_renewal")


def _start_nightly_shab_scheduler(app) -> None:
    """Start a background daemon thread that auto-enqueues a shab_daily job each night.

    The thread wakes up every 10 minutes and, once the clock enters the
    02:00–02:59 window in Europe/Zurich time, enqueues a shab_daily job for
    yesterday if one has not already been queued today.
    """
    import threading
    import time

    def _scheduler_loop():
        while True:
            time.sleep(600)  # check every 10 minutes
            try:
                _maybe_enqueue_shab_daily(app)
            except Exception:  # noqa: BLE001
                pass  # never crash the scheduler thread

    t = threading.Thread(target=_scheduler_loop, daemon=True, name="shab-nightly-scheduler")
    t.start()


def _maybe_enqueue_shab_daily(app) -> None:
    """Enqueue a shab_daily job for yesterday if it's 02:00–02:59 local time and not done yet."""
    from datetime import datetime, timedelta, timezone

    # Determine current hour in Europe/Zurich
    try:
        from zoneinfo import ZoneInfo
        tz_zurich = ZoneInfo("Europe/Zurich")
    except Exception:  # noqa: BLE001
        # Fallback: UTC+1 (CET) — close enough for scheduling purposes
        tz_zurich = timezone(timedelta(hours=1))

    now_local = datetime.now(tz=tz_zurich)
    if now_local.hour != 2:
        return  # only run during the 02:00–02:59 window

    from app.crud import create_event, create_job, has_shab_daily_run_today
    from app.database import SessionLocal
    from app.services.job_worker import kick_job_worker

    yesterday = (datetime.now(tz=timezone.utc) - timedelta(days=1)).date()

    with SessionLocal() as db:
        if has_shab_daily_run_today(db):
            return  # already queued or ran today

        job = create_job(
            db,
            job_type="shab_daily",
            label=f"SHAB daily import — {yesterday.isoformat()} (auto)",
            params={"date": yesterday.isoformat(), "request_delay": 0.15},
        )
        create_event(db, job_id=job.id, level="info", message="Auto-queued by nightly scheduler")

    kick_job_worker(app)
    logger.info("shab_nightly_scheduler: enqueued shab_daily for %s", yesterday)


def _warm_taxonomy_cache(app_state) -> None:
    from app.crud.company import _compute_global_taxonomy, _tax_cache_lock
    import time as _time
    import app.crud.company as _cc
    from app.database import SessionLocal
    app_state.startup_message = "Warming taxonomy cache…"
    try:
        with SessionLocal() as db:
            data = _compute_global_taxonomy(db)
        with _tax_cache_lock:
            _cc._tax_cache_data = data
            _cc._tax_cache_ts = _time.monotonic()
        app_state.startup_message = "Taxonomy cache warm"
    except Exception as exc:
        logger.warning("taxonomy cache warm failed (non-fatal): %s", exc)


def _recover_jobs_and_start_worker(app, app_state) -> None:
    from app.crud import (
        delete_old_finished_jobs,
        list_active_jobs,
        requeue_interrupted_jobs,
        requeue_recent_abandoned_jobs,
        resume_all_paused_jobs,
    )
    from app.database import SessionLocal

    app_state.startup_message = "Recovering background jobs…"
    try:
        with SessionLocal() as db:
            recovered = requeue_interrupted_jobs(db)
            recovered_abandoned = requeue_recent_abandoned_jobs(db)
            resumed = resume_all_paused_jobs(db)
            active_count = len(list_active_jobs(db))
            pruned = delete_old_finished_jobs(db, keep_days=30)
        kick_job_worker(app)
        app_state.startup_message = (
            f"Background jobs ready — recovered {recovered} interrupted, "
            f"{recovered_abandoned} abandoned, resumed {resumed}, "
            f"active {active_count}, pruned {pruned} old"
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
    app.state.disable_job_worker = bool(getattr(settings, "disable_job_worker", False))

    async def _startup() -> None:
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, _run_migrations, app.state)
            # Re-attach handler after _run_migrations, because alembic's fileConfig()
            # call in env.py resets existing loggers (disable_existing_loggers default).
            _app_logger.handlers.clear()
            _app_logger.setLevel(_LOG_LEVEL)
            _h = logging.StreamHandler(sys.stdout)
            _h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s - %(message)s"))
            _app_logger.addHandler(_h)
            _app_logger.propagate = False
            await loop.run_in_executor(None, _seed_settings, app.state)
            await loop.run_in_executor(None, _recover_jobs_and_start_worker, app, app.state)
            await loop.run_in_executor(None, _maybe_enqueue_geocode_upgrade, app, app.state)
            _start_nightly_shab_scheduler(app)
            _start_nightly_billing_scheduler(app)
            await loop.run_in_executor(None, _warm_taxonomy_cache, app.state)
            app.state.ready = True
            app.state.startup_message = "Ready"
        except Exception as exc:  # noqa: BLE001
            app.state.startup_error = str(exc)
            _app_logger.exception("boot.startup_error: %s", exc)

    asyncio.create_task(_startup())
    yield

    # Graceful shutdown: ask any in-process jobs to pause at their next
    # progress checkpoint so they are not left stuck as "running".
    from app.services.job_worker import request_shutdown
    request_shutdown()


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

# Add response compression middleware for large JSON payloads (5-10x compression typical)
app.add_middleware(GZipMiddleware, minimum_size=1000)

app.mount("/static", StaticFiles(directory="app/static"), name="static")

from prometheus_fastapi_instrumentator import Instrumentator
Instrumentator().instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)

app.include_router(admin_router, prefix="/api/v1")
app.include_router(auth_router, prefix="/api/v1")
app.include_router(clusters_router, prefix="/api/v1")
app.include_router(billing_router, prefix="/api/v1")
app.include_router(companies_router, prefix="/api/v1")
app.include_router(notes_router, prefix="/api/v1")
app.include_router(jobs_router, prefix="/api/v1")
app.include_router(map_router, prefix="/api/v1")
app.include_router(settings_router, prefix="/api/v1")
app.include_router(orgs_router, prefix="/api/v1")
app.include_router(invites_router, prefix="/api/v1")
app.include_router(views_router, prefix="/api/v1")
app.include_router(workspace_router, prefix="/api/v1")


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    print(f"UNHANDLED EXCEPTION {request.method} {request.url.path}\n{tb}", file=sys.stderr, flush=True)
    from fastapi.responses import JSONResponse
    return JSONResponse({"detail": "Internal server error"}, status_code=500)


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
        generic_error = "Service failed to start. Check server logs for details."
        return HTMLResponse(_ERROR_HTML.format(error=generic_error, elapsed=elapsed), status_code=500)

    if not getattr(app.state, "ready", False):
        message = getattr(app.state, "startup_message", "Initialising…")
        return HTMLResponse(_LOADING_HTML.format(message=message, elapsed=elapsed), status_code=503)

    return await call_next(request)


@app.middleware("http")
async def auth_gate(request: Request, call_next):
    """Enforce authentication on all protected paths.

    - Public paths pass through unconditionally.
    - Authenticated requests (cookie session OR Bearer JWT) pass through.
    - Unauthenticated API requests → 401 JSON.
    - Unauthenticated browser requests → redirect to /login.
    """
    path = request.url.path

    if path in _PUBLIC_EXACT or any(path.startswith(p) for p in _PUBLIC_PREFIXES):
        return await call_next(request)

    if _user_id_from_request(request) is not None:
        return await call_next(request)

    if path.startswith("/api/"):
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail": "Not authenticated"}, status_code=401)

    from urllib.parse import quote
    next_url = quote(path, safe="")
    return RedirectResponse(url=f"/login?next={next_url}", status_code=302)


@app.middleware("http")
async def api_request_logger(request: Request, call_next):
    """Emit concise request logs for API and diagnostics endpoints."""
    if not settings.api_request_logging_enabled:
        return await call_next(request)

    path = request.url.path
    should_log = path.startswith("/api/") or path in {"/health", "/metadata", "/docs"}
    if not should_log:
        return await call_next(request)

    started = time.perf_counter()
    client_host = request.client.host if request.client else "unknown"
    try:
        response = await call_next(request)
    except Exception:
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        logger.exception(
            "http.request_error method=%s path=%s client=%s duration_ms=%.1f",
            request.method,
            path,
            client_host,
            elapsed_ms,
        )
        raise

    elapsed_ms = (time.perf_counter() - started) * 1000.0
    logger.info(
        "http.request method=%s path=%s status=%s client=%s duration_ms=%.1f",
        request.method,
        path,
        response.status_code,
        client_host,
        elapsed_ms,
    )
    return response


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

    path = request.url.path

    # Worldline/Saferpay redirect-back endpoints are loaded *inside* the Saferpay
    # iframe hosted on a different domain.  They must be frameable so the inline
    # JS can execute `window.top.location.href = ...` to break out.
    # All other responses keep strict no-framing headers.
    if path.startswith("/api/v1/billing/webhooks/worldline/"):
        # Allow framing from any origin — these endpoints return nothing sensitive,
        # only a quick JS redirect that immediately leaves the iframe.
        # unsafe-inline is required for the inline <script> in _iframe_redirect().
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; "
            "script-src 'unsafe-inline'; "
            "frame-ancestors *;"
        )
        # Do NOT set X-Frame-Options here — CSP frame-ancestors takes precedence
        # in modern browsers, and setting DENY would conflict.
    else:
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "base-uri 'self'; "
            "object-src 'none'; "
            "form-action 'self'; "
            "script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; "
            "font-src 'self'; "
            "connect-src 'self'; "
            "frame-ancestors 'none';"
        )

    # HSTS — only meaningful over HTTPS; omit on plain HTTP dev
    forwarded_proto = request.headers.get("x-forwarded-proto", "")
    is_https = request.url.scheme == "https" or forwarded_proto.split(",")[0].strip().lower() == "https"
    if is_https:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


@app.get("/health", tags=["health"])
def health():
    from fastapi.responses import JSONResponse
    ready = getattr(app.state, "ready", False)
    error = getattr(app.state, "startup_error", None)
    if error:
        return JSONResponse({"status": "error"}, status_code=500)
    if not ready:
        return JSONResponse({"status": "starting"}, status_code=503)
    return {"status": "ok"}


@app.get("/metadata", tags=["metadata"])
def metadata():
    return {
        "version": APP_VERSION,
        "build_date": BUILD_DATE,
        "git_sha": BUILD_GIT_SHA,
        "ready": bool(getattr(app.state, "ready", False)),
    }
