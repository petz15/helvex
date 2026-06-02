"""Add google_search_full_raw column to companies for storing complete provider JSON.

Revision ID: 0092
Revises: 0091
Create Date: 2026-06-03
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0092"
down_revision: str = "0091"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "companies",
        sa.Column("google_search_full_raw", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("companies", "google_search_full_raw")
