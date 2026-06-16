"""Add uid_matches_zefix + persons to company_web_extract.

uid_matches_zefix records whether the Swiss UID found on the crawled site matches
the company's Zefix UID — a verification signal that drives extraction confidence
and best-candidate selection. persons holds management/contact names parsed from
the impressum (feeds the future People graph).

Revision ID: 0099
Revises: 0098
Create Date: 2026-06-16
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0099"
down_revision: str = "0098"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "company_web_extract",
        sa.Column("uid_matches_zefix", sa.Boolean(), nullable=True),
    )
    op.add_column(
        "company_web_extract",
        sa.Column("persons", postgresql.ARRAY(sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("company_web_extract", "persons")
    op.drop_column("company_web_extract", "uid_matches_zefix")
