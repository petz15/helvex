"""add truncate column to boilerplate_patterns

Revision ID: 0085
Revises: 0084
Create Date: 2026-05-21
"""
from alembic import op
import sqlalchemy as sa

revision = "0085"
down_revision = "0084"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "boilerplate_patterns",
        sa.Column("truncate", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade():
    op.drop_column("boilerplate_patterns", "truncate")
