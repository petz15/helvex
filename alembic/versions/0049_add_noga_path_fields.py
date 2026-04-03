"""Add noga_path and noga_path_labels to companies.

noga_path        — pipe-separated NOGA codes root→leaf, e.g. "C|26|263|2630|263001"
noga_path_labels — corresponding German labels, pipe-separated

Revision ID: 0049_add_noga_path_fields
Revises: 0048_add_job_dedup_and_heartbeat
Create Date: 2026-04-03
"""

from alembic import op
import sqlalchemy as sa

revision = "0049_add_noga_path_fields"
down_revision = "0048_add_job_dedup_and_heartbeat"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("companies", sa.Column("noga_path", sa.Text, nullable=True))
    op.add_column("companies", sa.Column("noga_path_labels", sa.Text, nullable=True))


def downgrade() -> None:
    op.drop_column("companies", "noga_path_labels")
    op.drop_column("companies", "noga_path")
