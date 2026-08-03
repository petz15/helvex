"""CRUD for company_score — per-scope (org, optional user) materialized scores.

Scoring/multi-tenancy rework. Reads always resolve to exactly one scope per
request (org_id, user_id-or-NULL) — see scoring/config_resolution.py for how
that scope is chosen — so every query here filters on a single (org_id,
user_id) pair, never merges rows across scopes.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.company_score import CompanyScore


def get_score(db: Session, *, org_id: int, user_id: int | None, company_id: int) -> CompanyScore | None:
    return (
        db.query(CompanyScore)
        .filter(
            CompanyScore.org_id == org_id,
            CompanyScore.user_id == user_id,
            CompanyScore.company_id == company_id,
        )
        .first()
    )


def upsert_score(
    db: Session,
    *,
    org_id: int,
    user_id: int | None,
    company_id: int,
    flex_score: int | None,
    web_score: int | None,
    combined_score: float | None,
    config_version: str | None = None,
) -> CompanyScore:
    """Insert or update one company's score row for a scope. Does not commit."""
    row = (
        db.query(CompanyScore)
        .filter(
            CompanyScore.org_id == org_id,
            CompanyScore.user_id == user_id,
            CompanyScore.company_id == company_id,
        )
        .first()
    )
    if row is None:
        row = CompanyScore(org_id=org_id, user_id=user_id, company_id=company_id)
        db.add(row)
    row.flex_score = flex_score
    row.web_score = web_score
    row.combined_score = combined_score
    row.config_version = config_version
    db.flush()
    return row


def scope_exists(db: Session, *, org_id: int, user_id: int | None) -> bool:
    return (
        db.query(CompanyScore.id)
        .filter(CompanyScore.org_id == org_id, CompanyScore.user_id == user_id)
        .first()
        is not None
    )
