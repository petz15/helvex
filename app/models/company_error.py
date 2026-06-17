from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class CompanyError(Base):
    """Per-company error log for pipeline failures.

    error_source values: 'web_enrichment' | 'zefix_import' | 'geocoding' | 'noga'
    error_type values:   'enrich_failed' | 'import_failed' | 'geocode_failed' | 'extract_failed'

    company_id is nullable for job-level errors that cannot be attributed to a single company.
    ignored=True means the operator dismissed the error without a formal fix.
    """

    __tablename__ = "company_errors"
    __table_args__ = (
        Index("ix_company_errors_company_id", "company_id"),
        Index("ix_company_errors_error_source", "error_source"),
        Index("ix_company_errors_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=True
    )
    error_source: Mapped[str] = mapped_column(String(32), nullable=False)
    error_type: Mapped[str] = mapped_column(String(64), nullable=False)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    detail_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    job_run_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("job_runs.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    ignored: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
