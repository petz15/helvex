"""drop_never_read_json_blob_columns

Revision ID: fe7a997e322a
Revises: 0070
Create Date: 2026-05-03 14:41:16.993728
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'fe7a997e322a'
down_revision: Union[str, None] = '0070'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop 8 JSON blob columns that are written during Zefix import but never read
    # These columns were populated from Zefix API but never used in scoring, filtering, or display
    op.drop_column('companies', 'sogc_pub')
    op.drop_column('companies', 'further_head_offices')
    op.drop_column('companies', 'branch_offices')
    op.drop_column('companies', 'has_taken_over')
    op.drop_column('companies', 'was_taken_over_by')
    op.drop_column('companies', 'audit_companies')
    op.drop_column('companies', 'old_names')
    op.drop_column('companies', 'translations')


def downgrade() -> None:
    # Restore columns (for rollback)
    op.add_column('companies', sa.Column('sogc_pub', sa.Text(), nullable=True))
    op.add_column('companies', sa.Column('further_head_offices', sa.Text(), nullable=True))
    op.add_column('companies', sa.Column('branch_offices', sa.Text(), nullable=True))
    op.add_column('companies', sa.Column('has_taken_over', sa.Text(), nullable=True))
    op.add_column('companies', sa.Column('was_taken_over_by', sa.Text(), nullable=True))
    op.add_column('companies', sa.Column('audit_companies', sa.Text(), nullable=True))
    op.add_column('companies', sa.Column('old_names', sa.Text(), nullable=True))
    op.add_column('companies', sa.Column('translations', sa.Text(), nullable=True))
