"""Widen simap_awards classification columns from VARCHAR(30) to VARCHAR(100).

VARCHAR(30) is too narrow for values like 'overall_performance_competition' (31 chars).

Revision ID: 0098
Revises: 0097
Create Date: 2026-06-15
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0098"
down_revision: str = "0097"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for col in ("pub_type", "process_type", "project_type", "project_subtype"):
        op.alter_column(
            "simap_awards",
            col,
            existing_type=sa.String(30),
            type_=sa.String(100),
            existing_nullable=True,
        )


def downgrade() -> None:
    for col in ("pub_type", "process_type", "project_type", "project_subtype"):
        op.alter_column(
            "simap_awards",
            col,
            existing_type=sa.String(100),
            type_=sa.String(30),
            existing_nullable=True,
        )
