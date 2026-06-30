from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, PrimaryKeyConstraint, String, Text, func
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


class CompanyDirectoryData(Base):
    """Crawled content from business directory profile pages (moneyhouse, local.ch, etc.).

    One row per (company_id, url). A company can appear in multiple directories,
    yielding multiple rows. PK is (company_id, url) so re-crawls are idempotent upserts.
    Used to enrich Claude classification with external profile text, ratings, and categories.
    """

    __tablename__ = "company_directory_data"
    __table_args__ = (
        PrimaryKeyConstraint("company_id", "url"),
        Index("ix_company_directory_data_company_id", "company_id"),
        Index("ix_company_directory_data_domain", "domain"),
    )

    company_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    url: Mapped[str] = mapped_column(Text, nullable=False)
    domain: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # 'crawled' | 'failed'
    crawl_status: Mapped[str] = mapped_column(String(32), nullable=False, default="crawled")
    crawl_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Main text from trafilatura (capped at 5000 chars)
    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Meta description or first paragraph
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Numeric rating if found (e.g. 4.2)
    rating: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Number of reviews if found
    review_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Service / industry categories extracted from the page
    categories: Mapped[list[str] | None] = mapped_column(_ArrayOfText, nullable=True)
    crawled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
