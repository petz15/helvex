"""Add organizations.credits_unlimited flag for superadmin orgs.

Revision ID: 0042
Revises: 0041
Create Date: 2026-04-01
"""

from alembic import op
import sqlalchemy as sa

revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column("credits_unlimited", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("organizations", "credits_unlimited")
