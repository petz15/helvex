"""Add page-inventory fields to company_web_pages (discovered_via, crawled, priority).

Part of the web-pipeline holistic rework, Layer A (ingestion): the crawler already
discovers the full sitemap URL list but discards everything beyond the ~5 pages it
fetches. This lets it persist the full page inventory (which pages exist, whether
each was actually fetched) instead of only the fetched subset.

Existing rows are all actually-fetched pages, so `crawled` backfills to TRUE.

Revision ID: 0118
Revises: 0117
Create Date: 2026-07-24
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0118"
down_revision: str = "0117"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "company_web_pages",
        sa.Column("discovered_via", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "company_web_pages",
        sa.Column("crawled", sa.Boolean(), nullable=False, server_default="true"),
    )
    op.add_column(
        "company_web_pages",
        sa.Column("priority", sa.Integer(), nullable=True),
    )
    op.alter_column("company_web_pages", "crawled", server_default=None)


def downgrade() -> None:
    op.drop_column("company_web_pages", "priority")
    op.drop_column("company_web_pages", "crawled")
    op.drop_column("company_web_pages", "discovered_via")
