"""Routes for company management and Zefix / Google Search integration."""

import json
import math
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import crud
from app.api import google_search_client, zefix_client
from app.auth import check_public_rate_limit, get_client_ip, get_current_user
from app.database import get_db
from app.models.company import Company
from app.models.org_company_state import OrgCompanyState
from app.models.organization import Organization
from app.models.user import User
from app.schemas.note import NoteRead
from app.schemas.company import (
    CompanyCreate,
    CompanyPage,
    CompanyRead,
    CompanyUpdate,
    GoogleSearchResult,
    ZefixSearchResult,
)
from app.services import credits as credits_service
from app.services.activity import log_activity
from app.services.collection import enrich_company_website
from app.services.scoring import is_social_lead_domain
from app.services.tiers import get_export_limit, get_web_results_privacy_months, normalize_tier, TIER_RANK

router = APIRouter(prefix="/companies", tags=["companies"])

# Fields whose values are org-specific and live in OrgCompanyState, not Company.
_ORG_FIELDS = frozenset({
    "review_status", "contact_status",
    "contact_name", "contact_email", "contact_phone",
    "tags",
})


def _overlay(
    company: Company,
    org_state: OrgCompanyState | None,
    w_ai: float = 0.70,
    w_web: float = 0.20,
    w_flex: float = 0.10,
) -> CompanyRead:
    """Build CompanyRead, overlaying org-specific workflow fields from OrgCompanyState."""
    base = CompanyRead.model_validate(company)
    overrides: dict = {}
    if org_state is not None:
        overrides.update({f: getattr(org_state, f) for f in _ORG_FIELDS if getattr(org_state, f) is not None})
    # Apply custom combined_score weights if non-default
    if (w_ai, w_web, w_flex) != (0.70, 0.20, 0.10):
        overrides["combined_score"] = company._combined_score(w_ai, w_web, w_flex)
    return base.model_copy(update=overrides) if overrides else base


def _bulk_org_states(db: Session, company_ids: list[int], org_id: int | None) -> dict[int, OrgCompanyState]:
    """Return a {company_id: OrgCompanyState} map for the given org."""
    if not org_id or not company_ids:
        return {}
    rows = db.query(OrgCompanyState).filter(
        OrgCompanyState.org_id == org_id,
        OrgCompanyState.company_id.in_(company_ids),
    ).all()
    return {r.company_id: r for r in rows}


_WEB_RESULT_FIELDS = ("website_url", "web_score", "google_search_results_raw")


def _apply_web_results_gate(company: CompanyRead, org: Organization | None, is_superadmin: bool) -> CompanyRead:
    """Mask web-search result fields based on the org's tier and the privacy window.

    - Superadmin: always visible.
    - Free tier: always masked.
    - Simple tier (rank 1): visible only if website_checked_at is older than the
      privacy window (~1 week).  Fresh results stay hidden.
    - Explorer+ (rank >= 2): always visible — higher tiers see results immediately.
    """
    if is_superadmin:
        return company
    if org is None:
        return company.model_copy(update={f: None for f in _WEB_RESULT_FIELDS})

    tier = normalize_tier(org.tier)
    rank = TIER_RANK.get(tier, 0)

    if rank == 0:
        # Free tier: no web results
        return company.model_copy(update={f: None for f in _WEB_RESULT_FIELDS})

    if rank >= 2:
        # Explorer and above: always visible
        return company

    # Simple tier: apply the privacy window
    privacy_months = get_web_results_privacy_months(org)
    if privacy_months > 0 and company.website_checked_at is not None:
        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=privacy_months * 30)
        checked_at = company.website_checked_at
        if checked_at.tzinfo is None:
            checked_at = checked_at.replace(tzinfo=timezone.utc)
        if checked_at > cutoff:
            # Results are still within the privacy window — mask them
            return company.model_copy(update={f: None for f in _WEB_RESULT_FIELDS})

    return company


# ---------------------------------------------------------------------------
# Zefix search (no DB persistence)
# ---------------------------------------------------------------------------


