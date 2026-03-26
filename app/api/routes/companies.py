"""Routes for company management and Zefix / Google Search integration."""

import csv
import io
import json
import math
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import crud
from app.api import google_search_client, zefix_client
from app.auth import get_current_user
from app.database import get_db
from app.models.company import Company
from app.models.user import User
from app.schemas.company import (
    CompanyCreate,
    CompanyPage,
    CompanyRead,
    CompanyUpdate,
    GoogleSearchResult,
    ZefixSearchResult,
)
from app.services.scoring import is_social_lead_domain

router = APIRouter(prefix="/companies", tags=["companies"])


# ---------------------------------------------------------------------------
# Zefix search (no DB persistence)
# ---------------------------------------------------------------------------


@router.get("/zefix/search", response_model=list[ZefixSearchResult], summary="Search Zefix API")
def zefix_search(
    name: str = Query(..., description="Company name to search for"),
    max_results: int = Query(20, ge=1, le=100, description="Maximum number of results"),
    active_only: bool = Query(False, description="Return only active companies"),
):
    """Query the Zefix REST API for Swiss companies matching *name*."""
    try:
        return zefix_client.search_companies(name, max_results=max_results, active_only=active_only)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.get("/zefix/{uid}", response_model=dict, summary="Get full Zefix company details")
def zefix_get_company(uid: str):
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
def import_from_zefix(uid: str, db: Session = Depends(get_db)):
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
    num: int = Query(5, ge=1, le=10, description="Number of results"),
    db: Session = Depends(get_db),
):
    """Run a Google Custom Search for *company_id* and return the top results.

    The first result's URL is automatically saved as the company's ``website_url``.
    """
    db_company = crud.get_company(db, company_id)
    if not db_company:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    try:
        results = google_search_client.search_website(db_company.name, num=num)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    if results:
        crud.update_company(
            db,
            db_company,
            CompanyUpdate(website_url=results[0].link),
        )
        db_company.website_checked_at = datetime.now(tz=timezone.utc)
        db.commit()

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
    website_match_score = int(score) if isinstance(score, (int, float)) else None
    social_media_only = is_social_lead_domain(wanted)

    old_values = {f: getattr(db_company, f) for f in (
        "website_url", "review_status", "proposal_status",
        "contact_name", "contact_email", "contact_phone", "tags",
    )}

    updated = crud.update_company(
        db,
        db_company,
        CompanyUpdate(
            website_url=wanted,
            website_match_score=website_match_score,
            social_media_only=social_media_only,
        ),
    )
    new_values = {
        "website_url": wanted,
        "website_match_score": website_match_score,
        "social_media_only": social_media_only,
    }
    crud.record_company_changes(
        db,
        company_id=company_id,
        user_id=current_user.id,
        old_values=old_values,
        new_values=new_values,
    )
    return updated


# ---------------------------------------------------------------------------
# CRUD for companies in the DB
# ---------------------------------------------------------------------------


@router.get("/stats", response_model=dict, summary="Company stats (totals, review/proposal counts)")
def get_stats(db: Session = Depends(get_db)):
    return crud.get_company_stats(db)


@router.get("/cantons", response_model=list[str], summary="List distinct cantons")
def list_cantons(db: Session = Depends(get_db)):
    rows = db.query(Company.canton).filter(Company.canton.isnot(None)).distinct().order_by(Company.canton).all()
    return [r.canton for r in rows]


@router.get("/taxonomy", response_model=dict, summary="Taxonomy stats (clusters, keywords, tags, categories)")
def get_taxonomy(db: Session = Depends(get_db)):
    return crud.get_taxonomy_stats(db)


@router.get("", response_model=CompanyPage, summary="List companies (paginated, filterable)")
def list_companies(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    sort: str = Query("-updated", description="Sort key, e.g. -combined_score, name, -updated"),
    q: str | None = Query(None, description="Filter by name (case-insensitive)"),
    canton: str | None = Query(None),
    review_status: str | None = Query(None, description="Use _none for unset"),
    proposal_status: str | None = Query(None, description="Use _none for unset"),
    google_searched: str | None = Query(None, description="yes | no | no_result"),
    min_google_score: int | None = Query(None, ge=0, le=100),
    min_zefix_score: int | None = Query(None, ge=0, le=100),
    min_claude_score: int | None = Query(None, ge=0, le=100),
    claude_category: str | None = Query(None, description="Use _none for unset"),
    tags: str | None = Query(None),
    tfidf_cluster: str | None = Query(None, description="_none | _any | keyword"),
    purpose_keywords: str | None = Query(None),
    exclude_tags: str | None = Query(None, description="Comma-separated tags to exclude"),
    exclude_review_status: str | None = Query(None),
    exclude_canton: str | None = Query(None),
    exclude_proposal_status: str | None = Query(None),
    db: Session = Depends(get_db),
) -> CompanyPage:
    filter_kwargs = dict(
        name_filter=q,
        canton=canton,
        review_status=review_status,
        proposal_status=proposal_status,
        google_searched=google_searched,
        min_google_score=min_google_score,
        min_zefix_score=min_zefix_score,
        min_claude_score=min_claude_score,
        claude_category=claude_category,
        tags=tags,
        tfidf_cluster=tfidf_cluster,
        purpose_keywords=purpose_keywords,
        exclude_tags=exclude_tags,
        exclude_review_status=exclude_review_status,
        exclude_canton=exclude_canton,
        exclude_proposal_status=exclude_proposal_status,
    )
    total = crud.count_companies(db, **filter_kwargs)
    items = crud.list_companies(db, page=page, page_size=page_size, sort=sort, **filter_kwargs)
    return CompanyPage(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        pages=math.ceil(total / page_size) if total else 1,
    )


