"""Bulk NOGA industry classification pipeline."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app import crud
from app.models.company import Company
from app.services.noga import apply_noga_classification

logger = logging.getLogger(__name__)

# When True, purpose_clean embeddings are computed alongside NOGA classification,
# sharing model loading and boilerplate patterns at no extra overhead.
_EMBED_ALONGSIDE_NOGA = True


def reclassify_noga(
    db: Session,
    *,
    batch_size: int = 500,
    resume_from: int = 0,
    only_missing_noga: bool = False,
    include_stale: bool = False,
    only_detailed_raw: bool = True,
    progress_cb: Any = None,
) -> dict[str, Any]:
    """Bulk (re)classify companies with NOGA based on local taxonomy data.

    include_stale=True also selects companies whose noga_classified_at is more
    than 5 minutes older than updated_at — meaning the record was updated
    (e.g. by a SHAB mutation) after the last NOGA run.
    """
    stats: dict[str, Any] = {
        "selected": 0,
        "updated": 0,
        "skipped_existing": 0,
        "skipped_not_detailed": 0,
        "skipped_no_match": 0,
        "branches_handled": 0,
        "embeddings_stored": 0,
        "errors": [],
    }

    # Load boilerplate patterns once for the whole run (shared with embedding pipeline).
    boilerplate_patterns = crud.get_active_boilerplate_patterns(db)

    query = db.query(Company)
    if only_missing_noga and not include_stale:
        query = query.filter(Company.noga_code.is_(None))
    elif only_missing_noga and include_stale:
        from sqlalchemy import or_ as _or, and_ as _and, text as _sqlt
        query = query.filter(
            _or(
                Company.noga_code.is_(None),
                _and(
                    Company.noga_classified_at.isnot(None),
                    Company.noga_classified_at < Company.updated_at - _sqlt("interval '5 minutes'"),
                ),
            )
        )
    elif include_stale:
        from sqlalchemy import or_ as _or, and_ as _and, text as _sqlt
        query = query.filter(
            _or(
                Company.noga_code.is_(None),
                _and(
                    Company.noga_classified_at.isnot(None),
                    Company.noga_classified_at < Company.updated_at - _sqlt("interval '5 minutes'"),
                ),
            )
        )

    total = query.count()
    stats["selected"] = total

    last_id: int = resume_from
    processed: int = 0

    while True:
        batch = (
            query.filter(Company.id > last_id)
            .order_by(Company.id.asc())
            .limit(batch_size)
            .all()
        )
        if not batch:
            break

        classified_this_batch: list[Company] = []

        for company in batch:
            try:
                if only_missing_noga and not include_stale and company.noga_code:
                    stats["skipped_existing"] += 1
                    continue

                if only_detailed_raw and not (company.purpose or "").strip():
                    stats["skipped_not_detailed"] += 1
                    continue

                from app.services.noga import is_branch_office
                if is_branch_office(company):
                    stats["branches_handled"] = stats.get("branches_handled", 0) + 1

                update = apply_noga_classification(db, company)
                if update is None:
                    stats["skipped_no_match"] += 1
                    continue
                crud.update_company(db, company, update)
                stats["updated"] += 1
                classified_this_batch.append(company)

            except Exception as exc:  # noqa: BLE001
                logger.warning("NOGA classification failed for %s: %s", company.uid, exc)
                stats["errors"].append(f"{company.uid} [{type(exc).__name__}]: {exc}")

        db.commit()

        # Co-locate purpose_clean embedding computation with NOGA run —
        # shares model loading and boilerplate patterns, no extra overhead.
        if _EMBED_ALONGSIDE_NOGA and classified_this_batch:
            try:
                from app.services.company_embedding_pipeline import embed_batch_for_noga
                n = embed_batch_for_noga(db, classified_this_batch, boilerplate_patterns)
                db.commit()
                stats["embeddings_stored"] = stats.get("embeddings_stored", 0) + n
            except Exception as exc:  # noqa: BLE001
                logger.warning("Embedding alongside NOGA failed: %s", exc)

        last_id = batch[-1].id
        processed += len(batch)

        if progress_cb:
            progress_cb(processed, total, stats)

    return stats


def reclassify_low_confidence_noga(
    db: Session,
    *,
    confidence_threshold: float = 0.80,
    batch_size: int = 500,
    progress_cb: Any = None,
) -> dict[str, Any]:
    """Re-classify companies whose noga_confidence is below the threshold.

    Companies with NULL confidence are also included since they have never been classified.
    """
    stats: dict[str, Any] = {
        "selected": 0,
        "updated": 0,
        "improved": 0,
        "still_low": 0,
        "skipped_no_match": 0,
        "errors": [],
    }

    query = db.query(Company).filter(
        Company.purpose.isnot(None),
        or_(
            Company.noga_confidence.is_(None),
            Company.noga_confidence < confidence_threshold,
        ),
    )

    total = query.count()
    stats["selected"] = total
    offset = 0

    while True:
        batch = query.order_by(Company.id.asc()).offset(offset).limit(batch_size).all()
        if not batch:
            break

        for company in batch:
            try:
                update = apply_noga_classification(db, company)
                if update is None:
                    stats["skipped_no_match"] += 1
                    continue
                crud.update_company(db, company, update)
                stats["updated"] += 1
                new_conf = update.noga_confidence or 0.0
                if new_conf >= confidence_threshold:
                    stats["improved"] += 1
                else:
                    stats["still_low"] += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("NOGA reclassification failed for %s: %s", company.uid, exc)
                stats["errors"].append(f"{company.uid}: {type(exc).__name__}: {exc}")

        db.commit()
        offset += len(batch)

        if progress_cb:
            progress_cb(min(offset, total), total, stats)

    return stats
