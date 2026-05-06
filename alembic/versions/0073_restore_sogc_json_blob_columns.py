"""Restore 8 JSON blob columns dropped in fe7a997e322a.

These columns are read by the company detail frontend page and
were mistakenly identified as unused when the analysis only
searched app/ and missed frontend/src/.

Revision ID: 0073_restore_sogc_json_blob_columns
Revises: 0072
Create Date: 2026-05-06
"""

from alembic import op
import sqlalchemy as sa

revision = "0073_restore_sogc_json_blob_columns"
down_revision = "0072"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("companies", sa.Column("sogc_pub", sa.Text(), nullable=True))
    op.add_column("companies", sa.Column("further_head_offices", sa.Text(), nullable=True))
    op.add_column("companies", sa.Column("branch_offices", sa.Text(), nullable=True))
    op.add_column("companies", sa.Column("has_taken_over", sa.Text(), nullable=True))
    op.add_column("companies", sa.Column("was_taken_over_by", sa.Text(), nullable=True))
    op.add_column("companies", sa.Column("audit_companies", sa.Text(), nullable=True))
    op.add_column("companies", sa.Column("old_names", sa.Text(), nullable=True))
    op.add_column("companies", sa.Column("translations", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("companies", "sogc_pub")
    op.drop_column("companies", "further_head_offices")
    op.drop_column("companies", "branch_offices")
    op.drop_column("companies", "has_taken_over")
    op.drop_column("companies", "was_taken_over_by")
    op.drop_column("companies", "audit_companies")
    op.drop_column("companies", "old_names")
    op.drop_column("companies", "translations")
