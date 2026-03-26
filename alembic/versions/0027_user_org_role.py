"""Add org_role to users for within-org authorization.

Revision ID: 0027
Revises: 0026
Create Date: 2026-03-26
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0027"
down_revision: Union[str, None] = "0026"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("org_role", sa.String(32), nullable=False, server_default="member"),
    )
    op.create_index("ix_users_org_id_org_role", "users", ["org_id", "org_role"])


def downgrade() -> None:
    op.drop_index("ix_users_org_id_org_role", table_name="users")
    op.drop_column("users", "org_role")
