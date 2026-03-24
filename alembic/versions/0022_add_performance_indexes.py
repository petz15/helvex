"""Add performance indexes for scores, filters, and ordering.

Revision ID: 0022
Revises: 0021
Create Date: 2026-03-21
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: Union[str, None] = "0021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Trigram extension (required for GIN ILIKE indexes) ─────────────────────
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # ── Score columns — ORDER BY and >= filters ────────────────────────────────
    op.create_index("ix_companies_zefix_score",         "companies", ["zefix_score"],         if_not_exists=True)
    op.create_index("ix_companies_website_match_score", "companies", ["website_match_score"], if_not_exists=True)
    op.create_index("ix_companies_claude_score",        "companies", ["claude_score"],         if_not_exists=True)

    # ── Frequently filtered low-cardinality columns ────────────────────────────
    op.create_index("ix_companies_canton",           "companies", ["canton"],           if_not_exists=True)
    op.create_index("ix_companies_status",           "companies", ["status"],           if_not_exists=True)
    op.create_index("ix_companies_review_status",    "companies", ["review_status"],    if_not_exists=True)
    op.create_index("ix_companies_proposal_status",  "companies", ["proposal_status"],  if_not_exists=True)

    # ── Timestamp columns — ORDER BY and IS NULL filters ──────────────────────
    op.create_index("ix_companies_updated_at",         "companies", ["updated_at"],         if_not_exists=True)
    op.create_index("ix_companies_website_checked_at", "companies", ["website_checked_at"], if_not_exists=True)
    op.create_index("ix_companies_claude_scored_at",   "companies", ["claude_scored_at"],   if_not_exists=True)
    op.create_index("ix_companies_zefix_scored_at",    "companies", ["zefix_scored_at"],    if_not_exists=True)

    # ── Composite: common filter + score sort combinations ─────────────────────
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_companies_canton_zefix ON companies "
        "(canton, zefix_score DESC NULLS LAST)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_companies_canton_google ON companies "
        "(canton, website_match_score DESC NULLS LAST)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_companies_canton_claude ON companies "
        "(canton, claude_score DESC NULLS LAST)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_companies_review_zefix ON companies "
        "(review_status, zefix_score DESC NULLS LAST)"
    )

    # ── Functional: combined score expression ──────────────────────────────────
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_companies_combined_score ON companies "
        "((COALESCE(claude_score * 0.70, 0.0) "
        " + COALESCE(website_match_score * 0.20, 0.0) "
        " + COALESCE(zefix_score * 0.10, 0.0)) DESC NULLS LAST)"
    )

    # ── Partial: batch operation hot paths ────────────────────────────────────
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_companies_missing_website ON companies (id) "
        "WHERE website_url IS NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_companies_no_claude_score ON companies (id) "
        "WHERE claude_score IS NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_companies_has_google_results ON companies (id) "
        "WHERE google_search_results_raw IS NOT NULL"
    )

    # ── GIN trigram: fast ILIKE for text filter fields ─────────────────────────
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_companies_tfidf_cluster_trgm ON companies "
        "USING gin (tfidf_cluster gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_companies_purpose_keywords_trgm ON companies "
        "USING gin (purpose_keywords gin_trgm_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_companies_purpose_keywords_trgm")
    op.execute("DROP INDEX IF EXISTS ix_companies_tfidf_cluster_trgm")
    op.execute("DROP INDEX IF EXISTS ix_companies_has_google_results")
    op.execute("DROP INDEX IF EXISTS ix_companies_no_claude_score")
    op.execute("DROP INDEX IF EXISTS ix_companies_missing_website")
    op.execute("DROP INDEX IF EXISTS ix_companies_combined_score")
    op.execute("DROP INDEX IF EXISTS ix_companies_review_zefix")
    op.execute("DROP INDEX IF EXISTS ix_companies_canton_claude")
    op.execute("DROP INDEX IF EXISTS ix_companies_canton_google")
    op.execute("DROP INDEX IF EXISTS ix_companies_canton_zefix")
    op.drop_index("ix_companies_zefix_scored_at",    table_name="companies")
    op.drop_index("ix_companies_claude_scored_at",   table_name="companies")
    op.drop_index("ix_companies_website_checked_at", table_name="companies")
    op.drop_index("ix_companies_updated_at",         table_name="companies")
    op.drop_index("ix_companies_proposal_status",    table_name="companies")
    op.drop_index("ix_companies_review_status",      table_name="companies")
    op.drop_index("ix_companies_status",             table_name="companies")
    op.drop_index("ix_companies_canton",             table_name="companies")
    op.drop_index("ix_companies_claude_score",       table_name="companies")
    op.drop_index("ix_companies_website_match_score",table_name="companies")
    op.drop_index("ix_companies_zefix_score",        table_name="companies")
