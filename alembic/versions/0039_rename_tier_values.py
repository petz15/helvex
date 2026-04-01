"""Rename legacy tier values to canonical names in users and organizations.

Revision ID: 0039
Revises: 0038
Create Date: 2026-04-01

Maps:
  pro          → simple
  team         → explorer
  enterprise   → strategist
  starter      → simple      (admin.py mis-alignment)
  professional → explorer    (admin.py mis-alignment)

'free' stays as-is.  'researcher', 'strategist', 'custom' are new names with
no existing rows.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0039"
down_revision: Union[str, None] = "0038"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (old_value, new_value)
_TIER_RENAMES = [
    ("pro", "simple"),
    ("team", "explorer"),
    ("enterprise", "strategist"),
    ("starter", "simple"),
    ("professional", "explorer"),
]


def upgrade() -> None:
    conn = op.get_bind()
    for old, new in _TIER_RENAMES:
        conn.execute(sa.text(f"UPDATE users SET tier = :new WHERE tier = :old"), {"old": old, "new": new})
        conn.execute(sa.text(f"UPDATE organizations SET tier = :new WHERE tier = :old"), {"old": old, "new": new})


def downgrade() -> None:
    # Reverse only the unambiguous production renames.
    conn = op.get_bind()
    reverse = [
        ("simple", "pro"),
        ("explorer", "team"),
        ("strategist", "enterprise"),
    ]
    conn = op.get_bind()
    for new, old in reverse:
        conn.execute(sa.text("UPDATE users SET tier = :old WHERE tier = :new"), {"old": old, "new": new})
        conn.execute(sa.text("UPDATE organizations SET tier = :old WHERE tier = :new"), {"old": old, "new": new})
