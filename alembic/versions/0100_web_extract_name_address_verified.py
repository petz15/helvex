"""Add name_address_verified to company_web_extract.

Secondary verification signal: True when no UID was found on the site but the
company name and postal address (zip + city) both match Zefix exactly. Used
alongside uid_matches_zefix to adjust web_score after extraction.

Revision ID: 0100
Revises: 0099
Create Date: 2026-06-16
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0100"
down_revision: str = "0099"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "company_web_extract",
        sa.Column("name_address_verified", sa.Boolean(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("company_web_extract", "name_address_verified")
