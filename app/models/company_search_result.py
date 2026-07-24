from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class CompanySearchResult(Base):
    """One row per company: the raw Google/ScrapingDog search-engine results.

    Separated from the fat `companies` table (Company table normalization,
    ROADMAP) — this is search-provider data, not company master data. Global
    fact, extracted once and shared across orgs (same tier as
    company_web_extract/company_web_page), not an org-scoped overlay.

    Holds only the raw/derived *search* data. website_url/web_score/
    website_status/website_count/social_media_only (the identity *verdict*
    computed from this data, per website_status.py) stay on Company for now —
    those are addressed by the scoring/multi-tenancy rework, a separate change.
    """

    __tablename__ = "company_search_results"

    company_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("companies.id", ondelete="CASCADE"), primary_key=True
    )
    # 'serper' | 'scrapingdog'
    provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Scored, sorted [{title, link, snippet, score, position}, ...]
    results_raw: Mapped[list[dict] | None] = mapped_column(JSON, nullable=True)
    # Complete provider response (organic_results, ads, local_results, knowledge_graph, ...)
    full_raw: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Search params sent (q, gl, hl, location, ...) — kept for diagnosing bad results
    params: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # When the search was performed (was Company.website_checked_at)
    searched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
