"""Add directory_crawl_domains table

DB-managed list of directory domains to crawl for company profile context.
Replaces the hardcoded DIRECTORY_CRAWL_DOMAINS frozenset so admins can:
  - review auto-discovered domains (those appearing frequently in search results)
  - approve / reject without a code deploy
  - see which domains are being crawled and why

Initial approved set seeded here: current hardcoded list + sector-specific sites
(healthcare, legal, construction, automotive, hospitality, etc.).
"""
from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0112"
down_revision: Union[str, None] = "0111"
branch_labels = None
depends_on = None

# Initial approved domains — seeded as 'manual' + 'approved' so the
# directory_crawl job works on the first run without admin intervention.
_INITIAL_APPROVED: list[tuple[str, str]] = [
    # Local / general Swiss directories
    ("local.ch",              "Swiss local business directory"),
    ("search.ch",             "Swiss search & local directory"),
    ("yellowpages.ch",        "Swiss yellow pages"),
    ("yellowpages.swiss",     "Swiss yellow pages (new TLD)"),
    ("help.ch",               "Swiss local business portal"),
    ("startups.ch",           "Swiss startup directory"),
    ("pappers.ch",            "Swiss company registry aggregator"),
    # Reviews / ratings
    ("provenexpert.com",      "Review platform — service ratings"),
    ("kununu.com",            "Employer review platform"),
    ("yelp.com",              "Consumer reviews — restaurants & services"),
    # Industry databases
    ("kompass.ch",            "Kompass B2B directory (CH)"),
    ("kompass.com",           "Kompass B2B directory (international)"),
    # Treuhand / fiduciary
    ("treuhandvergleich.ch",  "Treuhand comparison & reviews"),
    ("consultingvergleich.ch","Consulting comparison & reviews"),
    ("treuhandsuisse.ch",     "Treuhand Suisse association member directory"),
    ("treuhandsuisse-zh.ch",  "Treuhand Suisse Zurich chapter"),
    ("fiduciairesuisse-vd.ch","Fiduciaire Suisse Vaud chapter"),
    ("kanzleiwelten.com",     "Law & Treuhand firm profiles"),
    # Sector-specific Swiss directories
    ("spheriq.ch",            "Swiss technology & services directory"),
    ("swiss-arc.ch",          "Swiss architecture firm directory"),
    ("swissbiotech.org",      "Swiss biotech company directory"),
    ("ofri.ch",               "Swiss crafts & trades directory"),
    ("auditorstats.ch",       "Swiss auditor / revision statistics"),
    ("ccis.ch",               "Chambre de commerce et d'industrie — member directory"),
    # Healthcare
    ("medicosearch.ch",       "Swiss medical practitioners directory"),
    # Legal
    ("anwaltssuche.ch",       "Swiss lawyer search & profiles"),
    ("jurisearch.ch",         "Swiss legal firm directory"),
    # Construction / Architecture
    ("bauindex.ch",           "Swiss construction contractor directory"),
    ("sia.ch",                "Swiss Society of Engineers and Architects member directory"),
    # Automotive
    ("agvs-upsa.ch",          "Swiss automotive trade association member directory"),
    # Manufacturing
    ("swissmechanic.ch",      "Swiss metalworking & manufacturing member directory"),
    ("swissengineering.ch",   "Swiss engineering association member directory"),
    # Gastronomy / Hospitality
    ("gastrosuisse.ch",       "GastroSuisse association member directory"),
    ("hotelleriesuisse.ch",   "HotellerieSuisse member directory"),
    # Real estate / Property management
    ("svit.ch",               "Swiss real estate management association member directory"),
]


def upgrade() -> None:
    op.create_table(
        "directory_crawl_domains",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("value", sa.String(255), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending_review"),
        sa.Column("source", sa.String(32), nullable=False, server_default="manual"),
        sa.Column("company_count", sa.Integer(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "discovered_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_directory_crawl_domains_id", "directory_crawl_domains", ["id"])
    op.create_index(
        "ix_directory_crawl_domains_value", "directory_crawl_domains", ["value"], unique=True
    )

    # Seed initial approved domains
    op.bulk_insert(
        sa.table(
            "directory_crawl_domains",
            sa.column("value", sa.String),
            sa.column("status", sa.String),
            sa.column("source", sa.String),
            sa.column("notes", sa.Text),
        ),
        [
            {"value": domain, "status": "approved", "source": "manual", "notes": notes}
            for domain, notes in _INITIAL_APPROVED
        ],
    )


def downgrade() -> None:
    op.drop_index("ix_directory_crawl_domains_value", table_name="directory_crawl_domains")
    op.drop_index("ix_directory_crawl_domains_id", table_name="directory_crawl_domains")
    op.drop_table("directory_crawl_domains")
