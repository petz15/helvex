"""Add payment_card_info_json to users for storing masked card metadata."""
import sqlalchemy as sa
from alembic import op

revision = '0064'
down_revision = '0063'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('users', sa.Column('payment_card_info_json', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('users', 'payment_card_info_json')
