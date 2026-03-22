"""Add SaaS user fields and organizations table.

Revision ID: 0024
Revises: 0023
Create Date: 2026-03-22
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0024"
down_revision: Union[str, None] = "0023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # organizations must be created before users references it
    op.create_table(
        "organizations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("slug", sa.String(128), nullable=False),
        sa.Column("tier", sa.String(32), nullable=False, server_default="free"),
        sa.Column("payment_customer_id", sa.String(128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )

    # Extend users table
    op.add_column("users", sa.Column("email", sa.String(256), nullable=True))
    op.add_column("users", sa.Column("tier", sa.String(32), nullable=False, server_default="free"))
    op.add_column("users", sa.Column("email_verified", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("users", sa.Column("is_superadmin", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("users", sa.Column("payment_customer_id", sa.String(128), nullable=True))
    op.add_column("users", sa.Column("payment_subscription_id", sa.String(128), nullable=True))
    op.add_column("users", sa.Column("subscription_status", sa.String(32), nullable=True))
    op.add_column(
        "users",
        sa.Column(
            "org_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )

    op.create_unique_constraint("uq_users_email", "users", ["email"])
    op.create_index("ix_users_email", "users", ["email"])
    op.create_index("ix_users_org_id", "users", ["org_id"])


def downgrade() -> None:
    op.drop_index("ix_users_org_id", table_name="users")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_constraint("uq_users_email", "users", type_="unique")
    op.drop_column("users", "org_id")
    op.drop_column("users", "subscription_status")
    op.drop_column("users", "payment_subscription_id")
    op.drop_column("users", "payment_customer_id")
    op.drop_column("users", "is_superadmin")
    op.drop_column("users", "email_verified")
    op.drop_column("users", "tier")
    op.drop_column("users", "email")
    op.drop_table("organizations")
