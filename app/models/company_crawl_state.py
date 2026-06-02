from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON, TypeDecorator

from app.database import Base


class _ArrayOfText(TypeDecorator):
    """ARRAY(Text) on PostgreSQL; JSON on SQLite for test compatibility."""
    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(ARRAY(Text))
        return dialect.type_descriptor(JSON())


class CompanyCrawlState(Base):
    """Per-company crawl control record.

    crawl_status values:
      pending       — ready to be claimed by a crawler worker
      in_progress   — currently being crawled
      crawled       — successfully crawled (pages in company_web_pages)
      bot_blocked   — hard bot block; see bot_protection_type for detail
      js_required   — httpx returned near-empty JS page; queued for Playwright
      http_error    — non-bot HTTP failure (404, 5xx, connection refused)
      timeout       — request timed out
      no_content    — page returned near-empty body
      no_website    — company has no URL candidate to crawl

    tier: 'http' | 'playwright'
    """

    __tablename__ = "company_crawl_state"
    __table_args__ = (
        Index("ix_company_crawl_state_status_tier", "crawl_status", "tier"),
        Index("ix_company_crawl_state_next_crawl", "next_crawl_at"),
    )

    company_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("companies.id", ondelete="CASCADE"), primary_key=True, nullable=False
    )
    selected_url_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("company_url_candidates.id", ondelete="SET NULL"), nullable=True, index=True
    )
    crawl_status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", index=True)
    # http | playwright
    tier: Mapped[str] = mapped_column(String(16), nullable=False, default="http")
    bot_protected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # cloudflare | captcha | http_403 | js_challenge | unknown
    bot_protection_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    pages_crawled: Mapped[list[str] | None] = mapped_column(_ArrayOfText, nullable=True)
    last_crawled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_crawl_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    crawl_error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
