"""Org-shared overlay on top of the global company catalog.

Stores workflow state and contact info per (org, company) pair. Multiple orgs
can track the same catalog company with completely independent overlays.
"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
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
    contact_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    contact_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    contact_email: Mapped[str | None] = mapped_column(String(256), nullable=True)
    contact_phone: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    org: Mapped["Organization"] = relationship("Organization")  # noqa: F821
    company: Mapped["Company"] = relationship("Company")  # noqa: F821
