"""add company_id integer FK to sogc_person_appearances and sogc_auditors

Revision ID: 0089
Revises: 0088
Create Date: 2026-05-28
"""
from alembic import op
import sqlalchemy as sa

revision = "0089"
down_revision = "0088"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sogc_person_appearances",
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id", ondelete="SET NULL"), nullable=True),
    )
    op.create_index("ix_sogc_person_appearances_company_id", "sogc_person_appearances", ["company_id"])
    op.execute(
        """
        UPDATE sogc_person_appearances spa
        SET company_id = c.id
        FROM companies c
        WHERE c.uid = spa.company_uid
          AND spa.company_id IS NULL
        """
    )

    op.add_column(
        "sogc_auditors",
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id", ondelete="SET NULL"), nullable=True),
    )
    op.create_index("ix_sogc_auditors_company_id", "sogc_auditors", ["company_id"])
    op.execute(
        """
        UPDATE sogc_auditors sa
        SET company_id = c.id
        FROM companies c
        WHERE c.uid = sa.company_uid
          AND sa.company_id IS NULL
        """
    )


def downgrade() -> None:
    op.drop_index("ix_sogc_auditors_company_id", table_name="sogc_auditors")
    op.drop_column("sogc_auditors", "company_id")
    op.drop_index("ix_sogc_person_appearances_company_id", table_name="sogc_person_appearances")
    op.drop_column("sogc_person_appearances", "company_id")
