from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ShabOldUidMiss(Base):
    """Cache of old cantonal HR numbers the UID register had no match for.

    Keyed by the number itself (not by publication) because the same old_uid
    is often cited by several sogc_publications rows (a company's later
    mutations). Checked by resolve_shab_old_uids before spending an API call —
    without this, every job run re-queries the same permanently-unmatched
    numbers from scratch, since a failed lookup leaves company_uid NULL and
    the row stays in the candidate set forever.
    """
    __tablename__ = "shab_old_uid_misses"

    old_uid: Mapped[str] = mapped_column(String(20), primary_key=True)
    failed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    attempts: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
