"""rename score fields and add user_views

Revision ID: 0032
Revises: 0031
Create Date: 2026-03-27
"""
from alembic import op
import sqlalchemy as sa

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None

def upgrade() -> None:
    # Rename in companies table
    op.alter_column("companies", "zefix_score", new_column_name="flex_score")
    op.alter_column("companies", "zefix_score_breakdown", new_column_name="flex_score_breakdown")
    op.alter_column("companies", "zefix_scored_at", new_column_name="flex_scored_at")
    op.alter_column("companies", "claude_score", new_column_name="ai_score")
    op.alter_column("companies", "claude_category", new_column_name="ai_category")
    op.alter_column("companies", "claude_freeform", new_column_name="ai_freeform")
    op.alter_column("companies", "claude_scored_at", new_column_name="ai_scored_at")
    op.alter_column("companies", "website_match_score", new_column_name="web_score")
    op.alter_column("companies", "proposal_status", new_column_name="contact_status")
    # Rename in org_company_state
    op.alter_column("org_company_state", "website_match_score", new_column_name="web_score")
    op.alter_column("org_company_state", "proposal_status", new_column_name="contact_status")
    # Rename in user_company_state
    op.alter_column("user_company_state", "claude_score", new_column_name="ai_score")
    op.alter_column("user_company_state", "claude_category", new_column_name="ai_category")
    op.alter_column("user_company_state", "claude_freeform", new_column_name="ai_freeform")
    op.alter_column("user_company_state", "claude_scored_at", new_column_name="ai_scored_at")
    # Create user_views table
    op.create_table(
        "user_views",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("filters_json", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

def downgrade() -> None:
    op.drop_table("user_views")
    op.alter_column("user_company_state", "ai_scored_at", new_column_name="claude_scored_at")
    op.alter_column("user_company_state", "ai_freeform", new_column_name="claude_freeform")
    op.alter_column("user_company_state", "ai_category", new_column_name="claude_category")
    op.alter_column("user_company_state", "ai_score", new_column_name="claude_score")
    op.alter_column("org_company_state", "contact_status", new_column_name="proposal_status")
    op.alter_column("org_company_state", "web_score", new_column_name="website_match_score")
    op.alter_column("companies", "contact_status", new_column_name="proposal_status")
    op.alter_column("companies", "web_score", new_column_name="website_match_score")
    op.alter_column("companies", "ai_scored_at", new_column_name="claude_scored_at")
    op.alter_column("companies", "ai_freeform", new_column_name="claude_freeform")
    op.alter_column("companies", "ai_category", new_column_name="claude_category")
    op.alter_column("companies", "ai_score", new_column_name="claude_score")
    op.alter_column("companies", "flex_scored_at", new_column_name="zefix_scored_at")
    op.alter_column("companies", "flex_score_breakdown", new_column_name="zefix_score_breakdown")
    op.alter_column("companies", "flex_score", new_column_name="zefix_score")
