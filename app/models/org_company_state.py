"""Org-shared overlay on top of the global company catalog.

Stores workflow state, contact info, and Google scoring results
per (org, company) pair. Multiple orgs can track the same catalog
company with completely independent overlays.
"""
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class OrgCompanyState(Base):
    __tablename__ = "org_company_state"
    __table_args__ = (
        UniqueConstraint("org_id", "company_id", name="uq_org_company_state"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id: Mapped[int] = mapped_column(Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    company_id: Mapped[int] = mapped_column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)

    # Workflow (editable by member+)
    tags: Mapped[str | None] = mapped_column(Text, nullable=True)
    review_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    proposal_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    contact_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    contact_email: Mapped[str | None] = mapped_column(String(256), nullable=True)
    contact_phone: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Google scoring (written by worker, paid tier)
    website_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    website_match_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    google_search_results_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    website_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    social_media_only: Mapped[bool | None] = mapped_column(nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    org: Mapped["Organization"] = relationship("Organization")  # noqa: F821
    company: Mapped["Company"] = relationship("Company")  # noqa: F821
