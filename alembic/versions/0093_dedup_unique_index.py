"""Add partial unique index on job_runs.dedup_key for active jobs.

Enforces dedup at the DB layer: two pods that race past the soft app-level
check and both INSERT with the same dedup_key will have one insert rejected
by this constraint.  The application catches the IntegrityError and returns
the already-committed row as a dedup hit.

The index is partial (WHERE status IN (...)) so that completed/failed/cancelled
jobs with the same key do not block future runs of the same job type.

Revision ID: 0093
Revises: 0092
Create Date: 2026-06-13
"""

from alembic import op

revision: str = "0093"
down_revision: str = "0092"
branch_labels = None
depends_on = None

_ACTIVE_STATUSES = "('queued', 'running', 'paused', 'waiting_external')"
_INDEX_NAME = "ix_job_runs_dedup_key_active"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE UNIQUE INDEX {_INDEX_NAME}
        ON job_runs (dedup_key)
        WHERE status IN {_ACTIVE_STATUSES}
          AND dedup_key IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {_INDEX_NAME}")
