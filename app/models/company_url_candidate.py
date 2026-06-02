from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class CompanyUrlCandidate(Base):
    """One URL candidate per company from Serper.dev search results.

    Multiple candidates can exist per company; exactly one has status='selected'
    at any time and is used by the crawler. Others remain 'pending' for manual
    re-selection or future promotion.
    """

    __tablename__ = "company_url_candidates"
    __table_args__ = (
        UniqueConstraint("company_id", "url", name="company_url_candidates_company_url_key"),
        Index("ix_company_url_candidates_company_status", "company_id", "status"),
        Index("ix_company_url_candidates_status_score", "status", "score"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    url: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    snippet: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Score from scoring.py (0-100); higher = better match for this company
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Rank in original Serper results (0 = first result)
    position: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # pending | selected | crawling | crawled | rejected | revisit
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    # 'serper' | 'manual'
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="serper")
    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_crawled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    crawl_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
