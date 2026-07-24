from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class OrgCompanyAi(Base):
    """Org-shared AI classification result for a company.

    AI scoring is paid (Claude Haiku) and computed once per org, then reused by
    every member — never per-user. `ai_score` is the promoted, sortable/
    filterable value that feeds company_score.combined_score; `ai_data` holds
    everything else (category, freeform notes, and future per-company summaries
    or named prompt-scores) without needing a migration per new AI feature.
    """

    __tablename__ = "org_company_ai"

    org_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True
    )
    company_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("companies.id", ondelete="CASCADE"), primary_key=True
    )
    ai_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # {"ai_category": ..., "ai_freeform": ..., ...} — evolving fields, no schema migration needed.
    ai_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
