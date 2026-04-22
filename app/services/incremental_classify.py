"""Incremental classification for newly imported companies.

When new companies arrive via Zefix bulk import, they need:
  1. NOGA classification       (embedding + token hybrid, same as full pipeline)
  2. Cluster + keyword assignment (embedding-based soft assignment to existing clusters)
  3. Business model detection  (rule-based, instant)

This module is called from the collection pipeline after each import batch.
It is designed to be fast (<1s per company on CPU) so it can run inline.

For large backfills (>1000 new companies), the caller should enqueue a background
job rather than calling classify_new_companies_inline().
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Batch size for DB writes
_WRITE_BATCH = 200


def classify_new_companies_inline(
    db: Session,
    company_ids: list[int],
    *,
    run_noga: bool = True,
    run_clusters: bool = True,
    run_business_model: bool = True,
) -> dict[str, Any]:
    """Classify a batch of company IDs that were just imported.

    Runs NOGA, cluster/keyword assignment, and business-model detection.
    All three are independent and can be toggled via flags.

    Returns a summary dict with counts per step.
    """
    from app.models.company import Company
    from app.schemas.company import CompanyUpdate

    stats: dict[str, Any] = {
        "total": len(company_ids),
        "noga_classified": 0,
        "noga_skipped": 0,
        "cluster_assigned": 0,
        "cluster_skipped": 0,
        "business_model_set": 0,
        "errors": [],
    }

    if not company_ids:
        return stats

    companies = (
        db.query(Company)
        .filter(Company.id.in_(company_ids))
        .all()
    )

    # ── Step 1: NOGA classification ───────────────────────────────────────────
    if run_noga:
        _run_noga_batch(db, companies, stats)

    # ── Step 2: Cluster + keyword assignment ─────────────────────────────────
    if run_clusters:
        _run_cluster_batch(db, company_ids, stats)

    # ── Step 3: Business model ────────────────────────────────────────────────
    if run_business_model:
        _run_business_model_batch(db, companies, stats)

    return stats


def _run_noga_batch(db: Session, companies: list, stats: dict) -> None:
    from app.services.noga import apply_noga_classification
    from app import crud

    mappings = []
    for company in companies:
        if not company.purpose and not company.name:
            stats["noga_skipped"] += 1
            continue
        try:
            update = apply_noga_classification(db, company)
            if update:
                mappings.append({"id": company.id, **update.model_dump(exclude_unset=True)})
                stats["noga_classified"] += 1
            else:
                stats["noga_skipped"] += 1
        except Exception as exc:  # noqa: BLE001
            logger.debug("NOGA classification failed for %s: %s", company.uid, exc)
            stats["noga_skipped"] += 1
            stats["errors"].append(f"noga:{company.uid}:{exc}")

        if len(mappings) >= _WRITE_BATCH:
            from app.models.company import Company as CompanyModel
            db.bulk_update_mappings(CompanyModel, mappings)
            db.commit()
            mappings.clear()

    if mappings:
        from app.models.company import Company as CompanyModel
        db.bulk_update_mappings(CompanyModel, mappings)
        db.commit()


def _run_cluster_batch(db: Session, company_ids: list[int], stats: dict) -> None:
    from app.services.cluster_pipeline import assign_new_companies_to_clusters

    try:
        result = assign_new_companies_to_clusters(db, company_ids)
        stats["cluster_assigned"] += result.get("assigned", 0)
        stats["cluster_skipped"] += result.get("skipped", 0) + result.get("undefined", 0)
        if result.get("missing_artifacts"):
            logger.info(
                "Cluster assignment skipped for %d new companies: no pipeline artifacts yet. "
                "Run a full clustering job to enable incremental assignment.",
                len(company_ids),
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Cluster assignment failed for new companies: %s", exc)
        stats["cluster_skipped"] += len(company_ids)
        stats["errors"].append(f"clusters:{exc}")


# ── Business model detection ──────────────────────────────────────────────────
# Rule-based heuristic over company name + purpose text.
# Good enough for a first pass; AI scoring provides the refinement layer.

_B2B_SIGNALS = frozenset([
    "b2b", "unternehmensberatung", "dienstleistungen für unternehmen",
    "firmen", "geschäftskunden", "geschäftsreisen", "gewerbe",
    "services aux entreprises", "conseil aux entreprises",
    "enterprise", "business solutions", "corporate",
])
_B2C_SIGNALS = frozenset([
    "b2c", "einzelhandel", "detailhandel", "konsumenten",
    "retail", "privatkunden", "endkunden", "haushalte",
    "vente au détail", "particuliers",
    "consumers", "residential",
])
_B2G_SIGNALS = frozenset([
    "b2g", "öffentliche hand", "bund", "kanton", "gemeinde",
    "eidgenossenschaft", "bundesbehörde", "verwaltung",
    "secteur public", "administrations publiques",
    "government", "public sector",
])


def detect_business_model(name: str | None, purpose: str | None) -> str | None:
    """Return 'b2b' | 'b2c' | 'b2g' | 'mixed' | None based on text signals."""
    text = " ".join(filter(None, [name, purpose])).lower()
    if not text:
        return None

    b2b = any(s in text for s in _B2B_SIGNALS)
    b2c = any(s in text for s in _B2C_SIGNALS)
    b2g = any(s in text for s in _B2G_SIGNALS)

    matches = sum([b2b, b2c, b2g])
    if matches == 0:
        return None
    if matches > 1:
        return "mixed"
    if b2b:
        return "b2b"
    if b2c:
        return "b2c"
    return "b2g"


def _run_business_model_batch(db: Session, companies: list, stats: dict) -> None:
    from app.models.company import Company as CompanyModel

    mappings = []
    for company in companies:
        if company.business_model is not None:
            continue
        model = detect_business_model(company.name, company.purpose)
        if model:
            mappings.append({"id": company.id, "business_model": model})
            stats["business_model_set"] += 1

        if len(mappings) >= _WRITE_BATCH:
            db.bulk_update_mappings(CompanyModel, mappings)
            db.commit()
            mappings.clear()

    if mappings:
        db.bulk_update_mappings(CompanyModel, mappings)
        db.commit()


# ── Backfill: classify all unclassified companies ─────────────────────────────

def backfill_unclassified(
    db: Session,
    *,
    batch_size: int = 500,
    run_noga: bool = True,
    run_clusters: bool = True,
    run_business_model: bool = True,
    limit: int | None = None,
    progress_cb=None,
) -> dict[str, Any]:
    """Classify all companies that are missing NOGA, clusters, or business_model.

    Used for one-time backfill after upgrading the classification pipeline.
    """
    from sqlalchemy import or_
    from app.models.company import Company

    filters = []
    if run_noga:
        filters.append(Company.noga_code.is_(None))
    if run_clusters:
        filters.append(Company.tfidf_cluster.is_(None))
    if run_business_model:
        filters.append(Company.business_model.is_(None))

    if not filters:
        return {"total": 0}

    q = (
        db.query(Company.id)
        .filter(Company.purpose.isnot(None))
        .filter(or_(*filters))
        .order_by(Company.id.asc())
    )
    if limit:
        q = q.limit(limit)

    all_ids = [row.id for row in q.all()]
    total = len(all_ids)
    combined: dict[str, Any] = {"total": total, "batches": 0}

    for i in range(0, total, batch_size):
        batch = all_ids[i: i + batch_size]
        result = classify_new_companies_inline(
            db,
            batch,
            run_noga=run_noga,
            run_clusters=run_clusters,
            run_business_model=run_business_model,
        )
        for k, v in result.items():
            if k == "total":
                continue
            if isinstance(v, int):
                combined[k] = combined.get(k, 0) + v
            elif isinstance(v, list):
                combined.setdefault(k, []).extend(v)
        combined["batches"] = combined.get("batches", 0) + 1

        if progress_cb:
            progress_cb(min(i + batch_size, total), total, combined)

    return combined
