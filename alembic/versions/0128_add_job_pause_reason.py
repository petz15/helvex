"""add job_runs.pause_reason

Distinguishes a pause a person asked for from one caused by a pod shutting down
or by crawler preemption. Before this column, `resume_all_paused_jobs()` treated
every paused job as recoverable, so a job paused from the UI restarted itself on
the next recovery sweep (~3 minutes) and there was no way to keep it stopped.

Existing paused rows keep NULL, which is treated as auto-resumable — the old
behaviour — so no backfill is needed.

Revision ID: 0128
Revises: 0127
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0128"
down_revision: Union[str, None] = "0127"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "job_runs",
        sa.Column("pause_reason", sa.String(length=16), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("job_runs", "pause_reason")
