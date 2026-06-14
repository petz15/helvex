"""Link company_web_extract to url_candidate; change PK to (company_id, url_candidate_id).

Each extraction result is now tied to the specific URL candidate that was crawled,
so multiple results can coexist per company (one per URL tried). The best row
(highest confidence, then most recent) is selected by get_best_web_extract().

Rows that cannot be linked to a candidate (no crawl state / no selected URL) are
deleted — they will be regenerated on the next web_extract run with proper linkage.

Revision ID: 0096
Revises: 0095
Create Date: 2026-06-14
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0096"
down_revision: str = "0095"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Add column as nullable so we can populate it before enforcing NOT NULL.
    op.add_column(
        "company_web_extract",
        sa.Column("url_candidate_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        "fk_web_extract_url_candidate",
        "company_web_extract",
        "company_url_candidates",
        ["url_candidate_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # 2. Best-effort populate from the company's currently selected URL candidate.
    op.execute("""
        UPDATE company_web_extract e
        SET url_candidate_id = cs.selected_url_id
        FROM company_crawl_state cs
        WHERE cs.company_id = e.company_id
          AND cs.selected_url_id IS NOT NULL
    """)

    # 3. Drop rows that couldn't be linked — they will be regenerated on the
    #    next web_extract run now that the candidate FK is tracked.
    op.execute("DELETE FROM company_web_extract WHERE url_candidate_id IS NULL")

    # 4. Now safe to enforce NOT NULL.
    op.alter_column("company_web_extract", "url_candidate_id", nullable=False)

    # 5. Replace the single-column PK with the composite one.
    op.drop_constraint("company_web_extract_pkey", "company_web_extract", type_="primary")
    op.create_primary_key(
        "company_web_extract_pkey",
        "company_web_extract",
        ["company_id", "url_candidate_id"],
    )


def downgrade() -> None:
    # Keep only the highest-confidence row per company before restoring single-column PK.
    op.execute("""
        DELETE FROM company_web_extract a
        USING company_web_extract b
        WHERE a.company_id = b.company_id
          AND (
            a.confidence < b.confidence
            OR (a.confidence IS NOT DISTINCT FROM b.confidence AND a.extracted_at < b.extracted_at)
          )
    """)
    op.drop_constraint("company_web_extract_pkey", "company_web_extract", type_="primary")
    op.create_primary_key("company_web_extract_pkey", "company_web_extract", ["company_id"])
    op.drop_constraint(
        "fk_web_extract_url_candidate", "company_web_extract", type_="foreignkey"
    )
    op.drop_column("company_web_extract", "url_candidate_id")
