"""add truncate column to boilerplate_patterns

Revision ID: 0085
Revises: fe7a997e322a
Create Date: 2026-05-21
"""
from alembic import op
import sqlalchemy as sa

revision = "0085"
down_revision = "fe7a997e322a"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "boilerplate_patterns",
        sa.Column("truncate", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade():
    op.drop_column("boilerplate_patterns", "truncate")
