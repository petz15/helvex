"""Add company_directory_data table

Stores crawled content from business directory profile pages (moneyhouse.ch,
local.ch, treuhandvergleich.ch, northdata.com, etc.). One row per (company, URL).
Used to enrich Claude AI classification with external profile text, ratings,
and categories that are not available from the company's own website.
"""
from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0111"
down_revision: Union[str, None] = "0110"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "company_directory_data",
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("domain", sa.String(128), nullable=True),
        sa.Column("crawl_status", sa.String(32), nullable=False, server_default="crawled"),
        sa.Column("crawl_error", sa.Text(), nullable=True),
        sa.Column("raw_text", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("rating", sa.Float(), nullable=True),
        sa.Column("review_count", sa.Integer(), nullable=True),
        sa.Column(
            "categories",
            postgresql.ARRAY(sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "crawled_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("company_id", "url"),
    )
    op.create_index(
        "ix_company_directory_data_company_id",
        "company_directory_data",
        ["company_id"],
    )
    op.create_index(
        "ix_company_directory_data_domain",
        "company_directory_data",
        ["domain"],
    )


def downgrade() -> None:
    op.drop_index("ix_company_directory_data_domain", table_name="company_directory_data")
    op.drop_index("ix_company_directory_data_company_id", table_name="company_directory_data")
    op.drop_table("company_directory_data")
