"""Add org_payment_methods table for org-scoped saved cards."""
import sqlalchemy as sa
from alembic import op

revision = '0065'
down_revision = '0064'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'org_payment_methods',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('org_id', sa.Integer(), nullable=False),
        sa.Column('alias_id', sa.String(length=128), nullable=False),
        sa.Column('card_info_json', sa.Text(), nullable=True),
        sa.Column('is_default', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('label', sa.String(length=128), nullable=True),
        sa.Column('added_by_user_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.ForeignKeyConstraint(['added_by_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('org_id', 'alias_id', name='uq_org_payment_method_alias'),
    )
    op.create_index('ix_org_payment_methods_org_id', 'org_payment_methods', ['org_id'])

    # Migrate existing data: for each org whose default_payment_user has a saved card,
    # create an org-scoped entry so the card is available to all admins.
    op.execute("""
        INSERT INTO org_payment_methods
            (org_id, alias_id, card_info_json, is_default, added_by_user_id, created_at)
        SELECT
            o.id,
            u.payment_customer_id,
            u.payment_card_info_json,
            TRUE,
            u.id,
            NOW()
        FROM organizations o
        JOIN users u ON u.id = o.default_payment_user_id
        WHERE o.default_payment_user_id IS NOT NULL
          AND u.payment_customer_id IS NOT NULL
          AND u.payment_customer_id != ''
        ON CONFLICT (org_id, alias_id) DO NOTHING
    """)


def downgrade() -> None:
    op.drop_index('ix_org_payment_methods_org_id', table_name='org_payment_methods')
    op.drop_table('org_payment_methods')
