"""REST endpoint for map data."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.company import Company as CompanyModel

router = APIRouter(tags=["map"])

_MAP_MAX_POINTS = 5_000
_CANCELLED_TERMS = ["being_cancelled", "dissolved", "gelöscht", "radiation", "liquidation"]


def _apply_map_filters(
    query,
    *,
    canton: str | None,
    review_status: str | None,
    google_searched: str | None,
    min_google_score: int | None,
    min_zefix_score: int | None,
    min_claude_score: int | None,
    min_combined_score: int | None,
    keywords: str | None,
    hide_cancelled: bool,
    min_lat: float | None,
    max_lat: float | None,
    min_lon: float | None,
    max_lon: float | None,
):
    """Apply all map filter predicates to an existing SQLAlchemy query."""
    if canton:
        query = query.filter(CompanyModel.canton == canton)
    if review_status:
        query = query.filter(CompanyModel.review_status == review_status)
    if google_searched == "true":
        query = query.filter(CompanyModel.website_checked_at.isnot(None))
    elif google_searched == "false":
        query = query.filter(CompanyModel.website_checked_at.is_(None))
    if min_google_score is not None:
        query = query.filter(CompanyModel.website_match_score >= min_google_score)
    if min_zefix_score is not None:
        query = query.filter(CompanyModel.zefix_score >= min_zefix_score)
    if min_claude_score is not None:
        query = query.filter(CompanyModel.claude_score >= min_claude_score)
    if min_combined_score is not None:
        combined_expr = (
            func.coalesce(CompanyModel.claude_score * 0.70, 0.0)
            + func.coalesce(CompanyModel.website_match_score * 0.20, 0.0)
            + func.coalesce(CompanyModel.zefix_score * 0.10, 0.0)
        )
        query = query.filter(combined_expr >= min_combined_score)
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
    canton: str | None = Query(None),
    review_status: str | None = Query(None),
    google_searched: str | None = Query(None),
    min_google_score: int | None = Query(None),
    min_zefix_score: int | None = Query(None),
    min_claude_score: int | None = Query(None),
    min_combined_score: int | None = Query(None),
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
        canton=canton, review_status=review_status, google_searched=google_searched,
        min_google_score=min_google_score, min_zefix_score=min_zefix_score,
        min_claude_score=min_claude_score, min_combined_score=min_combined_score,
        keywords=keywords, hide_cancelled=hide_cancelled,
        min_lat=min_lat, max_lat=max_lat, min_lon=min_lon, max_lon=max_lon,
    )

    rows = base.with_entities(
        # Use average of actual company coordinates so the bubble sits at the
        # real centre of mass (e.g. a PLZ cluster), not a mathematical grid midpoint.
        func.avg(CompanyModel.lat).label("lat"),
        func.avg(CompanyModel.lon).label("lon"),
        func.count(CompanyModel.id).label("count"),
        func.avg(
            func.coalesce(CompanyModel.claude_score * 0.70, 0.0)
            + func.coalesce(CompanyModel.website_match_score * 0.20, 0.0)
            + func.coalesce(CompanyModel.zefix_score * 0.10, 0.0)
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
    canton: str | None = Query(None),
    review_status: str | None = Query(None),
    google_searched: str | None = Query(None),
    min_google_score: int | None = Query(None),
    min_zefix_score: int | None = Query(None),
    min_claude_score: int | None = Query(None),
    min_combined_score: int | None = Query(None),
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
        CompanyModel.website_match_score,
        CompanyModel.zefix_score,
        CompanyModel.claude_score,
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
        canton=canton, review_status=review_status, google_searched=google_searched,
        min_google_score=min_google_score, min_zefix_score=min_zefix_score,
        min_claude_score=min_claude_score, min_combined_score=min_combined_score,
        keywords=keywords, hide_cancelled=hide_cancelled,
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
            "google_score": r.website_match_score,
            "zefix_score": r.zefix_score,
            "claude_score": r.claude_score,
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
