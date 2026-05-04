"""Drop duplicate combined_score functional indexes.

Two functional indexes on the combined score expression were made redundant
by migration 0059 which added a stored combined_score column with its own
B-tree index (ix_companies_combined_score_stored):

  - ix_companies_combined_score     (0022) — old column names (pre-0032 rename)
  - ix_companies_combined_score_new (0058) — corrected column names, same formula

Both are now dead weight: queries ORDER BY combined_score use the stored column
index, not an expression index. Dropping them reduces write overhead on every
UPDATE to companies (700k+ rows).

Also drops the stale partial indexes that referenced old column names
(claude_score, zefix_score) — these were carried forward by 0032 column-rename
but their names are confusing in EXPLAIN output.

Revision ID: 0071
Revises: fe7a997e322a
Create Date: 2026-05-04
"""

from alembic import op

revision = "0071"
down_revision = "fe7a997e322a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop the two redundant combined_score functional indexes.
    # ix_companies_combined_score_stored (0059) is the correct replacement.
    op.execute("DROP INDEX IF EXISTS ix_companies_combined_score")
    op.execute("DROP INDEX IF EXISTS ix_companies_combined_score_new")

    # Drop stale partial indexes that referenced old column names.
    # The underlying partial conditions use renamed columns; their names are misleading.
    op.execute("DROP INDEX IF EXISTS ix_companies_no_claude_score")
    op.execute("DROP INDEX IF EXISTS ix_companies_missing_website")


def downgrade() -> None:
    # Restore the redundant combined-score functional indexes (not recommended)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_companies_combined_score ON companies "
        "((COALESCE(ai_score * 0.70, 0.0) "
        " + COALESCE(web_score * 0.20, 0.0) "
        " + COALESCE(flex_score * 0.10, 0.0)) DESC NULLS LAST)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_companies_combined_score_new ON companies "
        "((COALESCE(ai_score * 0.70, 0.0) "
        " + COALESCE(web_score * 0.20, 0.0) "
        " + COALESCE(flex_score * 0.10, 0.0)) DESC NULLS LAST)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_companies_no_claude_score ON companies (id) "
        "WHERE ai_score IS NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_companies_missing_website ON companies (id) "
        "WHERE website_url IS NULL"
    )
