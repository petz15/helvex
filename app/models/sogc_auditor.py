from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class SogcAuditor(Base):
    __tablename__ = "sogc_auditors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    sogc_change_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("sogc_changes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sogc_publication_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("sogc_publications.id", ondelete="CASCADE"), nullable=False, index=True
    )
    company_uid: Mapped[str | None] = mapped_column(
        String(20), ForeignKey("companies.uid", ondelete="SET NULL"), nullable=True, index=True
    )
    company_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("companies.id", ondelete="SET NULL"), nullable=True, index=True
    )

    pub_date: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    change_type: Mapped[str] = mapped_column(String(32), nullable=False)

    auditor_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    auditor_uid: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    auditor_legal_form: Mapped[str | None] = mapped_column(String(128), nullable=True)
    auditor_location: Mapped[str | None] = mapped_column(String(256), nullable=True)
    auditor_name_normalized: Mapped[str | None] = mapped_column(String(512), nullable=True, index=True)
    is_current: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
