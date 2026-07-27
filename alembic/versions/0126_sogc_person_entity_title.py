"""Add title column to sogc_person_entities.

sogc_person_appearances already had a `title` column (Dr./Prof./lic./etc.,
extracted and stripped from the raw name so it doesn't pollute search on
lastname/firstname) but the canonical, deduplicated sogc_person_entities
table never got the same column — titles had nowhere to live at the entity
level. Forward-only: existing entities backfill as NULL; new imports and the
extractor fixes in sogc_person_extractor.py populate it going forward.

Revision ID: 0126
Revises: 0125
Create Date: 2026-07-26
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0126"
down_revision: str = "0125"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sogc_person_entities", sa.Column("title", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("sogc_person_entities", "title")
