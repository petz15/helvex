"""Batch pipeline for computing and storing company purpose embeddings.

Two embedding types:
  'purpose_full'  — raw company.purpose text
  'purpose_clean' — boilerplate-stripped purpose text (same stripping as NOGA pipeline)

Vector operations use raw SQL with pgvector — the ORM model handles FK/CRUD only.
The embedding model (paraphrase-multilingual-mpnet-base-v2, 768-dim) is shared with
the NOGA pipeline via app.services.embeddings — it is lazy-loaded and cached.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import numpy as np
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from app import crud
from app.models.company import Company
from app.models.company_embedding import CompanyEmbedding

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Low-level upsert
# ---------------------------------------------------------------------------

def _vec_str(vec: np.ndarray) -> str:
    return "[" + ",".join(f"{x:.8f}" for x in vec.tolist()) + "]"


def upsert_embeddings_bulk(
    db: Session,
    rows: list[tuple[int, str, np.ndarray, str | None]],
) -> None:
    """Bulk upsert (company_id, embedding_type, vec, lang) rows."""
    if not rows:
        return
    now = datetime.now(timezone.utc).isoformat()
    for company_id, etype, vec, lang in rows:
        db.execute(sql_text("""
            INSERT INTO company_embeddings (company_id, embedding_type, embedding, lang, computed_at)
            VALUES (:cid, :etype, CAST(:vec AS vector), :lang, :now)
            ON CONFLICT (company_id, embedding_type)
            DO UPDATE SET embedding = CAST(:vec AS vector), lang = :lang, computed_at = :now
        """), {"cid": company_id, "etype": etype, "vec": _vec_str(vec), "lang": lang, "now": now})


# ---------------------------------------------------------------------------
# Boilerplate stripping helper (reuses the NOGA pipeline logic)
# ---------------------------------------------------------------------------

def _strip(purpose: str, patterns) -> str:
    from app.services.noga import _strip_purpose_boilerplate
    return _strip_purpose_boilerplate(purpose, patterns)


# ---------------------------------------------------------------------------
# Shared keyset-paginated batch loop
# ---------------------------------------------------------------------------

def _base_query(db: Session, embedding_type: str, only_missing: bool):
    """Return a SQLAlchemy query for companies to embed."""
    query = db.query(Company).filter(Company.purpose.isnot(None))
    if only_missing:
        # LEFT JOIN to exclude companies that already have this embedding type.
        query = (
            query
            .outerjoin(
                CompanyEmbedding,
                (CompanyEmbedding.company_id == Company.id)
                & (CompanyEmbedding.embedding_type == embedding_type),
            )
            .filter(CompanyEmbedding.id.is_(None))
        )
    return query


def _run_embed_batch(
    db: Session,
    embedding_type: str,
    get_text_fn,  # (company, boilerplate_patterns) -> str | None
    boilerplate_patterns,
    batch_size: int,
    resume_from: int,
    only_missing: bool,
    progress_cb: Any,
) -> dict[str, Any]:
    from app.services.embeddings import embed_texts

    stats: dict[str, Any] = {
        "selected": 0,
        "updated": 0,
        "skipped_no_purpose": 0,
        "errors": [],
    }

    query = _base_query(db, embedding_type, only_missing)
    stats["selected"] = query.count()

    last_id: int = resume_from
    processed = 0

    while True:
        batch = (
            query.filter(Company.id > last_id)
            .order_by(Company.id.asc())
            .limit(batch_size)
            .all()
        )
        if not batch:
            break

        texts: list[str] = []
        valid: list[Company] = []
        for company in batch:
            text = get_text_fn(company, boilerplate_patterns)
            if not text:
                stats["skipped_no_purpose"] += 1
                continue
            texts.append(text)
            valid.append(company)

        if texts:
            try:
                vecs = embed_texts(texts, batch_size=256)
                rows = [
                    (c.id, embedding_type, vecs[i], c.purpose_language)
                    for i, c in enumerate(valid)
                ]
                upsert_embeddings_bulk(db, rows)
                db.commit()
                stats["updated"] += len(rows)
            except Exception as exc:
                logger.warning("embed batch error (%s): %s", embedding_type, exc)
                stats["errors"].append(str(exc))
                db.rollback()

        last_id = batch[-1].id
        processed += len(batch)
        if progress_cb:
            progress_cb(processed, stats["selected"], stats)

    return stats


# ---------------------------------------------------------------------------
# Public batch jobs
# ---------------------------------------------------------------------------

def embed_purpose_full_batch(
    db: Session,
    *,
    batch_size: int = 500,
    resume_from: int = 0,
    only_missing: bool = True,
    progress_cb: Any = None,
) -> dict[str, Any]:
    """Compute purpose_full embeddings for all companies with a purpose."""

    def _get_text(company: Company, _patterns) -> str | None:
        return (company.purpose or "").strip() or None

    return _run_embed_batch(
        db,
        embedding_type="purpose_full",
        get_text_fn=_get_text,
        boilerplate_patterns=None,
        batch_size=batch_size,
        resume_from=resume_from,
        only_missing=only_missing,
        progress_cb=progress_cb,
    )


def embed_purpose_clean_batch(
    db: Session,
    *,
    batch_size: int = 500,
    resume_from: int = 0,
    only_missing: bool = True,
    progress_cb: Any = None,
) -> dict[str, Any]:
    """Compute purpose_clean embeddings using active boilerplate patterns.

    Falls back to raw purpose when stripping yields an empty string.
    """
    boilerplate_patterns = crud.get_active_boilerplate_patterns(db)

    def _get_text(company: Company, patterns) -> str | None:
        raw = (company.purpose or "").strip()
        if not raw:
            return None
        stripped = _strip(raw, patterns)
        return stripped or raw

    return _run_embed_batch(
        db,
        embedding_type="purpose_clean",
        get_text_fn=_get_text,
        boilerplate_patterns=boilerplate_patterns,
        batch_size=batch_size,
        resume_from=resume_from,
        only_missing=only_missing,
        progress_cb=progress_cb,
    )


# ---------------------------------------------------------------------------
# NOGA pipeline integration
# ---------------------------------------------------------------------------

def embed_batch_for_noga(
    db: Session,
    companies: list[Company],
    boilerplate_patterns,
    embed_mode: str = "clean",
) -> int:
    """Compute and upsert purpose embeddings for a NOGA classification batch.

    embed_mode controls which embedding types are stored:
      "clean"         — purpose_clean only (boilerplate-stripped; default)
      "full_and_clean" — purpose_full (raw) + purpose_clean
      "none"          — skip (no-op, returns 0)

    Called at the end of each reclassify_noga batch to share model loading and
    boilerplate stripping with the NOGA run.  Returns the count of rows stored.
    """
    if embed_mode == "none":
        return 0

    from app.services.embeddings import embed_texts

    raw_texts: list[str] = []
    clean_texts: list[str] = []
    valid: list[Company] = []
    for company in companies:
        raw = (company.purpose or "").strip()
        if not raw:
            continue
        stripped = _strip(raw, boilerplate_patterns)
        raw_texts.append(raw)
        clean_texts.append(stripped or raw)
        valid.append(company)

    if not valid:
        return 0

    try:
        rows: list[tuple] = []
        clean_vecs = embed_texts(clean_texts, batch_size=256)
        for i, c in enumerate(valid):
            rows.append((c.id, "purpose_clean", clean_vecs[i], c.purpose_language))
        if embed_mode == "full_and_clean":
            full_vecs = embed_texts(raw_texts, batch_size=256)
            for i, c in enumerate(valid):
                rows.append((c.id, "purpose_full", full_vecs[i], c.purpose_language))
        upsert_embeddings_bulk(db, rows)
        return len(rows)
    except Exception as exc:
        logger.warning("embed_batch_for_noga failed: %s", exc)
        return 0


# ---------------------------------------------------------------------------
# Semantic search
# ---------------------------------------------------------------------------

def search_companies_semantic(
    db: Session,
    query_text: str,
    *,
    embedding_type: str = "purpose_clean",
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Return companies ranked by cosine similarity to query_text.

    Returns a list of dicts: company_id, name, uid, canton, purpose,
    similarity, lang.
    """
    from app.services.embeddings import embed_single

    if not query_text.strip():
        return []

    query_vec = embed_single(query_text.strip())
    if np.linalg.norm(query_vec) < 1e-6:
        return []

    vec_str = _vec_str(query_vec)

    rows = db.execute(sql_text("""
        WITH q AS (SELECT CAST(:vec AS vector) AS v)
        SELECT
            ce.company_id,
            1 - (ce.embedding <=> q.v) AS similarity,
            ce.lang
        FROM company_embeddings ce, q
        WHERE ce.embedding_type = :etype
        ORDER BY ce.embedding <=> q.v
        LIMIT :lim
    """), {"vec": vec_str, "etype": embedding_type, "lim": limit}).fetchall()

    if not rows:
        return []

    id_to_sim = {r.company_id: (float(r.similarity), r.lang) for r in rows}
    company_ids = list(id_to_sim.keys())

    companies = db.query(Company).filter(Company.id.in_(company_ids)).all()
    company_map = {c.id: c for c in companies}

    results = []
    for cid in company_ids:
        c = company_map.get(cid)
        if c is None:
            continue
        sim, lang = id_to_sim[cid]
        results.append({
            "company_id": c.id,
            "name": c.name,
            "uid": c.uid,
            "canton": c.canton,
            "purpose": c.purpose,
            "similarity": round(sim, 4),
            "lang": lang,
        })
    return results
