"""Add logged_out_at to users for session revocation on logout.

Tokens (cookie or JWT) issued before this timestamp are rejected, which
invalidates all active sessions when a user explicitly logs out.

Revision ID: 0080
Revises: 0079
Create Date: 2026-05-15
"""

from alembic import op

revision = "0080"
down_revision = "0079"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE users ADD COLUMN logged_out_at TIMESTAMP WITH TIME ZONE"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS logged_out_at")
