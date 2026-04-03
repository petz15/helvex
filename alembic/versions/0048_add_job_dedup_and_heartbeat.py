"""Add dedup_key and last_heartbeat_at to job_runs.

dedup_key   — prevents duplicate active jobs of the same type/org.
last_heartbeat_at — used by requeue_interrupted_jobs to skip still-running
                    jobs (workers update this every ~30 s) so we never
                    double-execute a job that is alive on a worker pod.

Revision ID: 0048_add_job_dedup_and_heartbeat
Revises: 0047_add_default_payment_user_id
Create Date: 2026-04-03
"""

from alembic import op
import sqlalchemy as sa

revision = "0048_add_job_dedup_and_heartbeat"
down_revision = "0047_add_default_payment_user_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("job_runs", sa.Column("dedup_key", sa.String(64), nullable=True))
    op.create_index("ix_job_runs_dedup_key", "job_runs", ["dedup_key"])
    op.add_column(
        "job_runs",
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_job_runs_last_heartbeat_at", "job_runs", ["last_heartbeat_at"])


def downgrade() -> None:
    op.drop_index("ix_job_runs_last_heartbeat_at", table_name="job_runs")
    op.drop_column("job_runs", "last_heartbeat_at")
    op.drop_index("ix_job_runs_dedup_key", table_name="job_runs")
    op.drop_column("job_runs", "dedup_key")
