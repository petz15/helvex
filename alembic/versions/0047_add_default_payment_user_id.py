"""Add default payment user to organizations.

Revision ID: 0047_add_default_payment_user_id
Revises: 0046_drop_users_tier
Create Date: 2026-04-02
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0047_add_default_payment_user_id"
down_revision: Union[str, None] = "0046_drop_users_tier"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column("default_payment_user_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_organizations_default_payment_user_id_users",
        "organizations",
        "users",
        ["default_payment_user_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_organizations_default_payment_user_id_users", "organizations", type_="foreignkey")
    op.drop_column("organizations", "default_payment_user_id")