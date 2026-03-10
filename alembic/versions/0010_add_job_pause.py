"""Add pause_requested column to job_runs table.

Revision ID: 0012
Revises: 0011
Create Date: 2026-03-10
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("job_runs", sa.Column("pause_requested", sa.Boolean(), nullable=False, server_default="false"))


def downgrade() -> None:
    op.drop_column("job_runs", "pause_requested")
