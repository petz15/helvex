"""Saved dashboard filter views (per user, per org)."""
from datetime import datetime
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base

class UserView(Base):
    __tablename__ = "user_views"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    # Org scope: views are per-user per-org. NULL = legacy views shown in all org contexts.
    org_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    filters_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    user: Mapped["User"] = relationship("User")  # noqa: F821

    # ── Daily alert fields ────────────────────────────────────────────────────
    alert_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    alert_last_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    alert_last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
