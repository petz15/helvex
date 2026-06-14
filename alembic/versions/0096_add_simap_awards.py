"""Add simap_awards and simap_award_vendors tables.

Revision ID: 0096
Revises: 0095
Create Date: 2026-06-14
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0096"
down_revision: str = "0095"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "simap_awards",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("simap_project_id", sa.String(36), nullable=False, unique=True),
        sa.Column("simap_publication_id", sa.String(36), nullable=False),
        sa.Column("project_number", sa.String(50), nullable=True),
        sa.Column("publication_number", sa.String(50), nullable=True),
        sa.Column("publication_date", sa.Date(), nullable=False),
        sa.Column("award_decision_date", sa.Date(), nullable=True),
        sa.Column("pub_type", sa.String(30), nullable=True),
        sa.Column("process_type", sa.String(30), nullable=True),
        sa.Column("project_type", sa.String(30), nullable=True),
        sa.Column("project_subtype", sa.String(30), nullable=True),
        sa.Column("creation_language", sa.String(2), nullable=True),
        sa.Column("title_de", sa.Text(), nullable=True),
        sa.Column("title_fr", sa.Text(), nullable=True),
        sa.Column("title_it", sa.Text(), nullable=True),
        sa.Column("proc_office_name_de", sa.Text(), nullable=True),
        sa.Column("proc_office_name_fr", sa.Text(), nullable=True),
        sa.Column("proc_office_name_it", sa.Text(), nullable=True),
        sa.Column("order_description_de", sa.Text(), nullable=True),
        sa.Column("order_description_fr", sa.Text(), nullable=True),
        sa.Column("order_description_it", sa.Text(), nullable=True),
        sa.Column("cpv_code", sa.String(10), nullable=True),
        sa.Column("number_of_submissions", sa.Integer(), nullable=True),
        sa.Column("total_price_selection", sa.String(60), nullable=True),
        sa.Column("total_price_range_min", sa.Numeric(15, 2), nullable=True),
        sa.Column("total_price_range_max", sa.Numeric(15, 2), nullable=True),
        sa.Column("total_price_currency", sa.String(5), nullable=True),
        sa.Column("lot_number", sa.Integer(), nullable=True),
        sa.Column("lot_title_de", sa.Text(), nullable=True),
        sa.Column("lot_title_fr", sa.Text(), nullable=True),
        sa.Column("lot_title_it", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_simap_awards_simap_project_id", "simap_awards", ["simap_project_id"])
    op.create_index("ix_simap_awards_publication_date", "simap_awards", ["publication_date"])

    op.create_table(
        "simap_award_vendors",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "award_id",
            sa.Integer(),
            sa.ForeignKey("simap_awards.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "company_id",
            sa.Integer(),
            sa.ForeignKey("companies.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("simap_vendor_id", sa.String(36), nullable=False),
        sa.Column("vendor_name", sa.String(512), nullable=False),
        sa.Column("vendor_uid", sa.String(20), nullable=True),
        sa.Column("vendor_country", sa.String(2), nullable=True),
        sa.Column("vendor_city", sa.String(100), nullable=True),
        sa.Column("vendor_postal_code", sa.String(10), nullable=True),
        sa.Column("price", sa.Numeric(15, 2), nullable=True),
        sa.Column("price_currency", sa.String(5), nullable=True),
        sa.Column("rank", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("award_id", "simap_vendor_id", name="uq_simap_award_vendor"),
    )
    op.create_index("ix_simap_award_vendors_award_id", "simap_award_vendors", ["award_id"])
    op.create_index("ix_simap_award_vendors_company_id", "simap_award_vendors", ["company_id"])


def downgrade() -> None:
    op.drop_table("simap_award_vendors")
    op.drop_table("simap_awards")
