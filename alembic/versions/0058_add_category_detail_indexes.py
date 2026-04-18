"""Add indexes to speed up category detail queries.

Revision ID: 0058
Revises: 0057
Create Date: 2026-04-18
"""
from alembic import op

revision = "0058"
down_revision = "0057"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # B-tree on ai_category for equality filter (WHERE ai_category = ?)
    # The existing GIN trigram index helps LIKE; it doesn't help = efficiently.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_companies_ai_category "
        "ON companies (ai_category)"
    )

    # Composite: category filter + score sort — covers the common category-detail
    # company list query (WHERE ai_category = ? ORDER BY ai_score DESC).
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_companies_ai_category_ai_score "
        "ON companies (ai_category, ai_score DESC NULLS LAST)"
    )

    # Same pattern for noga_code (already has a plain B-tree from 0035,
    # but add composite with ai_score for sorted category detail lists).
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_companies_noga_code_ai_score "
        "ON companies (noga_code, ai_score DESC NULLS LAST)"
    )

    # Functional index for combined score expression used in ORDER BY and bands.
    # Mirrors the Python @property weights (0.70 ai, 0.20 web, 0.10 flex).
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_companies_combined_score_new ON companies "
        "((COALESCE(ai_score * 0.70, 0.0) "
        " + COALESCE(web_score * 0.20, 0.0) "
        " + COALESCE(flex_score * 0.10, 0.0)) DESC NULLS LAST)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_companies_combined_score_new")
    op.execute("DROP INDEX IF EXISTS ix_companies_noga_code_ai_score")
    op.execute("DROP INDEX IF EXISTS ix_companies_ai_category_ai_score")
    op.execute("DROP INDEX IF EXISTS ix_companies_ai_category")
