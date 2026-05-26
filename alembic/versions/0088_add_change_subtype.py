"""Add change_subtype to sogc_changes, sogc_person_appearances, sogc_corporate_roles.

Classifies person-level changes as: new_appointment, resignation, role_change,
capital_change, name_change, address_change, other.

Revision ID: 0088
Revises: 0087
Create Date: 2026-05-26
"""

from alembic import op
import sqlalchemy as sa

revision = "0088"
down_revision = "0087"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sogc_changes", sa.Column("change_subtype", sa.String(32), nullable=True))
    op.create_index("ix_sogc_changes_change_subtype", "sogc_changes", ["change_subtype"])

    op.add_column("sogc_person_appearances", sa.Column("change_subtype", sa.String(32), nullable=True))
    op.create_index("ix_sogc_person_appearances_change_subtype", "sogc_person_appearances", ["change_subtype"])

    op.add_column("sogc_corporate_roles", sa.Column("change_subtype", sa.String(32), nullable=True))
    op.create_index("ix_sogc_corporate_roles_change_subtype", "sogc_corporate_roles", ["change_subtype"])


def downgrade() -> None:
    op.drop_index("ix_sogc_corporate_roles_change_subtype", "sogc_corporate_roles")
    op.drop_column("sogc_corporate_roles", "change_subtype")

    op.drop_index("ix_sogc_person_appearances_change_subtype", "sogc_person_appearances")
    op.drop_column("sogc_person_appearances", "change_subtype")

    op.drop_index("ix_sogc_changes_change_subtype", "sogc_changes")
    op.drop_column("sogc_changes", "change_subtype")
