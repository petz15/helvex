from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, JSON, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

if TYPE_CHECKING:
    from app.models.org_credit_transaction import OrgCreditTransaction
    from app.models.org_member import OrgMember
    from app.models.user import User

from app.database import Base


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    slug: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)

    # Tier: free | simple | explorer | researcher | strategist | custom
    tier: Mapped[str] = mapped_column(String(32), nullable=False, default="free")

    # Payment
    payment_customer_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # --- Billing / subscription ---
    subscription_billing_cycle: Mapped[str] = mapped_column(String(10), nullable=False, default="monthly")
    subscription_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # --- Credit ledger ---
    # Balance stored as integer credits (1 credit = 0.0001 CHF)
    credits_balance: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    # --- Tier entitlements ---
    # Simple tier: one free flex-rescore per billing month
    monthly_rescore_used: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    monthly_rescore_reset_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # --- Web results privacy ---
    # Fractional months of web-result privacy (0 = public, 0.25 = 1 week, 1 = 1 month, etc.)
    web_results_private_months: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    # --- Verified business ---
    verified_business: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Email domain restriction when verified (e.g. "acme.com"); null = no restriction
    verified_domain: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Linked Zefix company UID for auto-verification
    zefix_uid: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # --- Custom tier feature bundle (null unless tier = 'custom') ---
    # Stores the selected modular features and their quantities, e.g.:
    # {"web_results_months": 2, "export_100k": true, "discount_steps": 3,
    #  "priority_level": 2, "immediate_llm": true, "byo_llm_keys": false,
    #  "flex_auto_score": false, "llm_auto_score": false}
    custom_features: Mapped[dict | None] = mapped_column(JSONB().with_variant(JSON(), "sqlite"), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(tz=timezone.utc),
    )

    # Legacy relationship (kept until Session 3 migrates to org_members)
    users: Mapped[list[User]] = relationship("User", back_populates="org", foreign_keys="User.org_id")

    members: Mapped[list[OrgMember]] = relationship("OrgMember", back_populates="org", cascade="all, delete-orphan")
    credit_transactions: Mapped[list[OrgCreditTransaction]] = relationship(
        "OrgCreditTransaction", back_populates="org", cascade="all, delete-orphan"
    )
