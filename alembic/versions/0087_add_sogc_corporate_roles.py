"""Add sogc_corporate_roles table for companies appearing as shareholders/officers.

Revision ID: 0087
Revises: 0086
Create Date: 2026-05-26
"""

from alembic import op
import sqlalchemy as sa

revision = "0087"
down_revision = "0086"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sogc_corporate_roles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sogc_change_id", sa.Integer(), sa.ForeignKey("sogc_changes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sogc_publication_id", sa.Integer(), sa.ForeignKey("sogc_publications.id", ondelete="CASCADE"), nullable=False),
        sa.Column("entity_name", sa.String(512), nullable=True),
        sa.Column("entity_name_normalized", sa.String(512), nullable=True),
        sa.Column("entity_che", sa.String(20), nullable=True),
        sa.Column("entity_location", sa.String(256), nullable=True),
        sa.Column("entity_legal_form", sa.String(256), nullable=True),
        sa.Column("company_uid", sa.String(20), sa.ForeignKey("companies.uid", ondelete="SET NULL"), nullable=True),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id", ondelete="SET NULL"), nullable=True),
        sa.Column("company_name", sa.String(512), nullable=True),
        sa.Column("role", sa.String(512), nullable=True),
        sa.Column("role_details", sa.Text(), nullable=True),
        sa.Column("raw_excerpt", sa.Text(), nullable=True),
        sa.Column("pub_date", sa.String(20), nullable=True),
        sa.Column("change_type", sa.String(40), nullable=True),
        sa.Column("is_current", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index("ix_sogc_corporate_roles_sogc_change_id", "sogc_corporate_roles", ["sogc_change_id"])
    op.create_index("ix_sogc_corporate_roles_sogc_publication_id", "sogc_corporate_roles", ["sogc_publication_id"])
    op.create_index("ix_sogc_corporate_roles_entity_name_normalized", "sogc_corporate_roles", ["entity_name_normalized"])
    op.create_index("ix_sogc_corporate_roles_entity_che", "sogc_corporate_roles", ["entity_che"])
    op.create_index("ix_sogc_corporate_roles_company_uid", "sogc_corporate_roles", ["company_uid"])
    op.create_index("ix_sogc_corporate_roles_company_id", "sogc_corporate_roles", ["company_id"])
    op.create_index("ix_sogc_corporate_roles_pub_date", "sogc_corporate_roles", ["pub_date"])


def downgrade() -> None:
    op.drop_table("sogc_corporate_roles")
