"""List and CSV export routes."""

from __future__ import annotations

import math

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app import crud
from app.auth import get_current_user
from app.services.rate_limit import check_rate_limit
from app.database import get_db
from app.models.organization import Organization
from app.models.user import User
from app.schemas.company import CompanyPage
from app.services import credits as credits_service
from app.services.activity import log_activity
from app.services.tiers import get_export_limit

from app.api.routes.companies._shared import _apply_web_results_gate, _bulk_org_states, _overlay

router = APIRouter()


_LIST_RATE_WINDOW = 300   # 5 minutes
_LIST_RATE_MAX = 200      # per user


@router.get("/", response_model=CompanyPage, summary="List companies (paginated, filterable)")
def list_companies(
    page: int = Query(1, ge=1, le=500),
    page_size: int = Query(50, ge=1, le=100),
    sort: str = Query("-updated", description="Sort key, e.g. -combined_score, name, -updated"),
    q: str | None = Query(None, description="Filter by name (case-insensitive)", max_length=200),
    uid: str | None = Query(None, description="Filter by UID (partial match)"),
    canton: str | None = Query(None),
    review_status: str | None = Query(None, description="Use _none for unset"),
    contact_status: str | None = Query(None, description="Use _none for unset"),
    google_searched: str | None = Query(None, description="yes | no | no_result"),
    min_web_score: int | None = Query(None, ge=0, le=100),
    max_web_score: int | None = Query(None, ge=0, le=100),
    min_flex_score: int | None = Query(None, ge=0, le=100),
    max_flex_score: int | None = Query(None, ge=0, le=100),
    min_ai_score: int | None = Query(None, ge=0, le=100),
    max_ai_score: int | None = Query(None, ge=0, le=100),
    min_combined_score: int | None = Query(None, ge=0, le=100),
    max_combined_score: int | None = Query(None, ge=0, le=100),
    ai_category: str | None = Query(None, description="Use _none for unset"),
    tags: str | None = Query(None),
    tfidf_cluster: str | None = Query(None, description="_none | _any | keyword"),
    purpose_keywords: str | None = Query(None),
    noga_code: str | None = Query(None, description="_none | _any | code/substring"),
    noga_label: str | None = Query(None),
    noga_level: str | None = Query(None),
    exclude_tags: str | None = Query(None, description="Comma-separated tags to exclude"),
    exclude_review_status: str | None = Query(None),
    exclude_canton: str | None = Query(None),
    exclude_contact_status: str | None = Query(None),
    exclude_tfidf_cluster: str | None = Query(None, description="Comma-separated tfidf_cluster terms to exclude"),
    exclude_purpose_keywords: str | None = Query(None, description="Comma-separated purpose keywords to exclude"),
    exclude_ai_category: str | None = Query(None, description="Exclude companies with this exact ai_category"),
    exclude_noga_code: str | None = Query(None, description="Comma-separated NOGA codes/fragments to exclude"),
    exclude_noga_label: str | None = Query(None, description="Comma-separated NOGA label terms to exclude"),
    exclude_noga_level: str | None = Query(None, description="Exclude one NOGA level"),
    status: str | None = Query(None, description="Filter by Zefix company status, e.g. ACTIVE"),
    has_website: bool | None = Query(None, description="true = has website, false = no website"),
    legal_form: str | None = Query(None, description="Filter by exact legal form string"),
    registered_after: str | None = Query(None, description="First SOGC date >= (YYYY-MM-DD)"),
    registered_before: str | None = Query(None, description="First SOGC date <= (YYYY-MM-DD)"),
    sogc_after: str | None = Query(None, description="Most recent SOGC date >= (YYYY-MM-DD)"),
    sogc_before: str | None = Query(None, description="Most recent SOGC date <= (YYYY-MM-DD)"),
    shab_type: str | None = Query(None, description="SHAB entry type: 'new' (HR01), 'mutation' (HR02), 'deleted' (HR03)"),
    business_model: str | None = Query(None, description="Filter by business model: b2b, b2c, b2g, mixed, or _none"),
    purpose_language: str | None = Query(None, description="Filter by detected purpose language: de, fr, it, en"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CompanyPage:
    if not current_user.is_superadmin:
        check_rate_limit(
            f"user_{current_user.id}",
            "list_companies",
            window=_LIST_RATE_WINDOW,
            max_calls=_LIST_RATE_MAX,
            detail=f"Too many requests. Maximum {_LIST_RATE_MAX} list requests per {_LIST_RATE_WINDOW // 60} minutes.",
        )

    filter_kwargs = dict(
        name_filter=q,
        uid_filter=uid,
        canton=canton,
        review_status=review_status,
        contact_status=contact_status,
        google_searched=google_searched,
        min_web_score=min_web_score,
        max_web_score=max_web_score,
        min_flex_score=min_flex_score,
        max_flex_score=max_flex_score,
        min_ai_score=min_ai_score,
        max_ai_score=max_ai_score,
        min_combined_score=min_combined_score,
        max_combined_score=max_combined_score,
        ai_category=ai_category,
        tags=tags,
        tfidf_cluster=tfidf_cluster,
        purpose_keywords=purpose_keywords,
        noga_code=noga_code,
        noga_label=noga_label,
        noga_level=noga_level,
        exclude_tags=exclude_tags,
        exclude_review_status=exclude_review_status,
        exclude_canton=exclude_canton,
        exclude_contact_status=exclude_contact_status,
        exclude_tfidf_cluster=exclude_tfidf_cluster,
        exclude_purpose_keywords=exclude_purpose_keywords,
        exclude_ai_category=exclude_ai_category,
        exclude_noga_code=exclude_noga_code,
        exclude_noga_label=exclude_noga_label,
        exclude_noga_level=exclude_noga_level,
        zefix_status=status,
        has_website=has_website,
        legal_form=legal_form,
        registered_after=registered_after,
        registered_before=registered_before,
        sogc_after=sogc_after,
        sogc_before=sogc_before,
        shab_type=shab_type,
        business_model=business_model,
        purpose_language=purpose_language,
    )
    total = crud.count_companies(db, **filter_kwargs)
    items = crud.list_companies(db, page=page, page_size=page_size, sort=sort, **filter_kwargs)
    org: Organization | None = db.get(Organization, current_user.org_id) if current_user.org_id else None

    if current_user.org_id:
        ids = [c.id for c in items]
        org_states = _bulk_org_states(db, ids, current_user.org_id)
        items = [_apply_web_results_gate(_overlay(c, org_states.get(c.id)), org, current_user.is_superadmin) for c in items]
    else:
        items = [_apply_web_results_gate(_overlay(c, None), org, current_user.is_superadmin) for c in items]
    return CompanyPage(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        pages=math.ceil(total / page_size) if total else 1,
    )


@router.get("/export.csv", summary="Export companies as CSV (enqueues async job)")
def export_companies_csv(
    request: Request,
    sort: str = Query("-updated"),
    q: str | None = Query(None),
    uid: str | None = Query(None),
    canton: str | None = Query(None),
    review_status: str | None = Query(None),
    contact_status: str | None = Query(None),
    google_searched: str | None = Query(None),
    min_web_score: int | None = Query(None),
    min_flex_score: int | None = Query(None),
    min_ai_score: int | None = Query(None),
    tags: str | None = Query(None),
    tfidf_cluster: str | None = Query(None),
    purpose_keywords: str | None = Query(None),
    noga_code: str | None = Query(None),
    noga_label: str | None = Query(None),
    noga_level: str | None = Query(None),
    exclude_tags: str | None = Query(None),
    exclude_review_status: str | None = Query(None),
    exclude_canton: str | None = Query(None),
    exclude_contact_status: str | None = Query(None),
    exclude_noga_code: str | None = Query(None),
    exclude_noga_label: str | None = Query(None),
    exclude_noga_level: str | None = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Enqueue a CSV export job with the given filters."""
    from app.api.routes.jobs import _enqueue_or_http_error
    from app.services.s3_client import is_configured

    if not is_configured():
        raise HTTPException(status_code=503, detail="S3 export storage is not configured on this server")

    if not current_user.is_superadmin:
        key = f"user_{current_user.id}"
        check_rate_limit(key, "job_rl:csv_export", window=600, max_calls=5, detail="Too many export requests. Maximum 5 per 10 minutes.")

    if current_user.is_superadmin:
        row_limit = None
    elif current_user.org_id:
        org = db.get(Organization, current_user.org_id)
        row_limit = get_export_limit(org) if org else 100
    else:
        row_limit = 100

    if current_user.org_id and not current_user.is_superadmin:
        cap = row_limit or 0
        units = max(1, -(-cap // 10_000))
        if not credits_service.check_and_deduct(
            db,
            current_user.org_id,
            "bulk_export_basic",
            units,
            reference_id=f"csv_export_user_{current_user.id}",
        ):
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail=(
                    f"Insufficient credits. A {cap:,}-row export costs "
                    f"{credits_service.compute_cost('bulk_export_basic', units):,} credits."
                ),
            )

    crud.cancel_active_csv_exports(db, user_id=current_user.id)

    params = {
        "sort": sort,
        "q": q,
        "uid": uid,
        "canton": canton,
        "review_status": review_status,
        "contact_status": contact_status,
        "google_searched": google_searched,
        "min_web_score": min_web_score,
        "min_flex_score": min_flex_score,
        "min_ai_score": min_ai_score,
        "tags": tags,
        "tfidf_cluster": tfidf_cluster,
        "purpose_keywords": purpose_keywords,
        "noga_code": noga_code,
        "noga_label": noga_label,
        "noga_level": noga_level,
        "exclude_tags": exclude_tags,
        "exclude_review_status": exclude_review_status,
        "exclude_canton": exclude_canton,
        "exclude_contact_status": exclude_contact_status,
        "exclude_noga_code": exclude_noga_code,
        "exclude_noga_label": exclude_noga_label,
        "exclude_noga_level": exclude_noga_level,
    }

    from app.schemas.job import JobOut
    job = _enqueue_or_http_error(
        request,
        job_type="csv_export",
        label="CSV export",
        params=params,
        db=db,
        org_id=current_user.org_id,
        user_id=current_user.id,
    )
    log_activity(
        db, action="company_exported",
        user_id=current_user.id, org_id=current_user.org_id,
        meta={"job_id": job.id, "filters": {k: v for k, v in params.items() if v is not None}},
    )
    db.commit()
    return JobOut.from_orm_obj(job)
