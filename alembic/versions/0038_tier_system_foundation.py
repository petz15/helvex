"""Add tier system foundation: org credit ledger, org_members, extended org fields.

Revision ID: 0038
Revises: 0037
Create Date: 2026-04-01

This migration is purely additive — no existing columns are renamed or dropped.
Existing code continues to work unchanged.

What this adds:
- organizations: credits_balance, subscription fields, tier entitlements,
                 web_results_private_months, verified_business, verified_domain,
                 zefix_uid, custom_features (JSONB)
- NEW TABLE org_members: many-to-many user↔org with per-org role
- NEW TABLE org_credit_transactions: full credit audit ledger

Deferred to Session 3:
- Rename users.org_id → users.active_org_id
- Drop users.org_role (role moves to org_members.role)
- Data migration: backfill org_members from existing users.org_id / org_role
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0038"
down_revision: Union[str, None] = "0037"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── organizations: new columns ──────────────────────────────────────────
    op.add_column("organizations", sa.Column("subscription_billing_cycle", sa.String(10), nullable=False, server_default="monthly"))
    op.add_column("organizations", sa.Column("subscription_period_end", sa.DateTime(timezone=True), nullable=True))
    op.add_column("organizations", sa.Column("credits_balance", sa.BigInteger(), nullable=False, server_default="0"))
    op.add_column("organizations", sa.Column("monthly_rescore_used", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("organizations", sa.Column("monthly_rescore_reset_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("organizations", sa.Column("web_results_private_months", sa.Float(), nullable=False, server_default="0"))
    op.add_column("organizations", sa.Column("verified_business", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("organizations", sa.Column("verified_domain", sa.String(255), nullable=True))
    op.add_column("organizations", sa.Column("zefix_uid", sa.String(64), nullable=True))
    op.add_column("organizations", sa.Column("custom_features", JSONB(), nullable=True))

    # ── org_members ─────────────────────────────────────────────────────────
    op.create_table(
        "org_members",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(32), nullable=False, server_default="member"),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id", "user_id", name="uq_org_members_org_user"),
    )
    op.create_index("ix_org_members_org_id", "org_members", ["org_id"])
    op.create_index("ix_org_members_user_id", "org_members", ["user_id"])

    # ── org_credit_transactions ─────────────────────────────────────────────
    op.create_table(
        "org_credit_transactions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("type", sa.String(20), nullable=False),
        sa.Column("action_type", sa.String(50), nullable=True),
        sa.Column("reference_id", sa.String(255), nullable=True),
        sa.Column("credits_before", sa.BigInteger(), nullable=False),
        sa.Column("credits_after", sa.BigInteger(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_org_credit_transactions_org_id_created_at", "org_credit_transactions", ["org_id", "created_at"])
    op.create_index("ix_org_credit_transactions_type", "org_credit_transactions", ["type"])


def downgrade() -> None:
    # org_credit_transactions
    op.drop_index("ix_org_credit_transactions_type", table_name="org_credit_transactions")
    op.drop_index("ix_org_credit_transactions_org_id_created_at", table_name="org_credit_transactions")
    op.drop_table("org_credit_transactions")

    # org_members
    op.drop_index("ix_org_members_user_id", table_name="org_members")
    op.drop_index("ix_org_members_org_id", table_name="org_members")
    op.drop_table("org_members")

    # organizations columns
    op.drop_column("organizations", "custom_features")
    op.drop_column("organizations", "zefix_uid")
    op.drop_column("organizations", "verified_domain")
    op.drop_column("organizations", "verified_business")
    op.drop_column("organizations", "web_results_private_months")
    op.drop_column("organizations", "monthly_rescore_reset_at")
    op.drop_column("organizations", "monthly_rescore_used")
    op.drop_column("organizations", "credits_balance")
    op.drop_column("organizations", "subscription_period_end")
    op.drop_column("organizations", "subscription_billing_cycle")
