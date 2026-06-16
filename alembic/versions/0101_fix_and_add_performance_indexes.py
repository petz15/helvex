"""Fix stale indexes from column renames and add missing performance indexes.

Migration 0022 created indexes on columns that were later renamed
(zefix_score→flex_score, website_match_score→web_score, claude_score→ai_score,
claude_scored_at→ai_scored_at, zefix_scored_at→flex_scored_at,
proposal_status→contact_status). Migration 0025 created a GIN trgm index on
claude_category which was renamed to ai_category. All those are effectively gone.

This migration:
  1. Drops the stale indexes that reference old column names (idempotent).
  2. Adds btree indexes on current score/timestamp columns.
  3. Fixes the ai_category GIN trgm index.
  4. Adds GIN trgm on old_names and noga_label (both now ILIKE-searched).
  5. Adds GIN trgm on sogc_person_entities.lastname / firstname (person search).
  6. Fixes partial indexes for batch job hot paths.
  7. Adds composite (company_id, is_current) on sogc_person_appearances.
  8. Adds btree on sogc_person_entities.active_company_count (ORDER BY).

Revision ID: 0101
Revises: 0100
Create Date: 2026-06-16
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0101"
down_revision: Union[str, None] = "0100"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 1. Drop stale indexes from column renames ──────────────────────────────
    # These referenced old column names that no longer exist; safe to drop.
    for idx in (
        "ix_companies_zefix_score",
        "ix_companies_website_match_score",
        "ix_companies_claude_score",
        "ix_companies_claude_scored_at",
        "ix_companies_zefix_scored_at",
        "ix_companies_proposal_status",
        "ix_companies_canton_zefix",
        "ix_companies_canton_google",
        "ix_companies_canton_claude",
        "ix_companies_review_zefix",
        "ix_companies_combined_score",        # expression used old column names
        "ix_companies_missing_website",       # partial — harmless but recreate below
        "ix_companies_no_claude_score",       # partial on old column name
        "ix_companies_has_google_results",    # partial — still valid, keep below
        "ix_companies_claude_category_trgm",  # GIN on old column name
    ):
        op.execute(f"DROP INDEX IF EXISTS {idx}")

    # ── 2. Score columns — btree for ORDER BY and range filters ───────────────
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_companies_flex_score "
        "ON companies (flex_score DESC NULLS LAST)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_companies_web_score "
        "ON companies (web_score DESC NULLS LAST)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_companies_ai_score "
        "ON companies (ai_score DESC NULLS LAST)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_companies_combined_score "
        "ON companies (combined_score DESC NULLS LAST)"
    )

    # ── 3. Timestamp columns — for ORDER BY and IS NULL / IS NOT NULL filters ──
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_companies_ai_scored_at "
        "ON companies (ai_scored_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_companies_flex_scored_at "
        "ON companies (flex_scored_at)"
    )
    # website_checked_at already created by 0022 under the same name — no-op
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_companies_website_checked_at "
        "ON companies (website_checked_at)"
    )

    # ── 4. Low-cardinality filter columns — btree ─────────────────────────────
    # canton, status, review_status created by 0022 under same names — no-ops
    op.execute("CREATE INDEX IF NOT EXISTS ix_companies_canton        ON companies (canton)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_companies_status        ON companies (status)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_companies_review_status ON companies (review_status)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_companies_contact_status ON companies (contact_status)")

    # ── 5. GIN trgm — fix ai_category (was claude_category in 0025) ──────────
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_companies_ai_category_trgm "
        "ON companies USING GIN (ai_category gin_trgm_ops)"
    )

    # ── 6. GIN trgm — old_names (now ILIKE-searched by name search + global) ──
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_companies_old_names_trgm "
        "ON companies USING GIN (old_names gin_trgm_ops)"
    )

    # ── 7. GIN trgm — noga_label (ILIKE filter in company list) ──────────────
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_companies_noga_label_trgm "
        "ON companies USING GIN (noga_label gin_trgm_ops)"
    )

    # ── 8. Person search — GIN trgm on lastname / firstname ───────────────────
    # sogc_person_entities has ~millions of rows; person search does
    # func.lower(lastname).like('%term%') — needs trgm to avoid full scans.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_sogc_person_lastname_trgm "
        "ON sogc_person_entities USING GIN (lower(lastname) gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_sogc_person_firstname_trgm "
        "ON sogc_person_entities USING GIN (lower(firstname) gin_trgm_ops)"
    )

    # ── 9. Person search ORDER BY ─────────────────────────────────────────────
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_sogc_person_active_company_count "
        "ON sogc_person_entities (active_company_count DESC NULLS LAST)"
    )

    # ── 10. Board panel — composite for current-director lookup ───────────────
    # Queries filter on company_id and ORDER BY / filter on is_current.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_sogc_appearances_company_current "
        "ON sogc_person_appearances (company_id, is_current)"
    )

    # ── 11. Partial indexes for batch job hot paths ───────────────────────────
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_companies_missing_website "
        "ON companies (id) WHERE website_url IS NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_companies_no_ai_score "
        "ON companies (id) WHERE ai_score IS NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_companies_no_flex_score "
        "ON companies (id) WHERE flex_score IS NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_companies_has_google_results "
        "ON companies (id) WHERE google_search_results_raw IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_companies_has_google_results")
    op.execute("DROP INDEX IF EXISTS ix_companies_no_flex_score")
    op.execute("DROP INDEX IF EXISTS ix_companies_no_ai_score")
    op.execute("DROP INDEX IF EXISTS ix_companies_missing_website")
    op.execute("DROP INDEX IF EXISTS ix_sogc_appearances_company_current")
    op.execute("DROP INDEX IF EXISTS ix_sogc_person_active_company_count")
    op.execute("DROP INDEX IF EXISTS ix_sogc_person_firstname_trgm")
    op.execute("DROP INDEX IF EXISTS ix_sogc_person_lastname_trgm")
    op.execute("DROP INDEX IF EXISTS ix_companies_noga_label_trgm")
    op.execute("DROP INDEX IF EXISTS ix_companies_old_names_trgm")
    op.execute("DROP INDEX IF EXISTS ix_companies_ai_category_trgm")
    op.execute("DROP INDEX IF EXISTS ix_companies_contact_status")
    op.execute("DROP INDEX IF EXISTS ix_companies_review_status")
    op.execute("DROP INDEX IF EXISTS ix_companies_status")
    op.execute("DROP INDEX IF EXISTS ix_companies_canton")
    op.execute("DROP INDEX IF EXISTS ix_companies_website_checked_at")
    op.execute("DROP INDEX IF EXISTS ix_companies_flex_scored_at")
    op.execute("DROP INDEX IF EXISTS ix_companies_ai_scored_at")
    op.execute("DROP INDEX IF EXISTS ix_companies_combined_score")
    op.execute("DROP INDEX IF EXISTS ix_companies_ai_score")
    op.execute("DROP INDEX IF EXISTS ix_companies_web_score")
    op.execute("DROP INDEX IF EXISTS ix_companies_flex_score")
