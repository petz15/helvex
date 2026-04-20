"""Denormalize delimited categories into junction tables for fast queries."""
from alembic import op
import sqlalchemy as sa

revision = '0062'
down_revision = '0061'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'company_tfidf_clusters',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('company_id', sa.Integer(), nullable=False),
        sa.Column('cluster', sa.String(255), nullable=False),
        sa.ForeignKeyConstraint(['company_id'], ['companies.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_company_tfidf_clusters_cluster', 'company_tfidf_clusters', ['cluster'])
    op.create_index('ix_company_tfidf_clusters_company_id', 'company_tfidf_clusters', ['company_id'])

    op.create_table(
        'company_purpose_keywords',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('company_id', sa.Integer(), nullable=False),
        sa.Column('keyword', sa.String(255), nullable=False),
        sa.ForeignKeyConstraint(['company_id'], ['companies.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_company_purpose_keywords_keyword', 'company_purpose_keywords', ['keyword'])
    op.create_index('ix_company_purpose_keywords_company_id', 'company_purpose_keywords', ['company_id'])

    op.execute("""
        INSERT INTO company_tfidf_clusters (company_id, cluster)
        SELECT id, TRIM(UNNEST(STRING_TO_ARRAY(tfidf_cluster, '|')))
        FROM companies
        WHERE tfidf_cluster IS NOT NULL AND tfidf_cluster != ''
        ON CONFLICT DO NOTHING
    """)

    op.execute("""
        INSERT INTO company_purpose_keywords (company_id, keyword)
        SELECT id, TRIM(UNNEST(STRING_TO_ARRAY(purpose_keywords, ',')))
        FROM companies
        WHERE purpose_keywords IS NOT NULL AND purpose_keywords != ''
        ON CONFLICT DO NOTHING
    """)

    op.execute("ANALYSE company_tfidf_clusters")
    op.execute("ANALYSE company_purpose_keywords")


def downgrade() -> None:
    op.drop_table('company_purpose_keywords')
    op.drop_table('company_tfidf_clusters')
