"""Search routes: semantic search, keyword/cluster autocomplete, demo company."""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app import crud
from app.auth import get_current_user
from app.database import get_db
from app.models.user import User
from app.schemas.company import CompanyRead

from app.api.routes.companies._shared import _overlay

router = APIRouter()

_DEMO_UID = "CHE-435.551.225"  # Post CH AG


def _fuzzy_score_items(
    items: list[tuple[str, int]],
    q_tokens: list[str],
    q_lower: str,
    top_k: int,
) -> list[dict]:
    """Score taxonomy items against a query using token overlap — no ML model required."""
    if not items:
        return []
    scored: list[tuple[float, str, int]] = []
    for label, count in items:
        label_lower = label.lower()
        if q_lower in label_lower:
            score = 1.0
        else:
            label_tokens = set(label_lower.replace("-", " ").replace("_", " ").split())
            hits = sum(
                1 for t in q_tokens
                if t in label_tokens or any(t in lt for lt in label_tokens)
            )
            score = hits / len(q_tokens) if q_tokens else 0.0
        if score > 0:
            scored.append((score, label, count))
    scored.sort(key=lambda x: (-x[0], -x[2]))
    return [
        {"value": label, "count": count, "similarity": round(score, 3)}
        for score, label, count in scored[:top_k]
    ]


@router.get("/semantic-search", summary="Cross-category search; proxies to ML worker when available")
def semantic_search(
    request: Request,
    q: str = Query(..., min_length=2, description="Natural-language search query (DE/FR/IT/EN)"),
    top_k: int = Query(10, ge=1, le=50, description="Maximum results per category type"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Proxy to the ML worker pod if ML_WORKER_INTERNAL_URL is set (full embedding-based search).
    Falls back to token-overlap fuzzy matching when the ML worker is unavailable.
    """
    ml_url = os.getenv("ML_WORKER_INTERNAL_URL")
    if ml_url:
        import httpx
        try:
            r = httpx.get(
                f"{ml_url}/api/v1/companies/semantic-search",
                params={"q": q, "top_k": top_k},
                headers={"cookie": request.headers.get("cookie", "")},
                timeout=15.0,
            )
            r.raise_for_status()
            return r.json()
        except Exception:
            pass  # fall through to fuzzy on any ML worker failure

    taxonomy = crud.get_taxonomy_stats(db)
    q_lower = q.lower()
    q_tokens = [t for t in q_lower.replace("-", " ").replace("_", " ").split() if len(t) > 1]
    clusters_raw = [(label, count) for label, count in (taxonomy.get("clusters") or [])]
    categories_raw = [(cat, count) for cat, count, *_ in (taxonomy.get("categories_enriched") or [])]
    keywords_raw = [(kw, count) for kw, count in (taxonomy.get("keywords") or [])]
    noga_raw = [(code_label, count) for code_label, count in (taxonomy.get("noga_codes") or [])]
    return {
        "query": q,
        "clusters": _fuzzy_score_items(clusters_raw, q_tokens, q_lower, top_k),
        "categories": _fuzzy_score_items(categories_raw, q_tokens, q_lower, top_k),
        "keywords": _fuzzy_score_items(keywords_raw, q_tokens, q_lower, top_k),
        "noga_codes": _fuzzy_score_items(noga_raw, q_tokens, q_lower, top_k),
    }


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
