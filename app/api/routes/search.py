"""Global cross-entity search endpoint."""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy import func, desc, or_
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models.user import User

router = APIRouter(tags=["search"])


# ---------------------------------------------------------------------------
# Semantic company search
# ---------------------------------------------------------------------------

class SemanticSearchResult(BaseModel):
    company_id: int
    name: str
    uid: str
    canton: str | None
    purpose: str | None
    similarity: float
    lang: str | None


class SemanticSearchOut(BaseModel):
    results: list[SemanticSearchResult]
    embedding_type: str
    query: str


@router.get("/search/semantic", response_model=SemanticSearchOut)
def semantic_search(
    request: Request,
    q: str = Query(..., min_length=2, description="Free-text query to match against company purpose"),
    embedding_type: str = Query("purpose_clean", description="'purpose_clean' (boilerplate-stripped) or 'purpose_full'"),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Semantic similarity search over company purpose embeddings.

    Proxies to the ML worker pod when ML_WORKER_INTERNAL_URL is set so the
    embedding model is never loaded in the normal app pod.
    """
    ml_url = os.getenv("ML_WORKER_INTERNAL_URL")
    if ml_url:
        import httpx
        try:
            r = httpx.get(
                f"{ml_url}/api/v1/search/semantic",
                params={"q": q, "embedding_type": embedding_type, "limit": limit},
                headers={"cookie": request.headers.get("cookie", "")},
                timeout=20.0,
            )
            r.raise_for_status()
            return r.json()
        except Exception:
            # ML worker unreachable — return empty rather than loading the model locally
            return SemanticSearchOut(results=[], embedding_type=embedding_type, query=q)

    if embedding_type not in ("purpose_clean", "purpose_full"):
        embedding_type = "purpose_clean"

    from app.services.ml.company_embedding_pipeline import search_companies_semantic
    hits = search_companies_semantic(db, q, embedding_type=embedding_type, limit=limit)
    results = [
        SemanticSearchResult(
            company_id=h["company_id"],
            name=h["name"],
            uid=h["uid"],
            canton=h["canton"],
            purpose=h["purpose"],
            similarity=h["similarity"],
            lang=h["lang"],
        )
        for h in hits
    ]
    return SemanticSearchOut(results=results, embedding_type=embedding_type, query=q)


class CompanySnippet(BaseModel):
    id: int
    uid: str
    name: str
    canton: str | None
    legal_form: str | None
    status: str | None
    matched_old_name: str | None = None


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

    import json as _json
    import re as _re

    # Normalize compact UID (CHE442723833) → CHE-442.723.833
    _uid_m = _re.match(r'^CHE(\d{9})$', q.strip().upper())
    uid_term = f"CHE-{_uid_m.group(1)[:3]}.{_uid_m.group(1)[3:6]}.{_uid_m.group(1)[6:]}" if _uid_m else q.strip()
    uid_is_query = bool(_re.match(r'^CHE[-\d.]{9,13}$', q.strip().upper()))

    company_filter = or_(
        Company.name.ilike(term),
        Company.old_names.ilike(term),
        *(
            [Company.uid.ilike(f"%{uid_term}%")]
            if uid_is_query else []
        ),
    )

    companies = (
        db.query(Company)
        .filter(company_filter)
        .order_by(
            # UID exact matches first, then current-name matches
            (Company.uid.ilike(uid_term)).desc() if uid_is_query else func.lower(Company.name).like(term_lower).desc(),
            func.lower(Company.name).like(term_lower).desc(),
            Company.name,
        )
        .limit(limit)
        .all()
    )

    def _find_old_name_match(raw_json: str | None, needle: str) -> str | None:
        if not raw_json:
            return None
        try:
            entries = _json.loads(raw_json)
            nl = needle.lower().strip("%")
            for entry in entries:
                n = entry.get("name", "")
                if n and nl in n.lower():
                    return n
        except Exception:
            pass
        return None

    company_snippets = [
        CompanySnippet(
            id=c.id, uid=c.uid, name=c.name,
            canton=c.canton, legal_form=c.legal_form, status=c.status,
            matched_old_name=(
                None if q.lower() in c.name.lower()
                else _find_old_name_match(c.old_names, q)
            ),
        )
        for c in companies
    ]

    q_parts = q.lower().split()
    if len(q_parts) >= 2:
        # "Hans Müller" — try first+last in both orders
        first_part = f"%{q_parts[0]}%"
        rest_part = f"%{' '.join(q_parts[1:])}%"
        person_filter = or_(
            func.lower(SogcPersonEntity.lastname).like(term_lower),
            func.lower(SogcPersonEntity.firstname).like(term_lower),
            # firstname matches first token, lastname matches rest
            (func.lower(SogcPersonEntity.firstname).like(first_part) &
             func.lower(SogcPersonEntity.lastname).like(rest_part)),
            # lastname matches first token, firstname matches rest
            (func.lower(SogcPersonEntity.lastname).like(first_part) &
             func.lower(SogcPersonEntity.firstname).like(rest_part)),
        )
    else:
        person_filter = or_(
            func.lower(SogcPersonEntity.lastname).like(term_lower),
            func.lower(SogcPersonEntity.firstname).like(term_lower),
        )

    persons = (
        db.query(SogcPersonEntity)
        .filter(
            SogcPersonEntity.merged_into_id.is_(None),
            person_filter,
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
            or_(SogcAuditor.is_current == True, SogcAuditor.is_current.is_(None)),  # noqa: E712
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
