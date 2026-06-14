"""Add google_search_params column to companies.

Stores the exact query params sent to the search API (q, gl/country, hl/language,
location, provider) so bad results can be diagnosed without guessing what was sent.

Revision ID: 0095
Revises: 0094
Create Date: 2026-06-14
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0095"
down_revision: str = "0094"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "companies",
        sa.Column("google_search_params", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("companies", "google_search_params")
