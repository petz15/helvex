"""Add structured content fields to company_web_extract (about_text, persons_struct, services_struct).

Part of the web-pipeline holistic rework, Layer A.4/C (structured content
extraction): team pages now parse into named+roled entries, services/products
pages into title+summary entries, and about/homepage pages contribute a
richer about_text than the existing 1000-char `description` — feeding NOGA/AI
classification for companies whose Zefix purpose text is thin.

Revision ID: 0119
Revises: 0118
Create Date: 2026-07-24
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0119"
down_revision: str = "0118"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("company_web_extract", sa.Column("about_text", sa.Text(), nullable=True))
    op.add_column(
        "company_web_extract",
        sa.Column("persons_struct", sa.JSON(), nullable=True),
    )
    op.add_column(
        "company_web_extract",
        sa.Column("services_struct", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("company_web_extract", "services_struct")
    op.drop_column("company_web_extract", "persons_struct")
    op.drop_column("company_web_extract", "about_text")
