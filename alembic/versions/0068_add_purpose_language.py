"""Add purpose_language column to companies table.

Revision ID: 0068
Revises: 0067
Create Date: 2026-04-24
"""

from alembic import op
import sqlalchemy as sa

revision = "0068"
down_revision = "0067"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "companies",
        sa.Column("purpose_language", sa.String(8), nullable=True),
    )
    op.create_index(
        "ix_companies_purpose_language",
        "companies",
        ["purpose_language"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_companies_purpose_language", table_name="companies")
    op.drop_column("companies", "purpose_language")
