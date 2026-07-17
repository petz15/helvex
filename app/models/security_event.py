"""Security events — anomaly-detection flags for authenticated API access.

One row per flag raised by the anomaly detector (request bursts, script-like
access, oversized exports, …). When ``throttle_until`` is set, the flagged user
is auto-throttled to a much lower request rate until that time. Rows are durable
so an admin can review abuse history after the fact.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class SecurityEvent(Base):
    __tablename__ = "security_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    org_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # rate_burst | script_access | large_export | ...
    event_type: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="warning")
    detail: Mapped[dict | None] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=True
    )
    ip: Mapped[str | None] = mapped_column(String(45), nullable=True)  # IPv4 or IPv6
    # When set and in the future, the user is auto-throttled until this instant.
    throttle_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(tz=timezone.utc),
        index=True,
    )

    user: Mapped["User | None"] = relationship("User", lazy="joined", foreign_keys=[user_id])  # type: ignore[name-defined]  # noqa: F821
