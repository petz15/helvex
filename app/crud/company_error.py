"""CRUD helpers for company_errors — pipeline failure log."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.company_error import CompanyError

logger = logging.getLogger(__name__)


def log_error(
    db: Session,
    *,
    company_id: int | None,
    source: str,
    error_type: str,
    message: str | None = None,
    detail: dict[str, Any] | None = None,
    job_run_id: int | None = None,
) -> CompanyError:
    """Persist a pipeline error.

    Deduplicates: if an active (unresolved, not ignored) error with the same
    company_id + error_source already exists, updates its message/detail instead
    of inserting a duplicate row.
    """
    detail_str = json.dumps(detail) if detail else None

    if company_id is not None:
        existing = db.scalars(
            select(CompanyError).where(
                CompanyError.company_id == company_id,
                CompanyError.error_source == source,
                CompanyError.resolved_at.is_(None),
                CompanyError.ignored.is_(False),
            ).limit(1)
        ).first()
        if existing:
            existing.error_type = error_type
            existing.message = message
            existing.detail_json = detail_str
            if job_run_id:
                existing.job_run_id = job_run_id
            db.flush()
            return existing

    err = CompanyError(
        company_id=company_id,
        error_source=source,
        error_type=error_type,
        message=message,
        detail_json=detail_str,
        job_run_id=job_run_id,
    )
    db.add(err)
    db.flush()
    return err


def get_errors(
    db: Session,
    *,
    page: int = 1,
    page_size: int = 50,
    source: str | None = None,
    show_resolved: bool = False,
) -> dict:
    """Return paginated error list, optionally filtered by source and resolved state."""
    from app.models.company import Company

    q = (
        select(
            CompanyError,
            Company.name.label("company_name"),
            Company.uid.label("company_uid"),
        )
        .outerjoin(Company, Company.id == CompanyError.company_id)
        .where(CompanyError.ignored.is_(False))
    )
    if not show_resolved:
        q = q.where(CompanyError.resolved_at.is_(None))
    if source:
        q = q.where(CompanyError.error_source == source)

    total = db.scalar(select(func.count()).select_from(q.subquery()))
    rows = db.execute(q.order_by(CompanyError.created_at.desc()).offset((page - 1) * page_size).limit(page_size)).all()

    items = []
    for row in rows:
        err = row.CompanyError
        items.append({
            "id": err.id,
            "company_id": err.company_id,
            "company_name": row.company_name,
            "company_uid": row.company_uid,
            "error_source": err.error_source,
            "error_type": err.error_type,
            "message": err.message,
            "detail_json": err.detail_json,
            "job_run_id": err.job_run_id,
            "created_at": err.created_at,
            "resolved_at": err.resolved_at,
            "ignored": err.ignored,
        })

    return {"items": items, "total": total or 0, "page": page, "page_size": page_size}


def resolve_error(db: Session, error_id: int, user_id: int) -> CompanyError | None:
    err = db.get(CompanyError, error_id)
    if err:
        err.resolved_at = datetime.now(timezone.utc)
        err.resolved_by = user_id
        db.flush()
    return err


def ignore_error(db: Session, error_id: int) -> CompanyError | None:
    err = db.get(CompanyError, error_id)
    if err:
        err.ignored = True
        db.flush()
    return err


def resolve_company_errors(db: Session, company_id: int, user_id: int) -> int:
    """Resolve all active errors for a company (called after a correction is saved)."""
    now = datetime.now(timezone.utc)
    errors = db.scalars(
        select(CompanyError).where(
            CompanyError.company_id == company_id,
            CompanyError.resolved_at.is_(None),
            CompanyError.ignored.is_(False),
        )
    ).all()
    for err in errors:
        err.resolved_at = now
        err.resolved_by = user_id
    db.flush()
    return len(errors)
