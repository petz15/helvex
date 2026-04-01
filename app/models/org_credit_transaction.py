from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

if TYPE_CHECKING:
    from app.models.organization import Organization

from app.database import Base


class OrgCreditTransaction(Base):
    """Full audit ledger for org credit changes.

    amount: positive = grant/topup, negative = deduction
    type: grant | topup | deduction | refund | expire
    action_type: batch_llm | immediate_llm | web_search | flex_rescore |
                 recluster | bulk_export_basic | bulk_export_detail
    expires_at: set for grant-type credits (1-year rollover); null for topups
    """

    __tablename__ = "org_credit_transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    action_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    reference_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    credits_before: Mapped[int] = mapped_column(BigInteger, nullable=False)
    credits_after: Mapped[int] = mapped_column(BigInteger, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(tz=timezone.utc),
    )

    org: Mapped[Organization] = relationship("Organization", back_populates="credit_transactions")
