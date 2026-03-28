"""Add oauth_accounts table and make hashed_password nullable

Revision ID: 0034
Revises: 0033
Create Date: 2026-03-28
"""

from alembic import op
import sqlalchemy as sa

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "oauth_accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("provider_user_id", sa.String(256), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("provider", "provider_user_id", name="uq_oauth_accounts_provider_user"),
    )

    # Allow OAuth-only users to have no password
    op.alter_column("users", "hashed_password", nullable=True)


def downgrade() -> None:
    # Restore non-nullable constraint (set a placeholder for any NULL rows first)
    op.execute("UPDATE users SET hashed_password = '' WHERE hashed_password IS NULL")
    op.alter_column("users", "hashed_password", nullable=False)

    op.drop_table("oauth_accounts")
