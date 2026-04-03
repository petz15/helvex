"""REST endpoint for map data."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app import crud
from app.api.geocoding_client import geocode_address
from app.database import get_db
from app.models.company import Company as CompanyModel

router = APIRouter(tags=["map"])

_MAP_MAX_POINTS = 20_000
_CANCELLED_TERMS = ["being_cancelled", "dissolved", "gelöscht", "radiation", "liquidation"]


def _normalise_google_searched(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip().lower()
    if value in {"1", "true", "yes", "y"}:
        return "yes"
    if value in {"0", "false", "no", "n"}:
        return "no"
    if value == "no_result":
        return "no_result"
    return None


def _apply_map_filters(
    query,
    *,
    q: str | None,
    uid: str | None,
    canton: str | None,
    review_status: str | None,
    contact_status: str | None,
    google_searched: str | None,
    min_web_score: int | None,
    max_web_score: int | None,
    min_flex_score: int | None,
    max_flex_score: int | None,
    min_ai_score: int | None,
    max_ai_score: int | None,
    min_combined_score: int | None,
    max_combined_score: int | None,
    ai_category: str | None,
    tags: str | None,
    tfidf_cluster: str | None,
    purpose_keywords: str | None,
    noga_code: str | None,
    noga_label: str | None,
    noga_level: str | None,
    exclude_tags: str | None,
    exclude_review_status: str | None,
    exclude_canton: str | None,
    exclude_contact_status: str | None,
    exclude_tfidf_cluster: str | None,
    exclude_purpose_keywords: str | None,
    exclude_ai_category: str | None,
    exclude_noga_code: str | None,
    exclude_noga_label: str | None,
    exclude_noga_level: str | None,
    zefix_status: str | None,
    legal_form: str | None,
    registered_after: str | None,
    registered_before: str | None,
    keywords: str | None,
    hide_cancelled: bool,
    min_lat: float | None,
    max_lat: float | None,
    min_lon: float | None,
    max_lon: float | None,
):
    """Apply search-style company filters to an existing SQLAlchemy query."""
    query = crud._apply_filters(
        query,
        name_filter=q,
        uid_filter=uid,
        canton=canton,
        review_status=review_status,
        contact_status=contact_status,
        google_searched=_normalise_google_searched(google_searched),
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
        zefix_status=zefix_status,
        legal_form=legal_form,
        registered_after=registered_after,
        registered_before=registered_before,
    )
    if keywords:
        kw_terms = [t.strip() for t in keywords.split(",") if t.strip()]
        if kw_terms:
            query = query.filter(or_(*(
                or_(
                    CompanyModel.purpose_keywords.ilike(f"%{kw}%"),
                    CompanyModel.tfidf_cluster.ilike(f"%{kw}%"),
                )
                for kw in kw_terms
            )))
    if hide_cancelled:
        query = query.filter(~or_(*(
            CompanyModel.status.ilike(f"%{t}%") for t in _CANCELLED_TERMS
        )))
    if min_lat is not None and max_lat is not None:
        query = query.filter(CompanyModel.lat >= min_lat, CompanyModel.lat <= max_lat)
    if min_lon is not None and max_lon is not None:
        query = query.filter(CompanyModel.lon >= min_lon, CompanyModel.lon <= max_lon)
    return query


@router.get("/map/clusters")
def map_clusters(
    zoom: int = Query(8, ge=1, le=18),
    q: str | None = Query(None),
    uid: str | None = Query(None),
    canton: str | None = Query(None),
    review_status: str | None = Query(None),
    contact_status: str | None = Query(None),
    google_searched: str | None = Query(None),
    min_web_score: int | None = Query(None),
    max_web_score: int | None = Query(None),
    min_flex_score: int | None = Query(None),
    max_flex_score: int | None = Query(None),
    min_ai_score: int | None = Query(None),
    max_ai_score: int | None = Query(None),
    min_combined_score: int | None = Query(None),
    max_combined_score: int | None = Query(None),
    ai_category: str | None = Query(None),
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
    exclude_tfidf_cluster: str | None = Query(None),
    exclude_purpose_keywords: str | None = Query(None),
    exclude_ai_category: str | None = Query(None),
    exclude_noga_code: str | None = Query(None),
    exclude_noga_label: str | None = Query(None),
    exclude_noga_level: str | None = Query(None),
    zefix_status: str | None = Query(None),
    legal_form: str | None = Query(None),
    registered_after: str | None = Query(None),
    registered_before: str | None = Query(None),
    keywords: str | None = Query(None),
    hide_cancelled: bool = Query(False),
    min_lat: float | None = Query(None),
    max_lat: float | None = Query(None),
    min_lon: float | None = Query(None),
    max_lon: float | None = Query(None),
    db: Session = Depends(get_db),
):
    """Return grid-aggregated cluster counts for low-zoom map views.

    Grid cell size (degrees) by zoom:
      zoom ≤ 6  → 1.0°  (~111 km)
      zoom 7–8  → 0.5°  (~55 km)
      zoom 9    → 0.25° (~28 km)
      zoom 10   → 0.1°  (~11 km, roughly town level)
      zoom 11   → 0.05° (~5 km,  city-district level)
    """
    if zoom <= 6:
        precision = 1.0
    elif zoom <= 8:
        precision = 0.5
    elif zoom <= 9:
        precision = 0.25
    elif zoom <= 10:
        precision = 0.1
    else:
        precision = 0.05   # ~5 km — city-district level at zoom 11

    # Grid bucket keys (floor only — used for grouping, not positioning)
    lat_bucket = func.floor(CompanyModel.lat / precision)
    lon_bucket = func.floor(CompanyModel.lon / precision)

    base = db.query(CompanyModel).filter(
        CompanyModel.lat.isnot(None),
        CompanyModel.lon.isnot(None),
    )
    base = _apply_map_filters(
        base,
        q=q,
        uid=uid,
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
        zefix_status=zefix_status,
        legal_form=legal_form,
        registered_after=registered_after,
        registered_before=registered_before,
        keywords=keywords,
        hide_cancelled=hide_cancelled,
        min_lat=min_lat, max_lat=max_lat, min_lon=min_lon, max_lon=max_lon,
    )

    rows = base.with_entities(
        # Use average of actual company coordinates so the bubble sits at the
        # real centre of mass (e.g. a PLZ cluster), not a mathematical grid midpoint.
        func.avg(CompanyModel.lat).label("lat"),
        func.avg(CompanyModel.lon).label("lon"),
        func.count(CompanyModel.id).label("count"),
        func.avg(
            func.coalesce(CompanyModel.ai_score * 0.70, 0.0)
            + func.coalesce(CompanyModel.web_score * 0.20, 0.0)
            + func.coalesce(CompanyModel.flex_score * 0.10, 0.0)
        ).label("avg_score"),
    ).group_by(lat_bucket, lon_bucket).all()

    cells = [
        {
            "lat": float(r.lat),
            "lon": float(r.lon),
            "count": r.count,
            "avg_score": round(float(r.avg_score), 1) if r.avg_score is not None else None,
        }
        for r in rows
    ]
    return JSONResponse({"cells": cells, "total": sum(r.count for r in rows)})


@router.get("/map")
def map_data(
    q: str | None = Query(None),
    uid: str | None = Query(None),
    canton: str | None = Query(None),
    review_status: str | None = Query(None),
    contact_status: str | None = Query(None),
    google_searched: str | None = Query(None),
    min_web_score: int | None = Query(None),
    max_web_score: int | None = Query(None),
    min_flex_score: int | None = Query(None),
    max_flex_score: int | None = Query(None),
    min_ai_score: int | None = Query(None),
    max_ai_score: int | None = Query(None),
    min_combined_score: int | None = Query(None),
    max_combined_score: int | None = Query(None),
    ai_category: str | None = Query(None),
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
    exclude_tfidf_cluster: str | None = Query(None),
    exclude_purpose_keywords: str | None = Query(None),
    exclude_ai_category: str | None = Query(None),
    exclude_noga_code: str | None = Query(None),
    exclude_noga_label: str | None = Query(None),
    exclude_noga_level: str | None = Query(None),
    zefix_status: str | None = Query(None),
    legal_form: str | None = Query(None),
    registered_after: str | None = Query(None),
    registered_before: str | None = Query(None),
    keywords: str | None = Query(None),
    hide_cancelled: bool = Query(False),
    min_lat: float | None = Query(None),
    max_lat: float | None = Query(None),
    min_lon: float | None = Query(None),
    max_lon: float | None = Query(None),
    db: Session = Depends(get_db),
):
    """Return lightweight point list for detail-zoom map views — geocoded companies only."""
    query = db.query(
        CompanyModel.id,
        CompanyModel.name,
        CompanyModel.lat,
        CompanyModel.lon,
        CompanyModel.web_score,
        CompanyModel.flex_score,
        CompanyModel.ai_score,
        CompanyModel.canton,
        CompanyModel.municipality,
        CompanyModel.website_url,
        CompanyModel.review_status,
        CompanyModel.status,
    ).filter(
        CompanyModel.lat.isnot(None),
        CompanyModel.lon.isnot(None),
    )

    query = _apply_map_filters(
        query,
        q=q,
        uid=uid,
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
        zefix_status=zefix_status,
        legal_form=legal_form,
        registered_after=registered_after,
        registered_before=registered_before,
        keywords=keywords,
        hide_cancelled=hide_cancelled,
        min_lat=min_lat, max_lat=max_lat, min_lon=min_lon, max_lon=max_lon,
    )

    rows = query.limit(_MAP_MAX_POINTS + 1).all()
    truncated = len(rows) > _MAP_MAX_POINTS
    if truncated:
        rows = rows[:_MAP_MAX_POINTS]

    features = [
        {
            "id": r.id,
            "name": r.name,
            "lat": r.lat,
            "lon": r.lon,
            "web_score": r.web_score,
            "flex_score": r.flex_score,
            "ai_score": r.ai_score,
            "canton": r.canton,
            "municipality": r.municipality,
            "website": r.website_url,
            "review": r.review_status,
            "status": r.status,
        }
        for r in rows
    ]
    return JSONResponse({
        "count": len(features),
        "features": features,
        "truncated": truncated,
        "max_points": _MAP_MAX_POINTS,
    })


@router.get("/map/geocode")
def geocode_map_address(address: str = Query(..., min_length=3, max_length=300)):
    coords = geocode_address(address)
    if coords is None:
        raise HTTPException(status_code=404, detail="Address not found")
    lat, lon = coords
    return JSONResponse({"lat": lat, "lon": lon, "address": address})
