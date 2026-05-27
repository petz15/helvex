"""Analytics routes: stats, cantons, taxonomy, NOGA hierarchy, market segments."""

from __future__ import annotations

import json
import time

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app import crud
from app.auth import get_current_user
from app.database import get_db
from app.models.company import Company
from app.models.user import User

from app.api.routes.companies._shared import _noga_hierarchy_cache

router = APIRouter()

_market_segments_cache: dict | None = None
_market_segments_cache_ts: float = 0.0
_MARKET_SEGMENTS_TTL = 3600.0


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


@router.get("/noga-hierarchy", summary="NOGA codes as a collapsible hierarchy with global company counts")
def get_noga_hierarchy(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    # Counts are always global — the taxonomy browser shows the full registry.
    _GLOBAL = None
    if _GLOBAL in _noga_hierarchy_cache:
        hierarchy = _noga_hierarchy_cache[_GLOBAL]
    else:
        hierarchy = crud.get_noga_hierarchy(db, org_id=None)
        _noga_hierarchy_cache[_GLOBAL] = hierarchy

    response = Response(content=json.dumps(hierarchy), media_type="application/json")
    response.headers["Cache-Control"] = "public, max-age=3600, s-maxage=86400"
    response.headers["ETag"] = f'"{hash(str(hierarchy)) & 0x7fffffff}"'
    return response


@router.get("/market-segments", summary="NOGA section stats for the market map treemap")
def get_market_segments(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Return per-NOGA-section aggregates cached in-process for 1 hour."""
    global _market_segments_cache, _market_segments_cache_ts

    now = time.monotonic()
    if _market_segments_cache is not None and (now - _market_segments_cache_ts) < _MARKET_SEGMENTS_TTL:
        response = Response(content=json.dumps(_market_segments_cache), media_type="application/json")
        response.headers["Cache-Control"] = "public, max-age=3600"
        return response

    segments = _build_market_segments(db)
    _market_segments_cache = segments
    _market_segments_cache_ts = now

    response = Response(content=json.dumps(segments), media_type="application/json")
    response.headers["Cache-Control"] = "public, max-age=3600"
    return response


def _build_market_segments(db: Session) -> list[dict]:
    from collections import defaultdict
    from datetime import date
    from sqlalchemy import text as sa_text

    from app.services.noga import _repo_root
    lookup_path = _repo_root() / "noga_lookup.json"
    section_labels: dict[str, dict[str, str]] = {}
    if lookup_path.exists():
        import json as _json
        with lookup_path.open("r", encoding="utf-8") as f:
            payload: dict = _json.load(f)
        for code, node in payload.items():
            if not isinstance(node, dict):
                continue
            if len(str(code)) == 1 and str(code).isalpha():
                name = node.get("name")
                if isinstance(name, dict):
                    labels: dict[str, str] = {}
                    for lang in ("de", "fr", "it", "en"):
                        v = name.get(lang)
                        if isinstance(v, str) and v.strip():
                            labels[lang] = v.strip()
                    section_labels[str(code).upper()] = labels
                elif isinstance(name, str) and name.strip():
                    section_labels[str(code).upper()] = {"de": name.strip()}

    cutoff_18m = (date.today().replace(year=date.today().year - 1) if date.today().month <= 6
                  else date.today().replace(month=date.today().month - 6))

    rows = db.execute(sa_text("""
        SELECT
            LEFT(noga_path, 1)                          AS section,
            COUNT(*)                                    AS company_count,
            AVG(combined_score)                         AS avg_relevance,
            COUNT(CASE WHEN first_sogc_date >= CAST(:cutoff AS text) THEN 1 END) AS growth_recent,
            MODE() WITHIN GROUP (ORDER BY canton)       AS canton_top,
            COUNT(canton)                               AS canton_total
        FROM companies
        WHERE noga_path IS NOT NULL
          AND LEFT(noga_path, 1) ~ '^[A-Za-z]$'
        GROUP BY LEFT(noga_path, 1)
        ORDER BY company_count DESC
    """), {"cutoff": cutoff_18m.isoformat()}).fetchall()

    kw_rows = db.execute(sa_text("""
        SELECT LEFT(noga_path, 1) AS section, kw, COUNT(*) AS cnt
        FROM companies,
             LATERAL unnest(string_to_array(purpose_keywords, ',')) AS kw
        WHERE noga_path IS NOT NULL
          AND purpose_keywords IS NOT NULL
          AND LEFT(noga_path, 1) ~ '^[A-Za-z]$'
        GROUP BY LEFT(noga_path, 1), kw
    """)).fetchall()

    kw_by_section: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for r in kw_rows:
        kw_by_section[r.section.upper()].append((r.kw.strip(), r.cnt))

    segments = []
    for row in rows:
        section = (row.section or "").upper()
        if not section:
            continue
        top_kws = sorted(kw_by_section.get(section, []), key=lambda x: x[1], reverse=True)
        canton_pct = round(100 * (row.canton_top is not None) / max(row.canton_total, 1)) if row.canton_total else 0

        segments.append({
            "section": section,
            "labels": section_labels.get(section, {}),
            "company_count": row.company_count,
            "avg_relevance": round(float(row.avg_relevance), 1) if row.avg_relevance is not None else None,
            "top_keywords": [kw for kw, _ in top_kws[:5]],
            "canton_top": row.canton_top,
            "canton_pct": canton_pct,
            "growth_recent": row.growth_recent,
        })

    return segments
