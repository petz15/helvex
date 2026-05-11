from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class SogcPersonEntity(Base):
    __tablename__ = "sogc_person_entities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    normalized_key: Mapped[str] = mapped_column(String(512), unique=True, nullable=False, index=True)

    lastname: Mapped[str | None] = mapped_column(String(256), nullable=True)
    firstname: Mapped[str | None] = mapped_column(String(256), nullable=True)
    hometown_municipality: Mapped[str | None] = mapped_column(String(256), nullable=True)
    is_foreign: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    nationality: Mapped[str | None] = mapped_column(String(128), nullable=True)

    confidence_level: Mapped[str] = mapped_column(String(16), nullable=False, default="medium", index=True)
    is_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verified_by_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    appearance_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active_company_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    linkedin_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    linkedin_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    merged_into_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("sogc_person_entities.id", ondelete="SET NULL"), nullable=True, index=True
    )

    identity_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
