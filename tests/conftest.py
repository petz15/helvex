"""Shared pytest fixtures for the test suite."""

import contextlib
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import COOKIE_NAME, create_session_cookie
from app.database import Base, get_db
from app.main import app

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

    with contextlib.ExitStack() as stack:
        for p in _STARTUP_PATCHES:
            stack.enter_context(p)
        with TestClient(app) as c:
            # Provide a signed session cookie so the auth gate lets requests through.
            c.cookies.set(COOKIE_NAME, create_session_cookie(1))
            yield c

    app.dependency_overrides.clear()
