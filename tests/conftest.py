"""Shared pytest fixtures for the test suite."""

import contextlib
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import COOKIE_NAME, create_session_cookie, get_current_user
from app.config import settings as app_settings
from app.database import Base, get_db
from app.main import app
from app.models.user import User as UserModel

_TEST_USER = UserModel(
    id=1,
    username="testuser",
    hashed_password="x",
    is_active=True,
    tier="free",
    email_verified=False,
    is_superadmin=True,
    created_at=datetime.now(timezone.utc),
)

# ---------------------------------------------------------------------------
# In-memory SQLite engine for tests (no real PostgreSQL needed)
# ---------------------------------------------------------------------------

SQLALCHEMY_TEST_DATABASE_URL = "sqlite://"

_engine = create_engine(
    SQLALCHEMY_TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_engine)


@pytest.fixture(autouse=True)
def setup_db():
    """Create all tables before each test and drop them afterwards."""
    Base.metadata.create_all(bind=_engine)
    yield
    Base.metadata.drop_all(bind=_engine)


@pytest.fixture
def db():
    """Yield a SQLAlchemy session connected to the in-memory test database."""
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


# Startup functions that hit the real PostgreSQL — patch them to no-ops in tests.
_STARTUP_PATCHES = [
    patch("app.main._run_migrations"),
    patch("app.main._seed_settings"),
    patch("app.main._recover_jobs_and_start_worker"),
    patch("app.main._maybe_enqueue_geocode_upgrade"),
]


@pytest.fixture
def client(db):
    """Return a FastAPI TestClient with the DB dependency overridden and auth bypassed."""

    def override_get_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: _TEST_USER

    with contextlib.ExitStack() as stack:
        for p in _STARTUP_PATCHES:
            stack.enter_context(p)

        # Tests use an in-memory DB; avoid starting a background thread worker (it would
        # use the real SessionLocal/Postgres). Instead, run in RQ mode and stub out the
        # Redis enqueue.
        stack.enter_context(patch.object(app_settings, "use_rq", True))
        stack.enter_context(patch.object(app_settings, "redis_url", "redis://localhost:6379/0"))
        stack.enter_context(patch("app.services.job_worker._enqueue_rq", return_value=None))

        with TestClient(app) as c:
            # Avoid race with async startup gate during tests.
            app.state.ready = True
            app.state.startup_error = None
            app.state.startup_message = "Ready"
            # Prevent the in-process worker thread from starting.
            app.state.disable_job_worker = True
            # Provide a signed session cookie so the auth gate lets requests through.
            c.cookies.set(COOKIE_NAME, create_session_cookie(1))
            yield c

    app.dependency_overrides.clear()
