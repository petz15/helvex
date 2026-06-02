from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class CompanyWebPage(Base):
    """One crawled page per company (homepage, impressum, privacy, other).

    Raw HTML is stored in S3 (s3_bucket_crawl) via s3_key_html.
    needs_extraction=True flags this page for the future web_extract job
    which reads S3 HTML and writes structured data (contacts, services, etc.).
    """

    __tablename__ = "company_web_pages"
    __table_args__ = (
        Index("ix_company_web_pages_company_type", "company_id", "page_type"),
        # Partial index — only unextracted pages, avoids scanning the full table
        Index(
            "ix_company_web_pages_needs_extraction",
            "needs_extraction",
            postgresql_where="needs_extraction = TRUE",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    url_candidate_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("company_url_candidates.id", ondelete="SET NULL"), nullable=True
    )
    # homepage | impressum | privacy | other
    page_type: Mapped[str] = mapped_column(String(32), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    # URL after any HTTP redirects
    final_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    crawled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Detected page language from <html lang> or meta tag
    lang: Mapped[str | None] = mapped_column(String(16), nullable=True)
    word_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    image_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    video_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    has_contact_form: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # S3 key in s3_bucket_crawl; None if S3 is not configured or upload failed
    s3_key_html: Mapped[str | None] = mapped_column(Text, nullable=True)
    bot_blocked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    needs_extraction: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    extracted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
