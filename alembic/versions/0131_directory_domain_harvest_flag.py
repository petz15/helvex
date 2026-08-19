"""Separate "block from candidate selection" from "harvest profile data".

`directory_crawl_domains.status='approved'` was doing double duty: the blocklist
(`get_effective_crawl_blocklist`) read approved rows as "never a company's own
website", while `handle_directory_crawl` read the *same* rows as "harvest profile
data from this". There was therefore no way to express "block, but never harvest"
— the wanted state for kompass.ch, moneyland.ch and business-monitor.ch, which
carry no useful data — versus "block AND harvest", wanted for local.ch and
treuhandvergleich.ch, whose reviews are valuable.

`harvest` is independent of `status`. Existing approved rows are backfilled to
harvest=TRUE, preserving today's behaviour exactly (approved implied harvestable),
so this migration is behaviour-neutral on its own.

Revision ID: 0131
Revises: 0130
"""
from alembic import op
import sqlalchemy as sa

revision = "0131"
down_revision = "0130"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "directory_crawl_domains",
        sa.Column("harvest", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    # Preserve current semantics: before this column, approved == harvestable.
    op.execute(
        "UPDATE directory_crawl_domains SET harvest = TRUE WHERE status = 'approved'"
    )


def downgrade() -> None:
    op.drop_column("directory_crawl_domains", "harvest")
