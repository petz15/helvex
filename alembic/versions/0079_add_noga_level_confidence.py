"""Add noga_level_confidence JSONB column to companies.

Stores per-level cosine confidence from the hierarchical NOGA classifier,
e.g. {"1": 0.95, "2": 0.88, "3": 0.75, "4": 0.70, "5": 0.68}.

Revision ID: 0079
Revises: 0078
Create Date: 2026-05-14
"""

from alembic import op

revision = "0079"
down_revision = "0078"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE companies ADD COLUMN noga_level_confidence JSONB")


def downgrade() -> None:
    op.execute("ALTER TABLE companies DROP COLUMN IF EXISTS noga_level_confidence")
