"""Payment transaction audit trail for all payment provider transactions."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

if TYPE_CHECKING:
    from app.models.organization import Organization

from app.database import Base


class PaymentTransaction(Base):
    """Full audit trail of payment transactions from Worldline Saferpay.

    Tracks every payment attempt, success, decline, or error for compliance and support.
    External IDs are unique to prevent duplicate processing.

    Status enum: pending -> authorized|declined|error
    Kind enum: subscription|topup
    """

    __tablename__ = "payment_transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)

    # Provider and external IDs
    provider: Mapped[str] = mapped_column(String(20), nullable=False)  # "worldline"
    external_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)  # Saferpay token
    order_reference: Mapped[str] = mapped_column(String(255), nullable=False)  # wl_sub_* or wl_topup_*

    # Transaction amount (always in CHF for consistency)
    amount_chf: Mapped[float] = mapped_column(Float, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="CHF")

    # VAT applied to this transaction
    vat_rate: Mapped[float | None] = mapped_column(Float, nullable=True)          # e.g. 0.081 for 8.1%
    vat_amount_chf: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)

    # Transaction type
    kind: Mapped[str] = mapped_column(String(20), nullable=False)  # "subscription" or "topup"
    status: Mapped[str] = mapped_column(String(20), nullable=False)  # "pending", "authorized", "captured", "declined", "error"

    # Payment method (extracted from provider response)
    payment_method: Mapped[str | None] = mapped_column(String(50), nullable=True)  # "card", "twint", "bank_transfer", etc.

    # Cardholder identity (for chargeback evidence)
    cardholder_name: Mapped[str | None] = mapped_column(String(255), nullable=True)  # From PaymentMeans.Card.HolderName
    billing_address: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON: {street, number, postal_code, city, country}

    # Provider transaction ID (for dispute resolution and provider lookups)
    provider_transaction_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # For subscriptions
    subscription_tier: Mapped[str | None] = mapped_column(String(20), nullable=True)
    subscription_billing_cycle: Mapped[str | None] = mapped_column(String(10), nullable=True)  # "monthly" or "yearly"
    upgrade_proration_credits: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # credits to grant on capture for plan upgrades

    # For topups
    credits_purchased: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    credits_bonus: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    credits_total_granted: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # Error tracking (if status = "error" or "declined")
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Refund tracking
    refunded_amount_chf: Mapped[float | None] = mapped_column(Float, nullable=True)
    refund_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Idempotency: prevent duplicate processing
    # If a webhook is received multiple times, we check this to ensure one-time processing
    webhook_received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    webhook_processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(tz=timezone.utc),
    )
    authorized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    refunded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    org: Mapped[Organization] = relationship("Organization", back_populates="payment_transactions", foreign_keys=[org_id])
