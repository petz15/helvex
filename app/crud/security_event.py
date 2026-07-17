"""CRUD for security_events (anomaly flags + auto-throttle state)."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.models.security_event import SecurityEvent


def record_security_event(
    db: Session,
    *,
    event_type: str,
    user_id: int | None = None,
    org_id: int | None = None,
    detail: dict | None = None,
    ip: str | None = None,
    throttle_until: datetime | None = None,
    severity: str = "warning",
) -> SecurityEvent:
    ev = SecurityEvent(
        event_type=event_type,
        user_id=user_id,
        org_id=org_id,
        detail=detail,
        ip=ip,
        throttle_until=throttle_until,
        severity=severity,
    )
    db.add(ev)
    db.commit()
    db.refresh(ev)
    return ev


def get_active_throttle_until(db: Session, user_id: int) -> datetime | None:
    """Return the furthest-future active throttle deadline for a user, or None."""
    now = datetime.now(tz=timezone.utc)
    return db.execute(
        select(SecurityEvent.throttle_until)
        .where(
            SecurityEvent.user_id == user_id,
            SecurityEvent.throttle_until.isnot(None),
            SecurityEvent.throttle_until > now,
        )
        .order_by(desc(SecurityEvent.throttle_until))
        .limit(1)
    ).scalar_one_or_none()


def list_recent_security_events(
    db: Session, *, limit: int = 100, user_id: int | None = None
) -> list[SecurityEvent]:
    q = select(SecurityEvent).order_by(desc(SecurityEvent.created_at)).limit(limit)
    if user_id is not None:
        q = q.where(SecurityEvent.user_id == user_id)
    return list(db.execute(q).scalars().all())