@router.get("/zefix/search", response_model=list[ZefixSearchResult], summary="Search Zefix API")
def zefix_search(
    name: str = Query(..., description="Company name to search for"),
    max_results: int = Query(20, ge=1, le=100, description="Maximum number of results"),
    active_only: bool = Query(False, description="Return only active companies"),
    _: User = Depends(get_current_user),
):
    """Query the Zefix REST API for Swiss companies matching *name*."""
    try:
        return zefix_client.search_companies(name, max_results=max_results, active_only=active_only)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.get("/zefix/{uid}", response_model=dict, summary="Get full Zefix company details")
def zefix_get_company(uid: str, _: User = Depends(get_current_user)):
    """Fetch the full company record from the Zefix API by UID."""
    try:
        return zefix_client.get_company(uid)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Import from Zefix into DB
# ---------------------------------------------------------------------------


@router.post(
    "/zefix/import/{uid}",
    response_model=CompanyRead,
    status_code=status.HTTP_201_CREATED,
    summary="Import a company from Zefix into the database",
)
def import_from_zefix(uid: str, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    """Fetch a company from the Zefix API and store it in the local database.

    If the company (identified by UID) already exists, it is updated.
    """
    try:
        raw = zefix_client.get_company(uid)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    # Parse common fields from the raw response
    name_raw = raw.get("name", "")
    if isinstance(name_raw, dict):
        name = name_raw.get("de") or name_raw.get("fr") or name_raw.get("it") or next(iter(name_raw.values()), "")
    else:
        name = str(name_raw)

    legal_form_raw = raw.get("legalForm", {})
    if isinstance(legal_form_raw, dict):
        legal_form = legal_form_raw.get("de") or legal_form_raw.get("shortName") or None
    else:
        legal_form = str(legal_form_raw) if legal_form_raw else None

    address_parts = raw.get("address", {}) or {}
    address_str: str | None = None
    if isinstance(address_parts, dict):
        parts = [
            address_parts.get("street"),
            address_parts.get("houseNumber"),
            address_parts.get("swissZipCode"),
            address_parts.get("city"),
        ]
        address_str = " ".join(str(p) for p in parts if p) or None

    uid_normalised = zefix_client._normalise_uid(str(raw.get("uid", uid)))

    # Extract purpose from multilingual dict if needed
    purpose_raw = raw.get("purpose") or raw.get("purposes") or None
    if isinstance(purpose_raw, list):
        purpose = " ".join(str(p) for p in purpose_raw if p) or None
    elif isinstance(purpose_raw, dict):
        purpose = (
            purpose_raw.get("de") or purpose_raw.get("fr")
            or purpose_raw.get("it") or purpose_raw.get("en")
            or next(iter(purpose_raw.values()), None) or None
        )
    else:
        purpose = str(purpose_raw) if purpose_raw else None

    company_data = CompanyCreate(
        uid=uid_normalised,
        name=name,
        legal_form=legal_form,
        status=str(raw.get("status", "")) or None,
        municipality=raw.get("municipality") or None,
        canton=raw.get("canton") or None,
        purpose=purpose,
        address=address_str,
        zefix_raw=json.dumps(raw),
    )

    existing = crud.get_company_by_uid(db, uid_normalised)
    if existing:
        return crud.update_company(db, existing, CompanyUpdate(**company_data.model_dump(exclude={"uid"})))
    return crud.create_company(db, company_data)


# ---------------------------------------------------------------------------
# Google Search integration
# ---------------------------------------------------------------------------


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
    """Run Google enrichment for an existing company only (no Zefix refresh).

    Persists scored results to ``google_search_results_raw`` and updates the selected
    ``website_url`` / ``web_score`` when candidates are found.

    Rate-limited to 30 Google searches per user per 10 minutes to protect the
    daily Google CSE quota.
    """
    if not current_user.is_superadmin:
        key = f"user_{current_user.id}"
        if not check_public_rate_limit(key, "google_search", window=600, max_requests=30):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many Google search requests. Maximum 30 per 10 minutes.",
            )
        if current_user.org_id:
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
    except Exception as exc:  # noqa: BLE001
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


