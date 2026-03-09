from contextlib import asynccontextmanager

from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.ui.routes import router as ui_router


def _run_migrations() -> None:
    """Apply any pending Alembic migrations (idempotent)."""
    cfg = AlembicConfig("alembic.ini")
    alembic_command.upgrade(cfg, "head")


def _seed_settings() -> None:
    """Seed default app settings from environment variables on first run.

    Values already stored in the DB are never overwritten — the DB wins.
    """
    from app.config import settings
    from app.crud import seed_defaults
    from app.database import SessionLocal

    defaults = {
        "google_search_enabled": "true" if settings.google_search_enabled else "false",
        "google_daily_quota": str(settings.google_daily_quota),
    }
    with SessionLocal() as db:
        seed_defaults(db, defaults)


@asynccontextmanager
async def lifespan(app: FastAPI):
    _run_migrations()
    _seed_settings()
    yield


app = FastAPI(
    title="Zefix Analyzer",
    description=(
        "Internal GUI tool for analysing Swiss registered companies via the Zefix API, "
        "Google Search enrichment, and manual notes stored in PostgreSQL."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.include_router(ui_router)


@app.get("/health", tags=["health"])
def health():
    return {"status": "ok"}
