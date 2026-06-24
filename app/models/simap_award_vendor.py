from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class SimapAwardVendor(Base):
    """A vendor that was awarded in a SIMAP tender.

    ``company_id`` is populated when the vendor's CHE UID matches a company in
    our database (Swiss companies only).  Foreign companies have ``company_id=NULL``.
    """

    __tablename__ = "simap_award_vendors"
    __table_args__ = (
        UniqueConstraint("award_id", "simap_vendor_id", name="uq_simap_award_vendor"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    award_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("simap_awards.id", ondelete="CASCADE"), nullable=False, index=True
    )
    company_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("companies.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # SIMAP internal vendor UUID
    simap_vendor_id: Mapped[str] = mapped_column(String(36), nullable=False)

    # Vendor details as stored in SIMAP
    vendor_name: Mapped[str] = mapped_column(String(512), nullable=False)
    vendor_uid: Mapped[str | None] = mapped_column(String(20), nullable=True)   # CHE-xxx.xxx.xxx
    vendor_country: Mapped[str | None] = mapped_column(String(2), nullable=True)
    vendor_city: Mapped[str | None] = mapped_column(String(100), nullable=True)
    vendor_postal_code: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Price (per-vendor; null when award uses a total price range)
    price: Mapped[float | None] = mapped_column(Numeric(15, 2), nullable=True)
    price_currency: Mapped[str | None] = mapped_column(String(5), nullable=True)
    rank: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    award: Mapped["SimapAward"] = relationship("SimapAward", back_populates="vendors")  # noqa: F821
    company: Mapped["Company"] = relationship("Company")  # noqa: F821