# ---------------------------------------------------------------------------
# CRUD for companies in the DB
# ---------------------------------------------------------------------------


@router.get("/stats", response_model=dict, summary="Company stats (totals, review/proposal counts)")
def get_stats(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return crud.get_company_stats(db)


@router.get("/cantons", response_model=list[str], summary="List distinct cantons")
def list_cantons(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    rows = db.query(Company.canton).filter(Company.canton.isnot(None)).distinct().order_by(Company.canton).all()
    return [r.canton for r in rows]


@router.get("/taxonomy", response_model=dict, summary="Taxonomy stats (clusters, keywords, tags, categories)")
def get_taxonomy(
    org_id: int | None = Query(None, description="Scope stats to companies with an org state for this org"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    effective_org_id = org_id or current_user.org_id
    return crud.get_taxonomy_stats(db, org_id=effective_org_id)


@router.get("/category-stats", response_model=dict, summary="Score landscape stats for a specific category value")
def get_category_stats(
    type: str = Query(..., description="Category type: ai_category | tfidf_cluster | keyword | noga_code"),
    value: str = Query(..., description="Category value to look up"),
    org_id: int | None = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    effective_org_id = org_id or current_user.org_id
    return crud.get_category_stats(db, category_type=type, value=value, org_id=effective_org_id)


@router.get("/noga-hierarchy", response_model=list, summary="NOGA codes as a collapsible hierarchy with aggregated counts")
def get_noga_hierarchy(
    org_id: int | None = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    effective_org_id = org_id or current_user.org_id
    return crud.get_noga_hierarchy(db, org_id=effective_org_id)


@router.get("/keywords/search", response_model=list, summary="Autocomplete search across all purpose keywords")
def search_keywords(
    q: str = Query(..., min_length=1),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    results = crud.search_keywords(db, q=q, limit=limit)
    return [{"keyword": kw, "count": cnt} for kw, cnt in results]


@router.get("/clusters/search", response_model=list, summary="Autocomplete search across all cluster labels")
def search_clusters(
    q: str = Query(..., min_length=1),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    results = crud.search_clusters(db, q=q, limit=limit)
    return [{"cluster": c, "count": cnt} for c, cnt in results]

_DEMO_UID = "CHE-435.551.225" # Post CH AG


@router.get("/demo", response_model=CompanyRead, summary="Public demo company (no auth required)")
def get_demo_company(db: Session = Depends(get_db)):
    """Return the Post CH AG company record for unauthenticated landing-page previews."""
    company = crud.get_company_by_uid(db, _DEMO_UID)
    if not company:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Demo company not found")
    demo = _overlay(company, None)
    return demo.model_copy(update={
        "review_status": None,
        "contact_status": None,
        "contact_name": None,
        "contact_email": None,
        "contact_phone": None,
        "tags": None,
        "notes": [],
    })


@router.get("", response_model=CompanyPage, summary="List companies (paginated, filterable)")
def list_companies(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    sort: str = Query("-updated", description="Sort key, e.g. -combined_score, name, -updated"),
    q: str | None = Query(None, description="Filter by name (case-insensitive)"),
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
    registered_after: str | None = Query(None, description="First SOGC date >= (YYYY-MM-DD) — filters by company's earliest SOGC appearance"),
    registered_before: str | None = Query(None, description="First SOGC date <= (YYYY-MM-DD) — filters by company's earliest SOGC appearance"),
    sogc_after: str | None = Query(None, description="Most recent SOGC date >= (YYYY-MM-DD)"),
    sogc_before: str | None = Query(None, description="Most recent SOGC date <= (YYYY-MM-DD)"),
    shab_type: str | None = Query(None, description="SHAB entry type: 'new' (HR01), 'mutation' (HR02), 'deleted' (HR03)"),
    business_model: str | None = Query(None, description="Filter by business model: b2b, b2c, b2g, mixed, or _none"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CompanyPage:
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
    )
    total = crud.count_companies(db, **filter_kwargs)
    items = crud.list_companies(db, page=page, page_size=page_size, sort=sort, **filter_kwargs)
    org: Organization | None = db.get(Organization, current_user.org_id) if current_user.org_id else None
    if current_user.org_id:
        ids = [c.id for c in items]
        org_states = _bulk_org_states(db, ids, current_user.org_id)
        _eff = lambda key, default: crud.get_effective_setting(db, key, org_id=current_user.org_id, default=default)
        w_ai = float(_eff("scoring_weight_ai", "0.70"))
        w_web = float(_eff("scoring_weight_web", "0.20"))
        w_flex = float(_eff("scoring_weight_flex", "0.10"))
        items = [_overlay(c, org_states.get(c.id), w_ai=w_ai, w_web=w_web, w_flex=w_flex) for c in items]
    items = [_apply_web_results_gate(c if isinstance(c, CompanyRead) else _overlay(c, None), org, current_user.is_superadmin) for c in items]
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
    """Enqueue a CSV export job with the given filters.

    Returns job details. The file will be available via /jobs/csv-export/status
    once the job completes. File is stored in S3 for 7 days.

    Credits are deducted upfront based on the tier row cap (worst-case rows),
    rounded up to the nearest 10k unit (bulk_export_basic action).
    """
    from app.api.routes.jobs import _enqueue_or_http_error
    from app.services.s3_client import is_configured

    if not is_configured():
        raise HTTPException(status_code=503, detail="S3 export storage is not configured on this server")

    # Rate limit: max 5 export enqueues per user per 10 minutes.
    if not current_user.is_superadmin:
        key = f"user_{current_user.id}"
        if not check_public_rate_limit(key, "job_rl:csv_export", window=600, max_requests=5):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many export requests. Maximum 5 per 10 minutes.",
            )

    # Determine tier row cap so we can charge credits before enqueuing.
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


class BulkUpdateBody(BaseModel):
    company_ids: list[int]
    field: str
    value: str | None


@router.post("/bulk-update", summary="Bulk update a status field on multiple companies")
def bulk_update_companies(
    body: BulkUpdateBody,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        updated = crud.bulk_update_status(db, body.company_ids, body.field, body.value)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    log_activity(
        db, action="company_bulk_updated",
        user_id=current_user.id, org_id=current_user.org_id,
        meta={"field": body.field, "value": body.value, "count": updated},
    )
    db.commit()
    return {"updated": updated}


class BulkTagBody(BaseModel):
    company_ids: list[int]
    tag: str
    action: str  # "add" | "remove"


@router.post("/bulk-tag", summary="Add or remove a tag across multiple companies")
def bulk_tag_companies(
    body: BulkTagBody,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if body.action not in ("add", "remove"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="action must be 'add' or 'remove'")
    updated = crud.bulk_update_tags(db, body.company_ids, body.tag, body.action)
    log_activity(
        db, action="company_bulk_tagged",
        user_id=current_user.id, org_id=current_user.org_id,
        meta={"tag": body.tag, "action": body.action, "count": updated},
    )
    db.commit()
    return {"updated": updated}


@router.post("", response_model=CompanyRead, status_code=status.HTTP_201_CREATED, summary="Create company")
def create_company(company_in: CompanyCreate, db: Session = Depends(get_db)):
    existing = crud.get_company_by_uid(db, company_in.uid)
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Company with this UID already exists")
    return crud.create_company(db, company_in)


@router.get("/{company_id}", response_model=CompanyRead, summary="Get company by ID")
def get_company(
    company_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    db_company = crud.get_company(db, company_id)
    if not db_company:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    # Filter notes at the ORM level (ORM objects carry org_id/user_id; NoteRead does not).
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

    # Route workflow fields to OrgCompanyState when user belongs to an org
    if workflow and current_user.org_id:
        org_state = crud.get_or_create_org_company_state(db, org_id=current_user.org_id, company_id=company_id)
        for field, value in workflow.items():
            setattr(org_state, field, value)
        db.commit()
        db.refresh(org_state)
    elif workflow:
        # Superadmin without org: write directly to Company (legacy path)
        catalog.update(workflow)

    if catalog:
        crud.update_company(db, db_company, CompanyUpdate(**catalog))

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
