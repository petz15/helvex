"""Add business_model field to companies.

Stores rule-based business model classification derived from purpose text:
  'b2b'   — company serves other businesses
  'b2c'   — company serves end consumers
  'b2g'   — company serves government / public sector
  'mixed' — multiple signals detected
  NULL    — not yet classified / insufficient data

Populated by the classify_business_model job.

Revision ID: 0057_add_business_model
Revises: 0056_add_pending_downgrade_tier
Create Date: 2026-04-18
"""
from alembic import op
import sqlalchemy as sa

revision = "0057_add_business_model"
down_revision = "0056_add_pending_downgrade_tier"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("companies", sa.Column("business_model", sa.String(16), nullable=True))
    op.create_index("ix_companies_business_model", "companies", ["business_model"])


def downgrade():
    op.drop_index("ix_companies_business_model", table_name="companies")
    op.drop_column("companies", "business_model")
