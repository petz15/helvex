"""Add critical indexes for common filters and queries."""
from alembic import op

revision = '0063'
down_revision = '0062'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Critical missing indexes for frequently-filtered columns
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_companies_website_url "
        "ON companies (website_url)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_companies_business_model "
        "ON companies (business_model)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_companies_first_sogc_date "
        "ON companies (first_sogc_date)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_companies_sogc_date "
        "ON companies (sogc_date)"
    )

    # Composite indexes for common filter + sort combinations
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_companies_canton_combined_score "
        "ON companies (canton, combined_score DESC NULLS LAST)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_companies_review_status_updated_at "
        "ON companies (review_status, updated_at DESC NULLS LAST)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_companies_contact_status_updated_at "
        "ON companies (contact_status, updated_at DESC NULLS LAST)"
    )

    # Partial indexes for hot path queries (smaller, faster)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_companies_active "
        "ON companies (id) "
        "WHERE status NOT IN ('DELETED', 'DISSOLVED', 'LIQUIDATION')"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_companies_has_website "
        "ON companies (combined_score DESC NULLS LAST) "
        "WHERE website_url IS NOT NULL"
    )

    # Analyze for query planner
    op.execute("ANALYSE companies")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_companies_has_website")
    op.execute("DROP INDEX IF EXISTS ix_companies_active")
    op.execute("DROP INDEX IF EXISTS ix_companies_contact_status_updated_at")
    op.execute("DROP INDEX IF EXISTS ix_companies_review_status_updated_at")
    op.execute("DROP INDEX IF EXISTS ix_companies_canton_combined_score")
    op.execute("DROP INDEX IF EXISTS ix_companies_sogc_date")
    op.execute("DROP INDEX IF EXISTS ix_companies_first_sogc_date")
    op.execute("DROP INDEX IF EXISTS ix_companies_business_model")
    op.execute("DROP INDEX IF EXISTS ix_companies_website_url")
