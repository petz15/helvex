"""REST endpoint for map data."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.company import Company as CompanyModel

router = APIRouter(tags=["map"])

_MAP_MAX_POINTS = 20_000
_CANCELLED_TERMS = ["being_cancelled", "dissolved", "gelöscht", "radiation", "liquidation"]


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
    """Return lightweight GeoJSON-style list for the map — geocoded companies only."""
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
