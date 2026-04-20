"""Add indexes to optimize category stats queries."""
from alembic import op

# revision identifiers, used by Alembic.
revision = '0061_optimize_category_stats_indexes'
down_revision = '0060_purpose_keywords_array'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_companies_tfidf_cluster_combined "
        "ON companies(tfidf_cluster, combined_score)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_companies_purpose_keywords_combined "
        "ON companies(purpose_keywords, combined_score)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_companies_noga_code_noga_label "
        "ON companies(noga_code, noga_label, combined_score)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_companies_tfidf_cluster_combined")
    op.execute("DROP INDEX IF EXISTS ix_companies_purpose_keywords_combined")
    op.execute("DROP INDEX IF EXISTS ix_companies_noga_code_noga_label")
