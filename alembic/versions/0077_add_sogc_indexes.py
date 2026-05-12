"""Add composite index on sogc_publications(company_uid, pub_date) and index on sogc_changes(change_type).

Revision ID: 0077_add_sogc_indexes
Revises: 0076_add_sogc_person_graph
Create Date: 2026-05-12
"""

from alembic import op

revision = "0077_add_sogc_indexes"
down_revision = "0076_add_sogc_person_graph"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_sogc_pub_uid_date", "sogc_publications", ["company_uid", "pub_date"])
    op.create_index("ix_sogc_changes_change_type", "sogc_changes", ["change_type"])


def downgrade() -> None:
    op.drop_index("ix_sogc_pub_uid_date", table_name="sogc_publications")
    op.drop_index("ix_sogc_changes_change_type", table_name="sogc_changes")
