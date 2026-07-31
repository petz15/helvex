"""Add crawl_phase to company_crawl_state for two-phase crawling.

Phase A (identity) fetches homepage + impressum/contact and settles the
identity verdict. Phase B (content) crawls the rest of the site, and only runs
for companies whose identity was confirmed.

Transition backfill for the existing corpus, per the three states it is in:

  1. Crawled AND ingested (a company_web_extract row exists) — phase A is
     complete. Confirmed ones (UID match, or no UID with confidence >= 0.65 —
     the same gate the live pipeline applies) are queued for phase B; the rest
     stay in phase 'identity' with their current status untouched, so the
     existing fallback machinery governs them exactly as before.

  2. Crawled but NOT ingested (pages exist, no extract) — treated as neither
     phase complete and reset to pending phase A, so the new pipeline re-crawls
     and re-ingests them from scratch. Their stale pages are left in place; the
     phase-A crawl deletes them before saving (delete_web_pages_for_company).

  3. URL candidates only (no pages, no extract) — already the default:
     phase 'identity', status 'pending'. No statement needed.

Revision ID: 0129
Revises: 0128
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0129"
down_revision: Union[str, None] = "0128"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Mirrors the live confirmed-gate in handle_web_extract: a UID match is proof,
# otherwise an absent UID needs confidence >= 0.65. A UID *mismatch* is never
# confirmed regardless of confidence.
_CONFIRMED_SQL = """
    (e.uid_matches_zefix IS TRUE
     OR (e.uid_matches_zefix IS NULL AND COALESCE(e.confidence, 0) >= 0.65))
"""


def upgrade() -> None:
    op.add_column(
        "company_crawl_state",
        sa.Column(
            "crawl_phase",
            sa.String(length=16),
            nullable=False,
            server_default="identity",
        ),
    )

    # Case 1 — ingested and confirmed: phase A done, queue phase B.
    op.execute(
        f"""
        UPDATE company_crawl_state cs
        SET crawl_phase = 'content',
            crawl_status = 'pending',
            next_crawl_at = NULL,
            consecutive_failures = 0
        WHERE EXISTS (
            SELECT 1 FROM company_web_extract e
            WHERE e.company_id = cs.company_id AND {_CONFIRMED_SQL}
        )
        """
    )

    # Case 1b — ingested but not confirmed: phase A is still "done" in the sense
    # that it ran, but these belong to the fallback chain, not phase B. Leave
    # crawl_status alone; only pin the phase so they are never claimed by a
    # content worker.
    op.execute(
        """
        UPDATE company_crawl_state cs
        SET crawl_phase = 'identity'
        WHERE crawl_phase <> 'content'
          AND EXISTS (
              SELECT 1 FROM company_web_extract e WHERE e.company_id = cs.company_id
          )
        """
    )

    # Case 2 — crawled but never ingested: re-do both phases with the new
    # pipeline. Excludes anything already moved to 'content' above.
    op.execute(
        """
        UPDATE company_crawl_state cs
        SET crawl_phase = 'identity',
            crawl_status = 'pending',
            tier = 'http',
            next_crawl_at = NULL,
            consecutive_failures = 0,
            crawl_error_detail = NULL,
            pages_crawled = NULL
        WHERE cs.crawl_phase <> 'content'
          AND NOT EXISTS (
              SELECT 1 FROM company_web_extract e WHERE e.company_id = cs.company_id
          )
          AND EXISTS (
              SELECT 1 FROM company_web_pages p
              WHERE p.company_id = cs.company_id AND p.crawled IS TRUE
          )
        """
    )

    op.create_index(
        "ix_company_crawl_state_phase_status_tier",
        "company_crawl_state",
        ["crawl_phase", "crawl_status", "tier"],
    )


def downgrade() -> None:
    op.drop_index("ix_company_crawl_state_phase_status_tier", table_name="company_crawl_state")
    op.drop_column("company_crawl_state", "crawl_phase")
