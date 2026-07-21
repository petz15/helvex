from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class DirectoryCrawlDomain(Base):
    """Managed list of directory domains to crawl for company profile context.

    Populated via two paths:
      - 'manual'          — seeded from the hardcoded initial list or added by an admin
      - 'auto_discovered' — inserted by the discover_directory_domains job based on
                            frequency of occurrence in company_url_candidates

    Only rows with status='approved' are used by the directory_crawl job.
    pending_review rows sit in the admin queue until approved or rejected.
    """

    __tablename__ = "directory_crawl_domains"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    value: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    # 'pending_review' | 'approved' | 'rejected'
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending_review")
    # 'manual' | 'auto_discovered'
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="manual")
    # Company count from discovery query (null for manually added)
    company_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    discovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
