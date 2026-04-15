"""User activity log — who did what, when.

Distinct from audit_log (field-level data changes on companies).
This table records user-initiated actions for observability, support,
and security audit purposes.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class ActivityLog(Base):
    """One row per meaningful user action.

    action      — slug from ACTIONS dict in app/services/activity.py
    resource_type — optional: "company", "view", "org", "note", "user"
    resource_id   — optional FK to the affected row (no DB-level FK, allows deletions)
    meta          — arbitrary extra context (e.g. search query, export filter count)
    ip            — client IP at time of action (optional, for security audit)
    """

    __tablename__ = "activity_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    org_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True, index=True
    )
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    resource_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    resource_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    meta: Mapped[dict | None] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=True
    )
    ip: Mapped[str | None] = mapped_column(String(45), nullable=True)  # IPv4 or IPv6
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(tz=timezone.utc),
        index=True,
    )

    user: Mapped["User | None"] = relationship("User", lazy="joined", foreign_keys=[user_id])  # type: ignore[name-defined]  # noqa: F821
