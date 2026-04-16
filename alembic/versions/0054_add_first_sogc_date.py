"""Add first_sogc_date to track the earliest SOGC publication date.

sogc_date gets overwritten on every Zefix update (always reflects the
latest SOGC entry). first_sogc_date is set once on company creation and
never updated — it represents when the company first appeared in SOGC.

Backfills existing rows with sogc_date as a best-effort approximation.

Revision ID: 0054_add_first_sogc_date
Revises: 0053_add_activity_log
Create Date: 2026-04-16
"""
from alembic import op
import sqlalchemy as sa

revision = "0054_add_first_sogc_date"
down_revision = "0053_add_activity_log"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("companies", sa.Column("first_sogc_date", sa.String(32), nullable=True))
    op.create_index("ix_companies_first_sogc_date", "companies", ["first_sogc_date"])


def downgrade():
    op.drop_index("ix_companies_first_sogc_date", table_name="companies")
    op.drop_column("companies", "first_sogc_date")
