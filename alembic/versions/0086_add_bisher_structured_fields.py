"""Add structured bisher fields to sogc_person_appearances.

Parses the [bisher: ...] annotation into discrete columns so the
entity resolver can find hard-linked appearances without free-text
matching.  Also adds an index on (company_uid, lastname) to support
efficient bisher lookups within the same company.

Revision ID: 0086
Revises: 0085
Create Date: 2026-05-21
"""

from alembic import op
import sqlalchemy as sa

revision = "0086"
down_revision = "0085"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sogc_person_appearances", sa.Column("bisher_residence_municipality", sa.String(256), nullable=True))
    op.add_column("sogc_person_appearances", sa.Column("bisher_lastname", sa.String(256), nullable=True))
    op.add_column("sogc_person_appearances", sa.Column("bisher_firstname", sa.String(256), nullable=True))
    op.add_column("sogc_person_appearances", sa.Column("bisher_is_foreign", sa.Boolean(), nullable=True))
    op.add_column("sogc_person_appearances", sa.Column("bisher_nationality", sa.String(128), nullable=True))
    op.create_index(
        "ix_sogc_person_appearances_company_lastname",
        "sogc_person_appearances",
        ["company_uid", "lastname"],
    )


def downgrade() -> None:
    op.drop_index("ix_sogc_person_appearances_company_lastname", "sogc_person_appearances")
    op.drop_column("sogc_person_appearances", "bisher_nationality")
    op.drop_column("sogc_person_appearances", "bisher_is_foreign")
    op.drop_column("sogc_person_appearances", "bisher_firstname")
    op.drop_column("sogc_person_appearances", "bisher_lastname")
    op.drop_column("sogc_person_appearances", "bisher_residence_municipality")
