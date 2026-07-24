"""Extract Google/ScrapingDog search-result data off companies into company_search_results.

Company table normalization (ROADMAP): google_search_results_raw, google_search_full_raw,
google_search_params, and website_checked_at are search-provider data, not company
master data — they move to their own table, a global fact (same tier as
company_web_extract/company_web_page), not an org-scoped overlay. The derived
verdict fields (website_url, web_score, website_status, website_count,
social_media_only) stay on companies for now — a separate change (the
scoring/multi-tenancy rework) addresses those.

Also drops org_company_state's Google-scoring shadow columns
(website_url/web_score/google_search_results_raw/website_checked_at/
social_media_only/website_status/website_count): confirmed dead — the intended
writer `update_org_google_results` has zero callers anywhere in the app, and
no code reads these fields off org_company_state either (`_overlay()` only
merges `_ORG_FIELDS`, which doesn't include them). See
docs/code-review/scoring-multitenancy-rework.md.

Revision ID: 0124
Revises: 0122
Create Date: 2026-07-24
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0124"
down_revision: str = "0122"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "company_search_results",
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=True),
        sa.Column("results_raw", sa.JSON(), nullable=True),
        sa.Column("full_raw", sa.JSON(), nullable=True),
        sa.Column("params", sa.JSON(), nullable=True),
        sa.Column("searched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("company_id"),
    )

    # Backfill from the existing companies columns — only rows with something to move.
    op.execute("""
        INSERT INTO company_search_results (company_id, results_raw, full_raw, params, searched_at)
        SELECT id,
               NULLIF(google_search_results_raw, '')::jsonb,
               NULLIF(google_search_full_raw, '')::jsonb,
               google_search_params,
               website_checked_at
        FROM companies
        WHERE google_search_results_raw IS NOT NULL
           OR google_search_full_raw IS NOT NULL
           OR google_search_params IS NOT NULL
           OR website_checked_at IS NOT NULL
    """)

    op.drop_column("companies", "google_search_results_raw")
    op.drop_column("companies", "google_search_full_raw")
    op.drop_column("companies", "google_search_params")
    op.drop_column("companies", "website_checked_at")

    op.drop_column("org_company_state", "website_url")
    op.drop_column("org_company_state", "web_score")
    op.drop_column("org_company_state", "google_search_results_raw")
    op.drop_column("org_company_state", "website_checked_at")
    op.drop_column("org_company_state", "social_media_only")
    op.drop_column("org_company_state", "website_status")
    op.drop_column("org_company_state", "website_count")


def downgrade() -> None:
    op.add_column("org_company_state", sa.Column("website_count", sa.Integer(), nullable=True))
    op.add_column("org_company_state", sa.Column("website_status", sa.String(length=16), nullable=True))
    op.add_column("org_company_state", sa.Column("social_media_only", sa.Boolean(), nullable=True))
    op.add_column("org_company_state", sa.Column("website_checked_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("org_company_state", sa.Column("google_search_results_raw", sa.Text(), nullable=True))
    op.add_column("org_company_state", sa.Column("web_score", sa.Float(), nullable=True))
    op.add_column("org_company_state", sa.Column("website_url", sa.String(length=512), nullable=True))

    op.add_column("companies", sa.Column("website_checked_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("companies", sa.Column("google_search_params", sa.JSON(), nullable=True))
    op.add_column("companies", sa.Column("google_search_full_raw", sa.Text(), nullable=True))
    op.add_column("companies", sa.Column("google_search_results_raw", sa.Text(), nullable=True))

    op.execute("""
        UPDATE companies c
        SET google_search_results_raw = csr.results_raw::text,
            google_search_full_raw = csr.full_raw::text,
            google_search_params = csr.params,
            website_checked_at = csr.searched_at
        FROM company_search_results csr
        WHERE csr.company_id = c.id
    """)

    op.drop_table("company_search_results")
