"""Incremental classification for newly imported companies.

When new companies arrive via Zefix bulk import, they need:
  1. NOGA classification       (embedding + token hybrid, same as full pipeline)
  2. Cluster + keyword assignment (embedding-based soft assignment to existing clusters)
  3. Language detection         (fast, offline — detects DE/FR/IT/EN/RM from purpose text)

This module is called from the collection pipeline after each import batch.
It is designed to be fast (<1s per company on CPU) so it can run inline.

For large backfills (>1000 new companies), the caller should enqueue a background
job rather than calling classify_new_companies_inline().
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_WRITE_BATCH = 200


def classify_new_companies_inline(
    db: Session,
    company_ids: list[int],
    *,
    run_noga: bool = True,
    run_clusters: bool = True,
    run_language: bool = True,
) -> dict[str, Any]:
    """Classify a batch of company IDs that were just imported.

    Runs NOGA, cluster/keyword assignment, and language detection.
    All three are independent and can be toggled via flags.

    Returns a summary dict with counts per step.
    """
    from app.models.company import Company

    stats: dict[str, Any] = {
        "total": len(company_ids),
        "noga_classified": 0,
        "noga_skipped": 0,
        "cluster_assigned": 0,
        "cluster_skipped": 0,
        "language_detected": 0,
        "errors": [],
    }

    if not company_ids:
        return stats

    companies = (
        db.query(Company)
        .filter(Company.id.in_(company_ids))
        .all()
    )

    if run_noga:
        _run_noga_batch(db, companies, stats)

    if run_clusters:
        _run_cluster_batch(db, company_ids, stats)

    if run_language:
        _run_language_batch(db, companies, stats)

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


def _detect_language(text: str) -> str | None:
    from app.services.language_detection import detect_purpose_language
    return detect_purpose_language(text)


def _run_language_batch(db: Session, companies: list, stats: dict) -> None:
    from app.models.company import Company as CompanyModel

    mappings = []
    for company in companies:
        if company.purpose_language is not None:
            continue
        lang = _detect_language(company.purpose or "")
        if lang:
            mappings.append({"id": company.id, "purpose_language": lang})
            stats["language_detected"] += 1

        if len(mappings) >= _WRITE_BATCH:
            db.bulk_update_mappings(CompanyModel, mappings)
            db.commit()
            mappings.clear()

    if mappings:
        db.bulk_update_mappings(CompanyModel, mappings)
        db.commit()


def backfill_unclassified(
    db: Session,
    *,
    batch_size: int = 500,
    run_noga: bool = True,
    run_clusters: bool = True,
    run_language: bool = True,
    limit: int | None = None,
    progress_cb=None,
) -> dict[str, Any]:
    """Classify all companies that are missing NOGA, clusters, or language.

    Used for one-time backfill after upgrading the classification pipeline.
    """
    from sqlalchemy import or_
    from app.models.company import Company

    filters = []
    if run_noga:
        filters.append(Company.noga_code.is_(None))
    if run_clusters:
        filters.append(Company.tfidf_cluster.is_(None))
    if run_language:
        filters.append(Company.purpose_language.is_(None))

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
            run_language=run_language,
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
