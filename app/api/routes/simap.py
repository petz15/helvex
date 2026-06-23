"""SIMAP contract search and export endpoints.

Searches across all simap_awards + simap_award_vendors using:
  - PostgreSQL full-text search (tsvector/GIN) for keyword queries
  - Exact and prefix filters for CPV, date range, price range
  - pg_trgm similarity for vendor-name lookup
  - Optional filter to only show awards matched to a company in our DB

Source distinction: archive records have simap_project_id starting with "arch-".
"""
from __future__ import annotations

import csv
import io
import math
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func
from sqlalchemy.orm import Session, aliased

from app.auth import get_current_user
from app.database import get_db
from app.models.company import Company
from app.models.simap_award import SimapAward
from app.models.simap_award_vendor import SimapAwardVendor
from app.models.user import User

router = APIRouter(prefix="/api/v1/simap", tags=["simap"])

_MAX_PER_PAGE = 100
_CSV_MAX = 10_000


# ── helpers ──────────────────────────────────────────────────────────────────

def _best_title(a: SimapAward) -> str:
    return a.title_de or a.title_fr or a.title_it or ""


def _best_authority(a: SimapAward) -> str:
    return a.proc_office_name_de or a.proc_office_name_fr or a.proc_office_name_it or ""


def _source(a: SimapAward) -> str:
    return "archive" if (a.simap_project_id or "").startswith("arch-") else "api"


def _build_query(
    db: Session,
    q: str | None,
    from_date: date | None,
    to_date: date | None,
    cpv: str | None,
    min_price: float | None,
    max_price: float | None,
    matched_only: bool,
    source: str | None,
    vendor_name: str | None,
):
    """Build the base SQLAlchemy query joining simap_awards + vendors."""
    # We aggregate vendors into a JSON-like sub-select per award.
    # To keep things simple, we return one row per (award, vendor) and
    # deduplicate award metadata in the serialiser.  For awards with multiple
    # vendors we take the one with a company_id if available.

    av = aliased(SimapAwardVendor, name="av")
    co = aliased(Company, name="co")

    query = (
        db.query(SimapAward, av, co)
        .outerjoin(av, av.award_id == SimapAward.id)
        .outerjoin(co, co.id == av.company_id)
    )

    if q:
        fts_col = func.to_tsvector(
            "simple",
            func.coalesce(SimapAward.title_de, "") + " " +
            func.coalesce(SimapAward.title_fr, "") + " " +
            func.coalesce(SimapAward.title_it, "") + " " +
            func.coalesce(SimapAward.proc_office_name_de, "") + " " +
            func.coalesce(SimapAward.proc_office_name_fr, "") + " " +
            func.coalesce(SimapAward.proc_office_name_it, ""),
        )
        tsq = func.plainto_tsquery("simple", q)
        query = query.filter(fts_col.op("@@")(tsq))

    if from_date:
        query = query.filter(SimapAward.publication_date >= from_date)
    if to_date:
        query = query.filter(SimapAward.publication_date <= to_date)

    if cpv:
        query = query.filter(SimapAward.cpv_code.like(cpv.rstrip("*") + "%"))

    if source == "archive":
        query = query.filter(SimapAward.simap_project_id.like("arch-%"))
    elif source == "api":
        query = query.filter(SimapAward.simap_project_id.not_like("arch-%"))

    if matched_only:
        query = query.filter(av.company_id.isnot(None))

    if vendor_name:
        query = query.filter(
            func.similarity(av.vendor_name, vendor_name) >= 0.40
        )

    # Price filter — applies to vendor price; also check if award has any vendor
    # in that range (loose: if ANY vendor matches, include award)
    if min_price is not None:
        query = query.filter(av.price >= min_price)
    if max_price is not None:
        query = query.filter(av.price <= max_price)

    return query, av, co


def _serialize_row(award: SimapAward, vendor: SimapAwardVendor | None, company: Company | None) -> dict[str, Any]:
    return {
        "id": award.id,
        "simap_project_id": award.simap_project_id,
        "source": "archive" if (award.simap_project_id or "").startswith("arch-") else "api",
        "publication_date": award.publication_date.isoformat() if award.publication_date else None,
        "award_decision_date": award.award_decision_date.isoformat() if award.award_decision_date else None,
        "title": award.title_de or award.title_fr or award.title_it or "",
        "authority": award.proc_office_name_de or award.proc_office_name_fr or award.proc_office_name_it or "",
        "cpv_code": award.cpv_code,
        "number_of_submissions": award.number_of_submissions,
        "pub_type": award.pub_type,
        "process_type": award.process_type,
        "vendor_name": vendor.vendor_name if vendor else None,
        "vendor_uid": vendor.vendor_uid if vendor else None,
        "vendor_city": vendor.vendor_city if vendor else None,
        "vendor_postal_code": vendor.vendor_postal_code if vendor else None,
        "vendor_country": vendor.vendor_country if vendor else None,
        "price": float(vendor.price) if vendor and vendor.price is not None else None,
        "price_currency": vendor.price_currency if vendor else None,
        "company_id": company.id if company else None,
        "company_name": company.name if company else None,
        "company_uid": company.uid if company else None,
    }


