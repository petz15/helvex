"""Remove username column; make email the primary login identifier.

Steps:
1. Backfill any users with NULL email (placeholder so NOT NULL can be set).
2. Make email NOT NULL.
3. Drop username column and its unique index.

Revision ID: 0031
Revises: 0030
Create Date: 2026-03-26
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0031"
down_revision: Union[str, None] = "0030"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # 1. Backfill NULL emails with a placeholder so NOT NULL can be enforced
    conn.execute(sa.text(
        "UPDATE users SET email = 'user_' || id || '@placeholder.invalid' WHERE email IS NULL"
    ))

    # 2. Make email NOT NULL
    op.alter_column("users", "email", nullable=False)

    # 3. Drop username index and column
    op.drop_index("ix_users_username", table_name="users", if_exists=True)
    op.drop_column("users", "username")


def downgrade() -> None:
    # Re-add username column (nullable, so existing rows don't break)
    op.add_column("users", sa.Column("username", sa.String(64), nullable=True))
    # Make email nullable again
    op.alter_column("users", "email", nullable=True)
    # NOTE: username uniqueness / NOT NULL constraint is NOT restored on downgrade
    # — this is intentional to avoid data loss.
