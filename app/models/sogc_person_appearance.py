from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class SogcPersonAppearance(Base):
    __tablename__ = "sogc_person_appearances"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    person_entity_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("sogc_person_entities.id", ondelete="CASCADE"), nullable=False, index=True
    )
    entity_override_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("sogc_person_entities.id", ondelete="SET NULL"), nullable=True, index=True
    )
    sogc_change_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("sogc_changes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sogc_publication_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("sogc_publications.id", ondelete="CASCADE"), nullable=False, index=True
    )
    company_uid: Mapped[str | None] = mapped_column(
        String(20), ForeignKey("companies.uid", ondelete="SET NULL"), nullable=True, index=True
    )

    pub_date: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    change_type: Mapped[str] = mapped_column(String(32), nullable=False)
    change_subtype: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)

    role: Mapped[str | None] = mapped_column(String(256), nullable=True)
    role_category: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    signature_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    bisher_role: Mapped[str | None] = mapped_column(String(256), nullable=True)
    residence_municipality: Mapped[str | None] = mapped_column(String(256), nullable=True)
    is_current: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    title: Mapped[str | None] = mapped_column(String(64), nullable=True)
    raw_excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Structured fields parsed from the [bisher: ...] annotation.
    # Populated when a publication mutates an existing person entry.
    # Used by the bisher-first entity resolver to hard-link appearances
    # that belong to the same person despite different normalized keys
    # (e.g. name changes: Müller → Müller-Schneider).
    bisher_residence_municipality: Mapped[str | None] = mapped_column(String(256), nullable=True)
    bisher_lastname: Mapped[str | None] = mapped_column(String(256), nullable=True)
    bisher_firstname: Mapped[str | None] = mapped_column(String(256), nullable=True)
    bisher_is_foreign: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    bisher_nationality: Mapped[str | None] = mapped_column(String(128), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
