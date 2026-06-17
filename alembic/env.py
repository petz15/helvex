"""Alembic environment configuration."""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool, text

import app.models  # noqa: F401 — register models for autogenerate
from app.database import Base, engine

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    # Use the engine URL directly — avoids configparser % interpolation issues
    context.configure(
        url=engine.url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


_MIGRATION_LOCK_KEY = 727001  # arbitrary fixed advisory lock id, just for serializing migrations


def run_migrations_online() -> None:
    with engine.connect() as connection:
        # Every pod (app, frontend, api-worker, ml-worker, crawler-http x2) independently
        # runs `alembic upgrade head` on every start/restart (both entrypoint.sh and
        # app/main.py's lifespan). Without serialization, concurrent sessions queue for
        # the same ACCESS EXCLUSIVE table lock on a plain ALTER TABLE and get killed by
        # the engine-wide 30s statement_timeout before any of them finish — and a
        # crash-looping pod (e.g. crawler-http) can keep refilling that queue, so the
        # migration never lands. A session-level advisory lock held for this connection's
        # lifetime makes every other pod simply wait its turn instead of fighting over
        # the table lock. The wait itself can legitimately exceed 30s under contention,
        # so disable the statement timeout for this connection only.
        connection.execute(text("SET statement_timeout = 0"))
        connection.commit()
        connection.execute(text("SELECT pg_advisory_lock(:key)"), {"key": _MIGRATION_LOCK_KEY})
        connection.commit()
        try:
            context.configure(connection=connection, target_metadata=target_metadata)
            with context.begin_transaction():
                context.run_migrations()
        finally:
            connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": _MIGRATION_LOCK_KEY})
            connection.commit()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
