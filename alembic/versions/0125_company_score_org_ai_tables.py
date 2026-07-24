"""Scoring & multi-tenancy rework — phases 1-2: company_score + org_company_ai tables.

Today flex/web/ai/combined scores are written onto the global `companies` table,
so one org's rescore overwrites what every other org sees. This adds the
per-scope score tables from docs/code-review/scoring-multitenancy-rework.md:

  - company_score(org_id, user_id NULL, company_id) — org-default score set,
    materialized by the future `rescore_scope` job. user_id is populated only
    for a user who has overridden at least one scoring_* config key.
  - org_company_ai(org_id, company_id) — org-shared AI classification
    (ai_score promoted column + ai_data JSONB for category/freeform/future
    per-company summaries).

Backfill: seed company_score(user_id NULL) and org_company_ai for EVERY
existing org from the current global Company columns (decision 1a in the
design doc — free starting point, no re-scoring needed). This is a one-time
cross join of organizations x companies; fine at current org counts (a
handful of orgs x 700k companies), but re-check row counts before re-running
this migration if the org count has grown substantially.

Non-breaking: `companies.flex_score/web_score/combined_score/ai_score` are
UNCHANGED and keep being written by existing code paths in this phase — read
paths are not yet cut over. See architecture.md for phase status.

Revision ID: 0125
Revises: 0124
Create Date: 2026-07-24
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0125"
down_revision: str = "0124"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "company_score",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("flex_score", sa.Integer(), nullable=True),
        sa.Column("web_score", sa.Integer(), nullable=True),
        sa.Column("combined_score", sa.Float(), nullable=True),
        sa.Column("config_version", sa.String(length=64), nullable=True),
        sa.Column("scored_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id", "user_id", "company_id", name="uq_company_score_scope"),
    )
    op.create_index(
        "ix_company_score_scope_combined", "company_score",
        ["org_id", "user_id", "combined_score"],
    )

    op.create_table(
        "org_company_ai",
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("ai_score", sa.Integer(), nullable=True),
        sa.Column("ai_data", sa.JSON(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("org_id", "company_id"),
    )

    # Backfill company_score(user_id NULL) for every org from current global scores.
    op.execute("""
        INSERT INTO company_score (org_id, user_id, company_id, flex_score, web_score, combined_score, scored_at)
        SELECT o.id, NULL, c.id, c.flex_score, c.web_score, c.combined_score, now()
        FROM organizations o
        CROSS JOIN companies c
    """)

    # Backfill org_company_ai for every org, only where a global ai_score exists.
    op.execute("""
        INSERT INTO org_company_ai (org_id, company_id, ai_score, ai_data, updated_at)
        SELECT o.id, c.id, c.ai_score,
               jsonb_strip_nulls(jsonb_build_object('ai_category', c.ai_category, 'ai_freeform', c.ai_freeform)),
               now()
        FROM organizations o
        CROSS JOIN companies c
        WHERE c.ai_score IS NOT NULL
    """)


def downgrade() -> None:
    op.drop_table("org_company_ai")
    op.drop_index("ix_company_score_scope_combined", table_name="company_score")
    op.drop_table("company_score")
