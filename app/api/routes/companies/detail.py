"""Single company CRUD routes plus Google search and website selection."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app import crud
from app.auth import get_current_user
from app.services.rate_limit import check_rate_limit
from app.database import get_db
from app.models.organization import Organization
from app.models.simap_award import SimapAward
from app.models.simap_award_vendor import SimapAwardVendor
from app.models.user import User
from app.schemas.company import CompanyCreate, CompanyRead, CompanyUpdate, GoogleSearchResult
from app.schemas.note import NoteRead
from app.services import credits as credits_service
from app.services.activity import log_activity
from app.services.collection import enrich_company_website
from app.services.scoring import is_social_lead_domain
from app.services.tiers import has_feature

from app.api.routes.companies._shared import (
    _ORG_FIELDS,
    _apply_web_results_gate,
    _clear_noga_cache,
    _overlay,
)
from app.services.job_worker import enqueue_job

router = APIRouter()


@router.post("/", response_model=CompanyRead, status_code=status.HTTP_201_CREATED, summary="Create company")
def create_company(company_in: CompanyCreate, db: Session = Depends(get_db)):
    existing = crud.get_company_by_uid(db, company_in.uid)
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Company with this UID already exists")
    result = crud.create_company(db, company_in)
    _clear_noga_cache()
    return result


@router.get("/{company_id}", response_model=CompanyRead, summary="Get company by ID")
def get_company(
    company_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    db_company = crud.get_company(db, company_id)
    if not db_company:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    if current_user.org_id is not None:
        scoped_notes = [n for n in db_company.notes if n.org_id == current_user.org_id]
    else:
        scoped_notes = [n for n in db_company.notes if n.user_id == current_user.id]
    org_state = crud.get_org_company_state(db, org_id=current_user.org_id, company_id=company_id) if current_user.org_id else None
    log_activity(
        db, action="company_viewed",
        user_id=current_user.id, org_id=current_user.org_id,
        resource_type="company", resource_id=company_id,
        meta={"name": db_company.name},
    )
    db.commit()
    result = _overlay(db_company, org_state)
    result = result.model_copy(update={"notes": [NoteRead.model_validate(n) for n in scoped_notes]})
    org: Organization | None = db.get(Organization, current_user.org_id) if current_user.org_id else None
    return _apply_web_results_gate(result, org, current_user.is_superadmin)


@router.patch("/{company_id}", response_model=CompanyRead, summary="Update company")
def update_company(
    company_id: int,
    company_in: CompanyUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    db_company = crud.get_company(db, company_id)
    if not db_company:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")

    payload = company_in.model_dump(exclude_unset=True)
    workflow = {k: v for k, v in payload.items() if k in _ORG_FIELDS}
    catalog = {k: v for k, v in payload.items() if k not in _ORG_FIELDS}

    old_values = {f: getattr(db_company, f) for f in ("website_url", *_ORG_FIELDS)}

    if workflow and current_user.org_id:
        org_state = crud.get_or_create_org_company_state(db, org_id=current_user.org_id, company_id=company_id)
        for field, value in workflow.items():
            setattr(org_state, field, value)
        db.commit()
        db.refresh(org_state)
    elif workflow:
        catalog.update(workflow)

    if catalog:
        crud.update_company(db, db_company, CompanyUpdate(**catalog))
        _clear_noga_cache()

    crud.record_company_changes(
        db, company_id=company_id, user_id=current_user.id,
        old_values=old_values, new_values=payload,
    )
    log_activity(
        db, action="company_updated",
        user_id=current_user.id, org_id=current_user.org_id,
        resource_type="company", resource_id=company_id,
        meta={"fields": list(payload.keys())},
    )
    db.commit()
    org_state_final = crud.get_org_company_state(db, org_id=current_user.org_id, company_id=company_id) if current_user.org_id else None
    return _overlay(db_company, org_state_final)


@router.delete("/{company_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete company")
def delete_company(
    company_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    db_company = crud.get_company(db, company_id)
    if not db_company:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    log_activity(
        db, action="company_deleted",
        user_id=current_user.id, org_id=current_user.org_id,
        resource_type="company", resource_id=company_id,
        meta={"name": db_company.name},
    )
    crud.delete_company(db, db_company)


@router.get(
    "/{company_id}/google-search",
    response_model=list[GoogleSearchResult],
    summary="Search Google for a company's website",
)
def google_search_for_company(
    company_id: int,
    request: Request,
    num: int = Query(10, ge=1, le=10, description="Number of results"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Run Google enrichment for an existing company. Rate-limited to 30 searches per user per 10 minutes."""
    if not current_user.is_superadmin:
        key = f"user_{current_user.id}"
        check_rate_limit(key, "google_search", window=600, max_calls=30, detail="Too many Google search requests. Maximum 30 per 10 minutes.")
        if current_user.org_id:
            org = db.get(Organization, current_user.org_id)
            if not org or not has_feature(org, "web_search"):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Website search is not available on the free plan. Upgrade to Simple or higher.",
                )
            if not credits_service.check_and_deduct(
                db,
                current_user.org_id,
                "web_search",
                1,
                reference_id=f"google_search:company_{company_id}:user_{current_user.id}",
            ):
                raise HTTPException(
                    status_code=status.HTTP_402_PAYMENT_REQUIRED,
                    detail=f"Insufficient credits. A web search costs {credits_service.CREDIT_COSTS['web_search']:,} credits.",
                )
    db_company = crud.get_company(db, company_id)
    if not db_company:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")

    log_activity(
        db,
        action="web_search_run",
        user_id=current_user.id,
        org_id=current_user.org_id,
        resource_type="company",
        resource_id=company_id,
        meta={"company_id": company_id, "name": db_company.name},
    )

    try:
        enrich_company_website(db, db_company, num=num)
        db.refresh(db_company)

        raw = db_company.google_search_results_raw or "[]"
        stored = json.loads(raw)
        if not isinstance(stored, list):
            stored = []

        results = [
            GoogleSearchResult(
                title=str(item.get("title") or ""),
                link=str(item.get("link") or ""),
                snippet=(str(item.get("snippet")) if item.get("snippet") is not None else None),
            )
            for item in stored
            if isinstance(item, dict) and (item.get("link") or "")
        ]
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    return results


