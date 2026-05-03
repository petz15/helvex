"""Search routes: semantic search, keyword/cluster autocomplete, demo company."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app import crud
from app.auth import get_current_user
from app.database import get_db
from app.models.user import User
from app.schemas.company import CompanyRead

from app.api.routes.companies._shared import _overlay

router = APIRouter()

_DEMO_UID = "CHE-435.551.225"  # Post CH AG


@router.get("/semantic-search", summary="Cross-category semantic search using multilingual embeddings")
def semantic_search(
    q: str = Query(..., min_length=2, description="Natural-language search query (DE/FR/IT/EN)"),
    top_k: int = Query(10, ge=1, le=50, description="Maximum results per category type"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Embed the query and return top matching clusters, categories, keywords, and NOGA codes."""
    from app.services.embeddings import embed_single
    import numpy as np

    query_vec = embed_single(q)
    if np.linalg.norm(query_vec) < 1e-6:
        return {"clusters": [], "categories": [], "keywords": [], "noga_codes": [], "query": q}

    taxonomy = crud.get_taxonomy_stats(db)

    def _score_items(items: list[tuple[str, int]], max_items: int = 200) -> list[dict]:
        if not items:
            return []
        labels = [item[0] for item in items[:max_items]]
        counts = {item[0]: item[1] for item in items[:max_items]}
        try:
            from app.services.embeddings import embed_texts
            vecs = embed_texts(labels)
        except Exception:
            return []
        sims = vecs @ query_vec
        top_indices = np.argsort(sims)[::-1][:top_k]
        return [
            {
                "value": labels[i],
                "count": counts.get(labels[i], 0),
                "similarity": round(float(sims[i]), 3),
            }
            for i in top_indices
            if sims[i] > 0.20
        ]

    clusters_raw = [(label, count) for label, count in (taxonomy.get("clusters") or [])]
    categories_raw = [(cat, count) for cat, count, *_ in (taxonomy.get("categories_enriched") or [])]
    keywords_raw = [(kw, count) for kw, count in (taxonomy.get("keywords") or [])]
    noga_raw = [(code_label, count) for code_label, count in (taxonomy.get("noga_codes") or [])]

    return {
        "query": q,
        "clusters": _score_items(clusters_raw),
        "categories": _score_items(categories_raw),
        "keywords": _score_items(keywords_raw),
        "noga_codes": _score_items(noga_raw),
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
