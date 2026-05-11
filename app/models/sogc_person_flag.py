from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class SogcPersonFlag(Base):
    __tablename__ = "sogc_person_flags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    flag_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    primary_entity_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("sogc_person_entities.id", ondelete="CASCADE"), nullable=False, index=True
    )
    secondary_entity_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("sogc_person_entities.id", ondelete="SET NULL"), nullable=True
    )
    appearance_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("sogc_person_appearances.id", ondelete="SET NULL"), nullable=True
    )

    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_resolved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    resolution_action: Mapped[str | None] = mapped_column(String(32), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reported_by_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