@router.post(
    "/{company_id}/noga-v2-explain",
    summary="Enqueue NOGA explain job for a company (superadmin only)",
    status_code=status.HTTP_202_ACCEPTED,
)
def noga_v2_explain_enqueue(
    company_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not current_user.is_superadmin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Superadmin only")
    db_company = crud.get_company(db, company_id)
    if not db_company:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    job = enqueue_job(
        request.app,
        job_type="noga_v2_explain",
        label=f"NOGA explain: {db_company.name}",
        params={"company_id": company_id},
        db=db,
        org_id=None,
        user_id=current_user.id,
    )
    return {"job_id": job.id, "status": job.status}


@router.get(
    "/{company_id}/noga-v2-explain/{job_id}",
    summary="Get NOGA explain job result (superadmin only)",
)
def noga_v2_explain_result(
    company_id: int,
    job_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    import json as _json
    if not current_user.is_superadmin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Superadmin only")
    job = crud.get_job(db, job_id)
    if not job or job.job_type != "noga_v2_explain":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    result = _json.loads(job.stats_json) if job.stats_json else None
    return {
        "job_id": job_id,
        "status": job.status,
        "result": result if job.status == "completed" else None,
        "error": job.error if job.status == "failed" else None,
    }


class CompanyWebsiteSelect(BaseModel):
    link: str


@router.patch(
    "/{company_id}/website",
    response_model=CompanyRead,
    summary="Select the company's website from stored Google results",
)
def select_company_website(
    company_id: int,
    body: CompanyWebsiteSelect,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    db_company = crud.get_company(db, company_id)
    if not db_company:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")

    if not db_company.google_search_results_raw:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="No stored Google results for this company")

    wanted = (body.link or "").strip()
    if not wanted:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="link is required")

    try:
        stored = json.loads(db_company.google_search_results_raw)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc

    match: dict | None = next((r for r in stored if (r.get("link") or "").strip() == wanted), None)
    if match is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Selected link not found in stored results")

    score = match.get("score")
    web_score = int(score) if isinstance(score, (int, float)) else None
    social_media_only = is_social_lead_domain(wanted)

    old_values = {f: getattr(db_company, f) for f in (
        "website_url", "review_status", "contact_status",
        "contact_name", "contact_email", "contact_phone", "tags",
    )}

    updated = crud.update_company(
        db,
        db_company,
        CompanyUpdate(
            website_url=wanted,
            web_score=web_score,
            social_media_only=social_media_only,
        ),
    )
    _clear_noga_cache()
    new_values = {
        "website_url": wanted,
        "web_score": web_score,
        "social_media_only": social_media_only,
    }
    crud.record_company_changes(
        db,
        company_id=company_id,
        user_id=current_user.id,
        old_values=old_values,
        new_values=new_values,
    )
    log_activity(
        db, action="company_website_set",
        user_id=current_user.id, org_id=current_user.org_id,
        resource_type="company", resource_id=company_id,
        meta={"url": wanted},
    )
    db.commit()
    return updated


@router.get("/{company_id}/simap-awards", summary="Get SIMAP contract awards for a company")
def get_company_simap_awards(
    company_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[dict[str, Any]]:
    """Return all SIMAP contract award records where this company was a winning vendor."""
    db_company = crud.get_company(db, company_id)
    if not db_company:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")

    rows = db.execute(
        select(SimapAwardVendor)
        .where(SimapAwardVendor.company_id == company_id)
        .options(joinedload(SimapAwardVendor.award))
        .order_by(SimapAwardVendor.id.desc())
    ).scalars().all()

    result = []
    for v in rows:
        a = v.award
        result.append({
            "id": a.id,
            "publication_date": a.publication_date.isoformat() if a.publication_date else None,
            "award_decision_date": a.award_decision_date.isoformat() if a.award_decision_date else None,
            "title_de": a.title_de,
            "title_fr": a.title_fr,
            "title_it": a.title_it,
            "best_title": a.best_title(),
            "proc_office_name_de": a.proc_office_name_de,
            "proc_office_name_fr": a.proc_office_name_fr,
            "proc_office_name_it": a.proc_office_name_it,
            "best_proc_office": a.best_proc_office(),
            "pub_type": a.pub_type,
            "process_type": a.process_type,
            "project_type": a.project_type,
            "project_subtype": a.project_subtype,
            "cpv_code": a.cpv_code,
            "order_description_de": a.order_description_de,
            "order_description_fr": a.order_description_fr,
            "order_description_it": a.order_description_it,
            "number_of_submissions": a.number_of_submissions,
            "lot_number": a.lot_number,
            "lot_title_de": a.lot_title_de,
            "lot_title_fr": a.lot_title_fr,
            "lot_title_it": a.lot_title_it,
            "project_number": a.project_number,
            "publication_number": a.publication_number,
            # Vendor-specific fields for this company
            "price": float(v.price) if v.price is not None else None,
            "price_currency": v.price_currency,
            "rank": v.rank,
            # Total price range (when individual price is not set)
            "total_price_selection": a.total_price_selection,
            "total_price_range_min": float(a.total_price_range_min) if a.total_price_range_min is not None else None,
            "total_price_range_max": float(a.total_price_range_max) if a.total_price_range_max is not None else None,
            "total_price_currency": a.total_price_currency,
        })

    return result
