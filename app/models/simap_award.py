from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class SimapAward(Base):
    """One SIMAP award publication (or direct award).

    The upsert key is ``simap_project_id`` — for lot-based projects each lot
    has its own publication UUID but shares the same project UUID.  We store
    one row per publication (``simap_publication_id``) so lot awards are
    separate rows, but the project/publication pair is unique.
    """

    __tablename__ = "simap_awards"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # SIMAP identifiers
    simap_project_id: Mapped[str] = mapped_column(String(36), unique=True, nullable=False, index=True)
    simap_publication_id: Mapped[str] = mapped_column(String(36), nullable=False)
    project_number: Mapped[str | None] = mapped_column(String(50), nullable=True)
    publication_number: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Dates
    publication_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    award_decision_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    # Publication classification
    pub_type: Mapped[str | None] = mapped_column(String(100), nullable=True)       # award / direct_award
    process_type: Mapped[str | None] = mapped_column(String(100), nullable=True)  # open / selective / direct / invitation
    project_type: Mapped[str | None] = mapped_column(String(100), nullable=True)  # tender / competition / study_contract
    project_subtype: Mapped[str | None] = mapped_column(String(100), nullable=True)  # e.g. overall_performance_competition
    creation_language: Mapped[str | None] = mapped_column(String(2), nullable=True)

    # Multilingual text (only creation_language slot is filled by SIMAP for most records)
    title_de: Mapped[str | None] = mapped_column(Text, nullable=True)
    title_fr: Mapped[str | None] = mapped_column(Text, nullable=True)
    title_it: Mapped[str | None] = mapped_column(Text, nullable=True)

    proc_office_name_de: Mapped[str | None] = mapped_column(Text, nullable=True)
    proc_office_name_fr: Mapped[str | None] = mapped_column(Text, nullable=True)
    proc_office_name_it: Mapped[str | None] = mapped_column(Text, nullable=True)

    order_description_de: Mapped[str | None] = mapped_column(Text, nullable=True)
    order_description_fr: Mapped[str | None] = mapped_column(Text, nullable=True)
    order_description_it: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Classification codes
    cpv_code: Mapped[str | None] = mapped_column(String(10), nullable=True)

    # Statistics
    number_of_submissions: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Price (total)  — for direct_award with price_range, per-vendor price is null
    total_price_selection: Mapped[str | None] = mapped_column(String(60), nullable=True)
    total_price_range_min: Mapped[float | None] = mapped_column(Numeric(15, 2), nullable=True)
    total_price_range_max: Mapped[float | None] = mapped_column(Numeric(15, 2), nullable=True)
    total_price_currency: Mapped[str | None] = mapped_column(String(5), nullable=True)

    # Lot info (filled only for lotsType="with" projects)
    lot_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lot_title_de: Mapped[str | None] = mapped_column(Text, nullable=True)
    lot_title_fr: Mapped[str | None] = mapped_column(Text, nullable=True)
    lot_title_it: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    vendors: Mapped[list["SimapAwardVendor"]] = relationship(
        "SimapAwardVendor", back_populates="award", cascade="all, delete-orphan"
    )

    def best_title(self) -> str:
        return self.title_de or self.title_fr or self.title_it or ""

    def best_proc_office(self) -> str:
        return self.proc_office_name_de or self.proc_office_name_fr or self.proc_office_name_it or ""
