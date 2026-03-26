"""Create org_company_state, user_company_state, org_settings overlay tables;
add org_id/user_id to notes and audit_log; add org_id to boilerplate_patterns.

Revision ID: 0028
Revises: 0027
Create Date: 2026-03-26
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0028"
down_revision: Union[str, None] = "0027"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── org_company_state ──────────────────────────────────────────────────────
    op.create_table(
        "org_company_state",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("org_id", sa.Integer, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("company_id", sa.Integer, sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        # Workflow
        sa.Column("tags", sa.Text, nullable=True),
        sa.Column("review_status", sa.String(64), nullable=True),
        sa.Column("proposal_status", sa.String(64), nullable=True),
        sa.Column("contact_name", sa.String(256), nullable=True),
        sa.Column("contact_email", sa.String(256), nullable=True),
        sa.Column("contact_phone", sa.String(64), nullable=True),
        # Google scoring
        sa.Column("website_url", sa.String(512), nullable=True),
        sa.Column("website_match_score", sa.Float, nullable=True),
        sa.Column("google_search_results_raw", sa.Text, nullable=True),
        sa.Column("website_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("social_media_only", sa.Boolean, nullable=True),
        # Timestamps
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_unique_constraint("uq_org_company_state", "org_company_state", ["org_id", "company_id"])
    op.create_index("ix_org_company_state_org_id", "org_company_state", ["org_id"])
    op.create_index("ix_org_company_state_company_id", "org_company_state", ["company_id"])
    op.create_index("ix_org_company_state_review_status", "org_company_state", ["org_id", "review_status"])
    op.create_index("ix_org_company_state_proposal_status", "org_company_state", ["org_id", "proposal_status"])

    # ── user_company_state ─────────────────────────────────────────────────────
    op.create_table(
        "user_company_state",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("company_id", sa.Integer, sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("org_id", sa.Integer, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        # Claude outputs
        sa.Column("claude_score", sa.Float, nullable=True),
        sa.Column("claude_category", sa.String(128), nullable=True),
        sa.Column("claude_freeform", sa.Text, nullable=True),
        sa.Column("claude_scored_at", sa.DateTime(timezone=True), nullable=True),
        # Personal override
        sa.Column("personal_score_override", sa.Float, nullable=True),
        # Timestamps
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_unique_constraint("uq_user_company_state", "user_company_state", ["user_id", "company_id"])
    op.create_index("ix_user_company_state_user_id", "user_company_state", ["user_id"])
    op.create_index("ix_user_company_state_company_id", "user_company_state", ["company_id"])
    op.create_index("ix_user_company_state_org_id", "user_company_state", ["org_id"])

    # ── org_settings ───────────────────────────────────────────────────────────
    op.create_table(
        "org_settings",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("org_id", sa.Integer, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("key", sa.String(128), nullable=False),
        sa.Column("value", sa.Text, nullable=True),
    )
    op.create_unique_constraint("uq_org_settings_key", "org_settings", ["org_id", "key"])
    op.create_index("ix_org_settings_org_id", "org_settings", ["org_id"])

    # ── notes: add user_id + org_id ────────────────────────────────────────────
    op.add_column("notes", sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True))
    op.add_column("notes", sa.Column("org_id", sa.Integer, sa.ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True))

    # ── boilerplate_patterns: add org_id ──────────────────────────────────────
    op.add_column("boilerplate_patterns", sa.Column("org_id", sa.Integer, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True))
    op.create_index("ix_boilerplate_patterns_org_id", "boilerplate_patterns", ["org_id"])

    # ── audit_log: add org_id ─────────────────────────────────────────────────
    op.add_column("audit_log", sa.Column("org_id", sa.Integer, sa.ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True))
    op.create_index("ix_audit_log_org_id", "audit_log", ["org_id"])


def downgrade() -> None:
    op.drop_index("ix_audit_log_org_id", table_name="audit_log")
    op.drop_column("audit_log", "org_id")

    op.drop_index("ix_boilerplate_patterns_org_id", table_name="boilerplate_patterns")
    op.drop_column("boilerplate_patterns", "org_id")

    op.drop_column("notes", "org_id")
    op.drop_column("notes", "user_id")

    op.drop_index("ix_org_settings_org_id", table_name="org_settings")
    op.drop_constraint("uq_org_settings_key", "org_settings", type_="unique")
    op.drop_table("org_settings")

    op.drop_index("ix_user_company_state_org_id", table_name="user_company_state")
    op.drop_index("ix_user_company_state_company_id", table_name="user_company_state")
    op.drop_index("ix_user_company_state_user_id", table_name="user_company_state")
    op.drop_constraint("uq_user_company_state", "user_company_state", type_="unique")
    op.drop_table("user_company_state")

    op.drop_index("ix_org_company_state_proposal_status", table_name="org_company_state")
    op.drop_index("ix_org_company_state_review_status", table_name="org_company_state")
    op.drop_index("ix_org_company_state_company_id", table_name="org_company_state")
    op.drop_index("ix_org_company_state_org_id", table_name="org_company_state")
    op.drop_constraint("uq_org_company_state", "org_company_state", type_="unique")
    op.drop_table("org_company_state")
