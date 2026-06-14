"""Add company_web_extract table: resolved structured data from crawled pages.

Revision ID: 0094
Revises: 0093
Create Date: 2026-06-13
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0094"
down_revision: str = "0093"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "company_web_extract",
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("emails", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("phones", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("socials", sa.JSON(), nullable=True),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("uid", sa.String(20), nullable=True),
        sa.Column("languages", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("service_keywords", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("extraction_method", sa.String(64), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("extracted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("company_id"),
    )
    # Partial index for companies with a verified UID — speeds up match/verify joins.
    op.execute(
        "CREATE INDEX ix_company_web_extract_uid "
        "ON company_web_extract (uid) WHERE uid IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_table("company_web_extract")
