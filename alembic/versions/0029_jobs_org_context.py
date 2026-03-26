"""Add org_id and user_id to job_runs; add org_id to collection_runs.

Revision ID: 0029
Revises: 0028
Create Date: 2026-03-26
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0029"
down_revision: Union[str, None] = "0028"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # job_runs
    op.add_column(
        "job_runs",
        sa.Column("org_id", sa.Integer, sa.ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True),
    )
    op.add_column(
        "job_runs",
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    )
    op.create_index("ix_job_runs_org_id", "job_runs", ["org_id"])
    op.create_index("ix_job_runs_user_id", "job_runs", ["user_id"])

    # collection_runs
    op.add_column(
        "collection_runs",
        sa.Column("org_id", sa.Integer, sa.ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True),
    )
    op.create_index("ix_collection_runs_org_id", "collection_runs", ["org_id"])


def downgrade() -> None:
    op.drop_index("ix_collection_runs_org_id", table_name="collection_runs")
    op.drop_column("collection_runs", "org_id")

    op.drop_index("ix_job_runs_user_id", table_name="job_runs")
    op.drop_index("ix_job_runs_org_id", table_name="job_runs")
    op.drop_column("job_runs", "user_id")
    op.drop_column("job_runs", "org_id")
