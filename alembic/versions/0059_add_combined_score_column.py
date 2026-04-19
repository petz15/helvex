"""Add combined_score stored column to companies.

Revision ID: 0059
Revises: 0058
Create Date: 2026-04-19
"""
from alembic import op
import sqlalchemy as sa

revision = "0059"
down_revision = "0058"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("companies", sa.Column("combined_score", sa.Float(), nullable=True))

    # Backfill using the same weighted formula as the Python @property.
    # Weights renormalised to present scores (matches Company._combined_score exactly).
    op.execute("""
        UPDATE companies SET combined_score = ROUND(
            CASE
                WHEN ai_score IS NOT NULL AND web_score IS NOT NULL AND flex_score IS NOT NULL
                    THEN (0.70 * ai_score + 0.20 * web_score + 0.10 * flex_score)
                WHEN ai_score IS NOT NULL AND web_score IS NOT NULL
                    THEN (0.70 * ai_score + 0.20 * web_score) / 0.90
                WHEN ai_score IS NOT NULL AND flex_score IS NOT NULL
                    THEN (0.70 * ai_score + 0.10 * flex_score) / 0.80
                WHEN web_score IS NOT NULL AND flex_score IS NOT NULL
                    THEN (0.20 * web_score + 0.10 * flex_score) / 0.30
                WHEN ai_score  IS NOT NULL THEN ai_score::float
                WHEN web_score IS NOT NULL THEN web_score::float
                WHEN flex_score IS NOT NULL THEN flex_score::float
                ELSE NULL
            END
        )
        WHERE ai_score IS NOT NULL OR web_score IS NOT NULL OR flex_score IS NOT NULL
    """)

    # Index for ORDER BY and range filters
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_companies_combined_score_stored "
        "ON companies (combined_score DESC NULLS LAST)"
    )

    # Composite indexes for category-detail queries (filter + sort)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_companies_ai_category_combined "
        "ON companies (ai_category, combined_score DESC NULLS LAST)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_companies_noga_code_combined "
        "ON companies (noga_code, combined_score DESC NULLS LAST)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_companies_noga_code_combined")
    op.execute("DROP INDEX IF EXISTS ix_companies_ai_category_combined")
    op.execute("DROP INDEX IF EXISTS ix_companies_combined_score_stored")
    op.drop_column("companies", "combined_score")
