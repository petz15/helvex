"""Add billing tiers table and user billing address.

Revision ID: 0045_add_billing_tiers_and_user_billing_address
Revises: 0044
Create Date: 2026-04-02
"""

from __future__ import annotations

import json

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0045_add_billing_tiers_and_user_billing_address"
down_revision = "0044"
branch_labels = None
depends_on = None


_DEFAULT_TIERS = [
    {
        "id": 0,
        "slug": "free",
        "display_name": "Free",
        "description": "Try the core search and export features at no cost.",
        "monthly_price_chf": 0.0,
        "yearly_multiplier": 10.0,
        "topup_bonus_rate": 0.0,
        "sort_order": 0,
        "is_active": True,
        "is_public": True,
    },
    {
        "id": 1,
        "slug": "simple",
        "display_name": "Simple",
        "description": "For individuals who need a clean workspace without ads.",
        "monthly_price_chf": 6.0,
        "yearly_multiplier": 10.0,
        "topup_bonus_rate": 0.10,
        "sort_order": 1,
        "is_active": True,
        "is_public": True,
    },
    {
        "id": 2,
        "slug": "explorer",
        "display_name": "Explorer",
        "description": "Immediate LLM scoring and automated flex rescoring.",
        "monthly_price_chf": 12.0,
        "yearly_multiplier": 10.0,
        "topup_bonus_rate": 0.15,
        "sort_order": 2,
        "is_active": True,
        "is_public": True,
    },
    {
        "id": 3,
        "slug": "researcher",
        "display_name": "Researcher",
        "description": "Full LLM auto-scoring and custom ML stopwords.",
        "monthly_price_chf": 17.0,
        "yearly_multiplier": 10.0,
        "topup_bonus_rate": 0.20,
        "sort_order": 3,
        "is_active": True,
        "is_public": True,
    },
    {
        "id": 4,
        "slug": "strategist",
        "display_name": "Strategist",
        "description": "Highest priority, BYO keys, and future API access.",
        "monthly_price_chf": 37.0,
        "yearly_multiplier": 10.0,
        "topup_bonus_rate": 0.30,
        "sort_order": 4,
        "is_active": True,
        "is_public": True,
    },
    {
        "id": 5,
        "slug": "custom",
        "display_name": "Custom",
        "description": "Build a modular plan with exactly the features you need.",
        "monthly_price_chf": 1.0,
        "yearly_multiplier": 10.0,
        "topup_bonus_rate": 0.0,
        "sort_order": 5,
        "is_active": True,
        "is_public": False,
    },
]


def upgrade() -> None:
    op.add_column("users", sa.Column("billing_address_json", sa.Text(), nullable=True))

    op.create_table(
        "billing_tiers",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("slug", sa.String(length=32), nullable=False, unique=True),
        sa.Column("display_name", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("monthly_price_chf", sa.Float(), nullable=False, server_default="0"),
        sa.Column("yearly_multiplier", sa.Float(), nullable=False, server_default="10"),
        sa.Column("topup_bonus_rate", sa.Float(), nullable=False, server_default="0"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_public", sa.Boolean(), nullable=False, server_default=sa.true()),
    )

    bind = op.get_bind()
    for row in _DEFAULT_TIERS:
        bind.execute(
            sa.text(
                """
                INSERT INTO billing_tiers
                    (id, slug, display_name, description, monthly_price_chf, yearly_multiplier,
                     topup_bonus_rate, sort_order, is_active, is_public)
                VALUES
                    (:id, :slug, :display_name, :description, :monthly_price_chf, :yearly_multiplier,
                     :topup_bonus_rate, :sort_order, :is_active, :is_public)
                """
            ),
            row,
        )

    # Preserve any previously saved billing address by copying the current org-level
    # checkout address onto users in the active org when they do not already have one.
    bind.execute(
        sa.text(
            """
            UPDATE users
            SET billing_address_json = (
                SELECT organizations.billing_address_json
                FROM organizations
                WHERE organizations.id = users.org_id
            )
            WHERE billing_address_json IS NULL
              AND org_id IS NOT NULL
              AND EXISTS (
                  SELECT 1
                  FROM organizations
                  WHERE organizations.id = users.org_id
                    AND organizations.billing_address_json IS NOT NULL
              )
            """
        )
    )


def downgrade() -> None:
    op.drop_column("users", "billing_address_json")
    op.drop_table("billing_tiers")
