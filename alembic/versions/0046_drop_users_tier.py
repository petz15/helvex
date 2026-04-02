"""Drop deprecated users.tier column.

Revision ID: 0046_drop_users_tier
Revises: 0045_billing_tiers_user_addr
Create Date: 2026-04-02
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0046_drop_users_tier"
down_revision = "0045_billing_tiers_user_addr"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("users", "tier")


def downgrade() -> None:
    op.add_column(
        "users",
        sa.Column("tier", sa.String(length=32), nullable=False, server_default="free"),
    )