@router.get("/export.csv", summary="Export companies as CSV")
def export_companies_csv(
    sort: str = Query("-updated"),
    q: str | None = Query(None),
    canton: str | None = Query(None),
    review_status: str | None = Query(None),
    proposal_status: str | None = Query(None),
    google_searched: str | None = Query(None),
    min_google_score: int | None = Query(None),
    min_zefix_score: int | None = Query(None),
    min_claude_score: int | None = Query(None),
    tags: str | None = Query(None),
    tfidf_cluster: str | None = Query(None),
    purpose_keywords: str | None = Query(None),
    exclude_tags: str | None = Query(None),
    exclude_review_status: str | None = Query(None),
    exclude_canton: str | None = Query(None),
    exclude_proposal_status: str | None = Query(None),
    db: Session = Depends(get_db),
):
    companies = crud.list_companies(
        db,
        page=1,
        page_size=10000,
        sort=sort,
        name_filter=q,
        canton=canton,
        review_status=review_status,
        proposal_status=proposal_status,
        google_searched=google_searched,
        min_google_score=min_google_score,
        min_zefix_score=min_zefix_score,
        min_claude_score=min_claude_score,
        tags=tags,
        tfidf_cluster=tfidf_cluster,
        purpose_keywords=purpose_keywords,
        exclude_tags=exclude_tags,
        exclude_review_status=exclude_review_status,
        exclude_canton=exclude_canton,
        exclude_proposal_status=exclude_proposal_status,
    )

    _HEADERS = [
        "uid", "name", "legal_form", "status", "municipality", "canton",
        "website_url", "website_match_score", "zefix_score", "claude_score", "combined_score",
        "review_status", "proposal_status", "contact_name", "contact_email", "contact_phone",
        "tags", "claude_category", "tfidf_cluster", "purpose_keywords",
        "created_at", "updated_at",
    ]

    def _generate():
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(_HEADERS)
        yield buf.getvalue()
        for c in companies:
            buf = io.StringIO()
            writer = csv.writer(buf)
            writer.writerow([
                c.uid, c.name, c.legal_form or "", c.status or "",
                c.municipality or "", c.canton or "",
                c.website_url or "",
                c.website_match_score if c.website_match_score is not None else "",
                c.zefix_score if c.zefix_score is not None else "",
                c.claude_score if c.claude_score is not None else "",
                c.combined_score if c.combined_score is not None else "",
                c.review_status or "", c.proposal_status or "",
                c.contact_name or "", c.contact_email or "", c.contact_phone or "",
                c.tags or "", c.claude_category or "", c.tfidf_cluster or "",
                c.purpose_keywords or "",
                c.created_at.isoformat(), c.updated_at.isoformat(),
            ])
            yield buf.getvalue()

    return StreamingResponse(
        _generate(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=helvex_export.csv"},
    )


@router.post("", response_model=CompanyRead, status_code=status.HTTP_201_CREATED, summary="Create company")
def create_company(company_in: CompanyCreate, db: Session = Depends(get_db)):
    existing = crud.get_company_by_uid(db, company_in.uid)
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Company with this UID already exists")
    return crud.create_company(db, company_in)


@router.get("/{company_id}", response_model=CompanyRead, summary="Get company by ID")
def get_company(company_id: int, db: Session = Depends(get_db)):
    db_company = crud.get_company(db, company_id)
    if not db_company:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    return db_company


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
    old_values = {f: getattr(db_company, f) for f in (
        "website_url", "review_status", "proposal_status",
        "contact_name", "contact_email", "contact_phone", "tags",
    )}
    updated = crud.update_company(db, db_company, company_in)
    new_values = company_in.model_dump(exclude_unset=True)
    crud.record_company_changes(
        db, company_id=company_id, user_id=current_user.id,
        old_values=old_values, new_values=new_values,
    )
    return updated


@router.delete("/{company_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete company")
def delete_company(
    company_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    db_company = crud.get_company(db, company_id)
    if not db_company:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    crud.delete_company(db, db_company)
