"""Add web crawler tables: company_url_candidates, company_crawl_state, company_web_pages.

Revision ID: 0091
Revises: 0090
Create Date: 2026-06-02
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0091"
down_revision: str = "0090"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── company_url_candidates ─────────────────────────────────────────────
    op.create_table(
        "company_url_candidates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("snippet", sa.Text(), nullable=True),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.Column("position", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("source", sa.String(32), nullable=False, server_default="serper"),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_crawled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("crawl_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", "url", name="company_url_candidates_company_url_key"),
    )
    op.create_index("ix_company_url_candidates_company_id", "company_url_candidates", ["company_id"])
    op.create_index("ix_company_url_candidates_company_status", "company_url_candidates", ["company_id", "status"])
    op.create_index("ix_company_url_candidates_status_score", "company_url_candidates", ["status", "score"])

    # ── company_crawl_state ────────────────────────────────────────────────
    op.create_table(
        "company_crawl_state",
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("selected_url_id", sa.Integer(), nullable=True),
        sa.Column("crawl_status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("tier", sa.String(16), nullable=False, server_default="http"),
        sa.Column("bot_protected", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("bot_protection_type", sa.String(32), nullable=True),
        sa.Column("pages_crawled", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("last_crawled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_crawl_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("crawl_error_detail", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["selected_url_id"], ["company_url_candidates.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("company_id"),
    )
    op.create_index("ix_company_crawl_state_crawl_status", "company_crawl_state", ["crawl_status"])
    op.create_index("ix_company_crawl_state_status_tier", "company_crawl_state", ["crawl_status", "tier"])
    op.create_index("ix_company_crawl_state_selected_url_id", "company_crawl_state", ["selected_url_id"])
    op.create_index("ix_company_crawl_state_next_crawl", "company_crawl_state", ["next_crawl_at"])

    # ── company_web_pages ──────────────────────────────────────────────────
    op.create_table(
        "company_web_pages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("url_candidate_id", sa.Integer(), nullable=True),
        sa.Column("page_type", sa.String(32), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("final_url", sa.Text(), nullable=True),
        sa.Column("crawled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("lang", sa.String(16), nullable=True),
        sa.Column("word_count", sa.Integer(), nullable=True),
        sa.Column("image_count", sa.Integer(), nullable=True),
        sa.Column("video_count", sa.Integer(), nullable=True),
        sa.Column("has_contact_form", sa.Boolean(), nullable=True),
        sa.Column("s3_key_html", sa.Text(), nullable=True),
        sa.Column("bot_blocked", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("needs_extraction", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("extracted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["url_candidate_id"], ["company_url_candidates.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_company_web_pages_company_id", "company_web_pages", ["company_id"])
    op.create_index("ix_company_web_pages_company_type", "company_web_pages", ["company_id", "page_type"])
    # Partial index: only unextracted pages
    op.execute(
        "CREATE INDEX ix_company_web_pages_needs_extraction "
        "ON company_web_pages (needs_extraction) "
        "WHERE needs_extraction = TRUE"
    )


def downgrade() -> None:
    op.drop_table("company_web_pages")
    op.drop_table("company_crawl_state")
    op.drop_table("company_url_candidates")
