from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class SogcPublication(Base):
    __tablename__ = "sogc_publications"
    __table_args__ = (
        Index("ix_sogc_pub_uid_date", "company_uid", "pub_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    sogc_id: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    company_uid: Mapped[str | None] = mapped_column(
        String(20), ForeignKey("companies.uid", ondelete="SET NULL"), nullable=True, index=True
    )
    company_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("companies.id", ondelete="SET NULL"), nullable=True, index=True
    )
    pub_date: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    sub_rubric: Mapped[str | None] = mapped_column(String(8), nullable=True)
    pub_number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    text_de: Mapped[str | None] = mapped_column(Text, nullable=True)
    text_fr: Mapped[str | None] = mapped_column(Text, nullable=True)
    text_it: Mapped[str | None] = mapped_column(Text, nullable=True)
    text_en: Mapped[str | None] = mapped_column(Text, nullable=True)
    detected_language: Mapped[str | None] = mapped_column(String(8), nullable=True)
    encoding_fixed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    raw_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    preprocessed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    company: Mapped["Company"] = relationship("Company", back_populates="sogc_publications")  # noqa: F821
    changes: Mapped[list["SogcChange"]] = relationship(  # noqa: F821
        "SogcChange", back_populates="publication", cascade="all, delete-orphan"
    )
