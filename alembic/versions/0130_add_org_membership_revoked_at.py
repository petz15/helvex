"""Add users.org_membership_revoked_at to make org invites revocable.

Invite tokens are stateless signed payloads (org_id, email, role) with a 7-day
max_age and no DB record, so redeeming one leaves no trace. A member removed
from an org could therefore re-open their original invite link and rejoin at the
original role for the remainder of those 7 days — removal did not actually
remove them.

This mirrors the existing `users.logged_out_at` mechanism, which rejects JWTs
issued before a logout: stamp the user on removal, and refuse any invite whose
signed issue timestamp predates the stamp.

Revision ID: 0130
Revises: 0129
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0130"
down_revision: Union[str, None] = "0129"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("org_membership_revoked_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "org_membership_revoked_at")
