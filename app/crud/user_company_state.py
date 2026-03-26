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


def update_user_claude_results(
    db: Session,
    *,
    user_id: int,
    company_id: int,
    org_id: int,
    claude_score: float | None,
    claude_category: str | None,
    claude_freeform: str | None,
    claude_scored_at: datetime,
) -> UserCompanyState:
    """Upsert Claude classification results. Called by workers."""
    row = get_or_create_user_company_state(db, user_id=user_id, company_id=company_id, org_id=org_id)
    row.claude_score = claude_score
    row.claude_category = claude_category
    row.claude_freeform = claude_freeform
    row.claude_scored_at = claude_scored_at
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
