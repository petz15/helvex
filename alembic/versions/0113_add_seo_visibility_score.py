"""Add seo_visibility_score to companies

Separate from web_score (URL-selection confidence). This measures actual
Google search visibility: organic rank of the company's own site, discounted
by ads and SERP features (local pack, knowledge graph) that push it down the
page. Computed from already-stored google_search_results_raw /
google_search_full_raw — no new API calls.
"""
from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0113"
down_revision: Union[str, None] = "0112"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("companies", sa.Column("seo_visibility_score", sa.Integer(), nullable=True))
    op.add_column(
        "companies",
        sa.Column("seo_visibility_computed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_companies_seo_visibility_score", "companies", ["seo_visibility_score"]
    )


def downgrade() -> None:
    op.drop_index("ix_companies_seo_visibility_score", table_name="companies")
    op.drop_column("companies", "seo_visibility_computed_at")
    op.drop_column("companies", "seo_visibility_score")
