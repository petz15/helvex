"""company-level website verdict (website_status + website_count)

Adds a derived company-level verdict for "does this company have a website,
and how many?" computed from URL search results + crawl-verification extracts.
Mirrored on org_company_state for parity with the other web result fields
(website_url / web_score / social_media_only) that already dual-write there.
"""
from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0109"
down_revision: Union[str, None] = "0108"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # verified | confirmed | likely | social_only | directory_only | none | unknown(NULL)
    op.add_column("companies", sa.Column("website_status", sa.String(16), nullable=True))
    # number of distinct genuine websites detected (>=2 ⇒ multiple sites)
    op.add_column("companies", sa.Column("website_count", sa.Integer(), nullable=True))
    op.create_index("ix_companies_website_status", "companies", ["website_status"])

    op.add_column("org_company_state", sa.Column("website_status", sa.String(16), nullable=True))
    op.add_column("org_company_state", sa.Column("website_count", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("org_company_state", "website_count")
    op.drop_column("org_company_state", "website_status")
    op.drop_index("ix_companies_website_status", table_name="companies")
    op.drop_column("companies", "website_count")
    op.drop_column("companies", "website_status")
