from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(256), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(tz=timezone.utc),
    )

    # SaaS fields
    email: Mapped[str | None] = mapped_column(String(256), unique=True, nullable=True)
    tier: Mapped[str] = mapped_column(String(32), nullable=False, default="free")
    email_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_superadmin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Payment (vendor-neutral — currently Worldline)
    payment_customer_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    payment_subscription_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    subscription_status: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # Email verification
    email_verification_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Organisation (for team-seat tiers)
    org_id: Mapped[int | None] = mapped_column(ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True)
    # Role within the org: owner | admin | member | viewer
    org_role: Mapped[str] = mapped_column(String(32), nullable=False, default="member")

    org: Mapped["Organization | None"] = relationship("Organization", back_populates="users", foreign_keys=[org_id])
