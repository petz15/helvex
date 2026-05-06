"""Add restart_count to job_runs for crash-loop detection.

Revision ID: 0075_add_job_run_restart_count
Revises: 0074_merge_sogc_heads
Create Date: 2026-05-07
"""

import sqlalchemy as sa
from alembic import op

revision = "0075_add_job_run_restart_count"
down_revision = "0074_merge_sogc_heads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "job_runs",
        sa.Column("restart_count", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("job_runs", "restart_count")
