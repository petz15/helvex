"""Backfill org_members from existing users.org_id / org_role.

Revision ID: 0040
Revises: 0039
Create Date: 2026-04-01

Every user that has an org_id set gets an org_members row with their current
org_role.  The INSERT uses ON CONFLICT DO NOTHING so it is safe to run more
than once.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0040"
down_revision: Union[str, None] = "0039"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("""
        INSERT INTO org_members (org_id, user_id, role, joined_at)
        SELECT org_id, id, org_role, created_at
        FROM users
        WHERE org_id IS NOT NULL
        ON CONFLICT ON CONSTRAINT uq_org_members_org_user DO NOTHING
    """))


def downgrade() -> None:
    # Remove all backfilled rows — rows added after this migration was run
    # are indistinguishable, so downgrade truncates the whole table.
    op.execute(sa.text("DELETE FROM org_members"))
