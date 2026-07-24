"""Add per-candidate identity_category/identity_probability to company_web_extract.

Part of the web-pipeline holistic rework, Layer B phase 2 (categorical verdict):
each crawled candidate now gets a categorical identity label (MATCH_UID /
MATCH_STRONG / MATCH_WEAK / MISMATCH / UNKNOWN) computed from the same
confidence/evidence already persisted, via website_status.categorize_identity.
Company-level cross-candidate outcomes (AMBIGUOUS, RELATED_ENTITY) are computed
in compute_verdict and are not stored per-row.

Additive only — the existing company-level `companies.website_status` vocabulary
(verified/confirmed/likely/social_only/directory_only/none) is unchanged in this
phase; that's a separate, larger cutover left for later.

Revision ID: 0121
Revises: 0120
Create Date: 2026-07-24
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0121"
down_revision: str = "0120"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "company_web_extract",
        sa.Column("identity_category", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "company_web_extract",
        sa.Column("identity_probability", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("company_web_extract", "identity_probability")
    op.drop_column("company_web_extract", "identity_category")
