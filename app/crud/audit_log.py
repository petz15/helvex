from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog

_TRACKED_FIELDS = {
    "website_url", "review_status", "contact_status",
    "contact_name", "contact_email", "contact_phone", "tags",
}


def create_audit_entry(
    db: Session,
    *,
    company_id: int | None,
    user_id: int | None,
    field: str,
    old_value: str | None,
    new_value: str | None,
) -> AuditLog:
    entry = AuditLog(
        company_id=company_id,
        user_id=user_id,
        field=field,
        old_value=old_value,
        new_value=new_value,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


def record_company_changes(
    db: Session,
    *,
    company_id: int,
    user_id: int | None,
    old_values: dict,
    new_values: dict,
) -> int:
    """Diff old vs new values for tracked fields and create audit entries. Returns count written."""
    count = 0
    for field in _TRACKED_FIELDS:
        if field not in new_values:
            continue
        old = old_values.get(field)
        new = new_values[field]
        # Normalise None and empty string to "" for comparison
        old_s = str(old) if old is not None else ""
        new_s = str(new) if new is not None else ""
        if old_s != new_s:
            create_audit_entry(
                db,
                company_id=company_id,
                user_id=user_id,
                field=field,
                old_value=old_s or None,
                new_value=new_s or None,
            )
            count += 1
    return count


def list_audit_for_company(db: Session, company_id: int, limit: int = 50) -> list[AuditLog]:
    return (
        db.query(AuditLog)
        .filter(AuditLog.company_id == company_id)
        .order_by(AuditLog.changed_at.desc())
        .limit(limit)
        .all()
    )


def list_recent_audit(db: Session, limit: int = 100) -> list[AuditLog]:
    return (
        db.query(AuditLog)
        .order_by(AuditLog.changed_at.desc())
        .limit(limit)
        .all()
    )
