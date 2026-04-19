"""Add purpose_keywords_arr TEXT[] array column alongside the existing comma-separated text.

The old purpose_keywords column is kept for now so existing code continues to
work while consumers migrate. The array column is populated from it and kept
in sync by the cluster pipeline going forward.

Revision ID: 0060
Revises: 0059
Create Date: 2026-04-19
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, TEXT

revision = "0060"
down_revision = "0059"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "companies",
        sa.Column("purpose_keywords_arr", ARRAY(TEXT), nullable=True),
    )

    # Backfill: split existing comma-separated purpose_keywords into an array,
    # trimming whitespace from each element and dropping empty strings.
    op.execute("""
        UPDATE companies
        SET purpose_keywords_arr = ARRAY(
            SELECT trim(kw)
            FROM unnest(string_to_array(purpose_keywords, ',')) AS kw
            WHERE trim(kw) <> ''
        )
        WHERE purpose_keywords IS NOT NULL AND purpose_keywords <> ''
    """)

    # GIN index enables fast ANY/overlap array operators.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_companies_purpose_keywords_arr "
        "ON companies USING gin (purpose_keywords_arr)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_companies_purpose_keywords_arr")
    op.drop_column("companies", "purpose_keywords_arr")
