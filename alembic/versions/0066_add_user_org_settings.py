"""Add user_org_settings table for per-user per-org preferences (e.g. notification toggles).

Revision ID: 0066_add_user_org_settings
Revises: 0065_add_org_payment_methods
Create Date: 2026-04-22
"""

from alembic import op
import sqlalchemy as sa

revision = "0066_add_user_org_settings"
down_revision = "0065_add_org_payment_methods"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_org_settings",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("org_id", sa.Integer, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("key", sa.String(128), nullable=False),
        sa.Column("value", sa.Text, nullable=True),
        sa.UniqueConstraint("user_id", "org_id", "key", name="uq_user_org_settings_key"),
    )


def downgrade() -> None:
    op.drop_table("user_org_settings")