# ── endpoints ─────────────────────────────────────────────────────────────────

@router.get("/search")
def simap_search(
    q: str | None = Query(None, description="Full-text keyword search over title and authority"),
    from_date: date | None = Query(None, alias="from_date"),
    to_date: date | None = Query(None, alias="to_date"),
    cpv: str | None = Query(None, description="CPV code prefix, e.g. '451' matches all 451xxxxx codes"),
    min_price: float | None = Query(None),
    max_price: float | None = Query(None),
    matched_only: bool = Query(False, description="Only show awards where vendor matched a company in our DB"),
    source: str | None = Query(None, description="'api' (post-2024), 'archive' (pre-2024), or omit for all"),
    vendor_name: str | None = Query(None, description="Fuzzy vendor name search (pg_trgm similarity ≥ 0.40)"),
    page: int = Query(1, ge=1),
    per_page: int = Query(25, ge=1, le=_MAX_PER_PAGE),
    sort_by: str = Query("date", description="'date', 'price', 'title'"),
    sort_dir: str = Query("desc", description="'asc' or 'desc'"),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Search SIMAP contract award notices with filters and full-text search."""
    if source and source not in ("api", "archive"):
        raise HTTPException(status_code=400, detail="source must be 'api' or 'archive'")

    base_q, av, co = _build_query(
        db, q, from_date, to_date, cpv, min_price, max_price,
        matched_only, source, vendor_name,
    )

    # Count distinct awards (not rows, since one award may have multiple vendor rows)
    count_q = base_q.with_entities(func.count(func.distinct(SimapAward.id)))
    total = count_q.scalar() or 0

    # Sort
    if sort_by == "price":
        sort_col = av.price
    elif sort_by == "title":
        sort_col = SimapAward.title_de
    else:
        sort_col = SimapAward.publication_date

    if sort_dir == "asc":
        base_q = base_q.order_by(sort_col.asc().nullslast(), SimapAward.id.asc())
    else:
        base_q = base_q.order_by(sort_col.desc().nullsfirst(), SimapAward.id.desc())

    rows = base_q.offset((page - 1) * per_page).limit(per_page).all()

    results = [_serialize_row(award, vendor, company) for award, vendor, company in rows]

    return {
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": math.ceil(total / per_page) if per_page else 1,
        "results": results,
    }


@router.get("/export.csv")
def simap_export_csv(
    q: str | None = Query(None),
    from_date: date | None = Query(None, alias="from_date"),
    to_date: date | None = Query(None, alias="to_date"),
    cpv: str | None = Query(None),
    min_price: float | None = Query(None),
    max_price: float | None = Query(None),
    matched_only: bool = Query(False),
    source: str | None = Query(None),
    vendor_name: str | None = Query(None),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Export SIMAP search results as CSV (capped at 10,000 rows)."""
    base_q, av, co = _build_query(
        db, q, from_date, to_date, cpv, min_price, max_price,
        matched_only, source, vendor_name,
    )
    base_q = base_q.order_by(SimapAward.publication_date.desc(), SimapAward.id.desc())
    rows = base_q.limit(_CSV_MAX).all()

    def _generate():
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow([
            "publication_date", "award_decision_date", "title", "authority",
            "cpv_code", "process_type", "number_of_submissions",
            "vendor_name", "vendor_uid", "vendor_city", "vendor_postal_code",
            "vendor_country", "price", "price_currency",
            "company_id", "company_name", "company_uid",
            "simap_project_id", "source",
        ])
        yield out.getvalue()
        for award, vendor, company in rows:
            out = io.StringIO()
            writer = csv.writer(out)
            writer.writerow([
                award.publication_date.isoformat() if award.publication_date else "",
                award.award_decision_date.isoformat() if award.award_decision_date else "",
                award.title_de or award.title_fr or award.title_it or "",
                award.proc_office_name_de or award.proc_office_name_fr or award.proc_office_name_it or "",
                award.cpv_code or "",
                award.process_type or "",
                award.number_of_submissions if award.number_of_submissions is not None else "",
                vendor.vendor_name if vendor else "",
                vendor.vendor_uid if vendor else "",
                vendor.vendor_city if vendor else "",
                vendor.vendor_postal_code if vendor else "",
                vendor.vendor_country if vendor else "",
                float(vendor.price) if vendor and vendor.price is not None else "",
                vendor.price_currency if vendor else "",
                company.id if company else "",
                company.name if company else "",
                company.uid if company else "",
                award.simap_project_id or "",
                "archive" if (award.simap_project_id or "").startswith("arch-") else "api",
            ])
            yield out.getvalue()

    return StreamingResponse(
        _generate(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=simap_contracts.csv"},
    )
