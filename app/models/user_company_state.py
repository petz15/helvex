"""Private per-user overlay on top of the global company catalog.

Stores Claude classification results and personal score overrides
per (user, company) pair. Completely private — other users in the
same org cannot read or write this data.
"""
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class UserCompanyState(Base):
    __tablename__ = "user_company_state"
    __table_args__ = (
        UniqueConstraint("user_id", "company_id", name="uq_user_company_state"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    company_id: Mapped[int] = mapped_column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    # org_id stored for fast scoping/cleanup when a user leaves an org
    org_id: Mapped[int] = mapped_column(Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)

    # Private AI outputs
    ai_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    ai_category: Mapped[str | None] = mapped_column(String(128), nullable=True)
    ai_freeform: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_scored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Personal score override (null = no override, use catalog flex_score)
    personal_score_override: Mapped[float | None] = mapped_column(Float, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    user: Mapped["User"] = relationship("User")  # noqa: F821
    company: Mapped["Company"] = relationship("Company")  # noqa: F821
    org: Mapped["Organization"] = relationship("Organization")  # noqa: F821
