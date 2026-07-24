from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class CompanyScore(Base):
    """Per-scope (org, optional user) materialized score set.

    Scoring/multi-tenancy rework: `companies.flex_score/web_score/combined_score`
    are global, so one org's rescore overwrites what every other org sees. This
    table holds the same three scores, but scoped — `user_id IS NULL` is the
    org-default row; `user_id = N` exists only for a user who has overridden at
    least one `scoring_*` config key (see scoring.config_resolution). AI stays
    org-level (org_company_ai) and is read into `combined_score` here, never
    recomputed per user.

    Materialized by the `rescore_scope` job — never computed inline on read.
    """

    __tablename__ = "company_score"
    __table_args__ = (
        UniqueConstraint("org_id", "user_id", "company_id", name="uq_company_score_scope"),
        Index("ix_company_score_scope_combined", "org_id", "user_id", "combined_score"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id: Mapped[int] = mapped_column(Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    # NULL = org-default scope; a specific user_id = that user's overridden scope.
    user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    company_id: Mapped[int] = mapped_column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)

    flex_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    web_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    combined_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Opaque marker of which config produced this row (e.g. a hash of the
    # resolved scoring_* dict) — lets rescore_scope skip companies whose
    # inputs haven't changed since the config was last applied. Not yet used
    # for anything beyond that cheap short-circuit.
    config_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    scored_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
