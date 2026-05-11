from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class SogcChange(Base):
    __tablename__ = "sogc_changes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    sogc_publication_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("sogc_publications.id", ondelete="CASCADE"), nullable=False, index=True
    )
    change_type: Mapped[str] = mapped_column(String(32), nullable=False)
    keywords_matched: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    publication: Mapped["SogcPublication"] = relationship(  # noqa: F821
        "SogcPublication", back_populates="changes"
    )
