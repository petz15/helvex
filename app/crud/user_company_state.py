"""CRUD operations for user_company_state overlay (private per-user data)."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.models.user_company_state import UserCompanyState


def get_user_company_state(db: Session, *, user_id: int, company_id: int) -> UserCompanyState | None:
    return (
        db.query(UserCompanyState)
        .filter(UserCompanyState.user_id == user_id, UserCompanyState.company_id == company_id)
        .first()
    )


def get_or_create_user_company_state(
    db: Session, *, user_id: int, company_id: int, org_id: int
) -> UserCompanyState:
    row = get_user_company_state(db, user_id=user_id, company_id=company_id)
    if row is None:
        row = UserCompanyState(user_id=user_id, company_id=company_id, org_id=org_id)
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def update_user_ai_results(
    db: Session,
    *,
    user_id: int,
    company_id: int,
    org_id: int,
    ai_score: float | None,
    ai_category: str | None,
    ai_freeform: str | None,
    ai_scored_at: datetime,
) -> UserCompanyState:
    """Upsert AI classification results. Called by workers."""
    row = get_or_create_user_company_state(db, user_id=user_id, company_id=company_id, org_id=org_id)
    row.ai_score = ai_score
    row.ai_category = ai_category
    row.ai_freeform = ai_freeform
    row.ai_scored_at = ai_scored_at
    db.commit()
    db.refresh(row)
    return row


def update_personal_score_override(
    db: Session,
    *,
    user_id: int,
    company_id: int,
    org_id: int,
    personal_score_override: float | None,
) -> UserCompanyState:
    row = get_or_create_user_company_state(db, user_id=user_id, company_id=company_id, org_id=org_id)
    row.personal_score_override = personal_score_override
    db.commit()
    db.refresh(row)
    return row
