"""Global cross-entity search endpoint."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, desc, or_
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models.user import User

router = APIRouter(tags=["search"])


class CompanySnippet(BaseModel):
    id: int
    uid: str
    name: str
    canton: str | None
    legal_form: str | None
    status: str | None


class PersonSnippet(BaseModel):
    id: int
    firstname: str | None
    lastname: str | None
    hometown_municipality: str | None
    active_company_count: int
    confidence_level: str


class AuditorSnippet(BaseModel):
    key: str
    name: str
    location: str | None
    client_count: int


class GlobalSearchResult(BaseModel):
    companies: list[CompanySnippet]
    persons: list[PersonSnippet]
    auditors: list[AuditorSnippet]


@router.get("/search/global", response_model=GlobalSearchResult)
def global_search(
    q: str = Query(..., min_length=2),
    limit: int = Query(6, ge=1, le=20),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    from app.models.company import Company
    from app.models.sogc_person_entity import SogcPersonEntity
    from app.models.sogc_auditor import SogcAuditor

    term = f"%{q}%"
    term_lower = f"%{q.lower()}%"

    companies = (
        db.query(Company)
        .filter(Company.name.ilike(term))
        .order_by(Company.name)
        .limit(limit)
        .all()
    )
    company_snippets = [
        CompanySnippet(
            id=c.id, uid=c.uid, name=c.name,
            canton=c.canton, legal_form=c.legal_form, status=c.status,
        )
        for c in companies
    ]

    persons = (
        db.query(SogcPersonEntity)
        .filter(
            SogcPersonEntity.merged_into_id.is_(None),
            or_(
                func.lower(SogcPersonEntity.lastname).like(term_lower),
                func.lower(SogcPersonEntity.firstname).like(term_lower),
            ),
        )
        .order_by(SogcPersonEntity.active_company_count.desc())
        .limit(limit)
        .all()
    )
    person_snippets = [
        PersonSnippet(
            id=p.id, firstname=p.firstname, lastname=p.lastname,
            hometown_municipality=p.hometown_municipality,
            active_company_count=p.active_company_count,
            confidence_level=p.confidence_level,
        )
        for p in persons
    ]

    auditor_rows = (
        db.query(
            SogcAuditor.auditor_uid,
            SogcAuditor.auditor_name,
            SogcAuditor.auditor_location,
            func.count(SogcAuditor.company_uid.distinct()).label("client_count"),
        )
        .filter(
            SogcAuditor.auditor_name_normalized.like(term_lower),
            or_(SogcAuditor.is_current == True, SogcAuditor.is_current.is_(None)),
        )
        .group_by(SogcAuditor.auditor_uid, SogcAuditor.auditor_name, SogcAuditor.auditor_location)
        .order_by(desc("client_count"))
        .limit(limit)
        .all()
    )
    auditor_snippets = [
        AuditorSnippet(
            key=row.auditor_uid or row.auditor_name or str(i),
            name=row.auditor_name or "—",
            location=row.auditor_location,
            client_count=row.client_count,
        )
        for i, row in enumerate(auditor_rows)
    ]

    return GlobalSearchResult(
        companies=company_snippets,
        persons=person_snippets,
        auditors=auditor_snippets,
    )
