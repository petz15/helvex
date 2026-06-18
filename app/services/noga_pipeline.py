"""Bulk NOGA industry classification pipeline."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import func, or_, text
from sqlalchemy.orm import Session

from app import crud
from app.models.company import Company
from app.services.noga import apply_noga_classification

logger = logging.getLogger(__name__)

def reclassify_noga(
    db: Session,
    *,
    batch_size: int = 500,
    resume_from: int = 0,
    only_missing_noga: bool = False,
    include_stale: bool = False,
    only_detailed_raw: bool = True,
    embed_mode: str = "clean",
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

    if include_stale:
        # The stale filter ORs an IS NULL check with a cross-column comparison
        # (noga_classified_at < updated_at - interval), which can't be served
        # by a btree index — it forces a full table scan no matter how high
        # statement_timeout is set (already tried bumping to 120s; still timed
        # out under load on the 700k-row table). For progress-display purposes
        # an exact count isn't needed, so use the planner's cheap row estimate
        # instead of scanning the table.
        total = db.execute(text("SELECT reltuples::bigint FROM pg_class WHERE relname = 'companies'")).scalar() or 0
    else:
        # noga_code IS NULL alone is served by ix_companies_no_noga_code.
        total = query.with_entities(func.count(Company.id)).scalar() or 0
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

        # Collect (company_id, vec, lang) for purpose_clean upsert — reuses the
        # vector already computed by the NOGA classifier, no re-embedding needed.
        embedding_rows: list[tuple[int, str, object, str | None]] = []

        for company in batch:
            try:
                from app.services.noga import is_branch_office
                is_branch = is_branch_office(company)

                # Branch offices always get re-processed regardless of only_missing_noga:
                # they inherit NOGA from their parent rather than running the classifier,
                # so even a company with an existing (potentially wrong) code must be re-run
                # to inherit from the parent once the parent's NOGA is available.
                if only_missing_noga and not include_stale and company.noga_code and not is_branch:
                    stats["skipped_existing"] += 1
                    continue

                if only_detailed_raw and not (company.purpose or "").strip() and not is_branch:
                    stats["skipped_not_detailed"] += 1
                    continue

                if is_branch:
                    stats["branches_handled"] = stats.get("branches_handled", 0) + 1

                vec_out: list = []
                update = apply_noga_classification(db, company, _vec_out=vec_out)
                if update is None:
                    stats["skipped_no_match"] += 1
                    continue
                crud.update_company(db, company, update)
                stats["updated"] += 1

                if vec_out:
                    embedding_rows.append((company.id, "purpose_clean", vec_out[0], company.purpose_language))

            except Exception as exc:  # noqa: BLE001
                logger.warning("NOGA classification failed for %s: %s", company.uid, exc)
                stats["errors"].append(f"{company.uid} [{type(exc).__name__}]: {exc}")

        db.commit()

        # Store captured vectors — no re-embedding, zero extra model cost.
        if embedding_rows:
            try:
                from app.services.company_embedding_pipeline import upsert_embeddings_bulk
                upsert_embeddings_bulk(db, embedding_rows)
                db.commit()
                stats["embeddings_stored"] = stats.get("embeddings_stored", 0) + len(embedding_rows)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Embedding upsert alongside NOGA failed: %s", exc)

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

    db.execute(text("SET LOCAL statement_timeout = '120000'"))
    total = query.with_entities(func.count(Company.id)).scalar() or 0
    stats["selected"] = total
    offset = 0

    while True:
        batch = query.order_by(Company.id.asc()).offset(offset).limit(batch_size).all()
        if not batch:
            break

        embedding_rows: list[tuple[int, str, object, str | None]] = []

        for company in batch:
            try:
                vec_out: list = []
                update = apply_noga_classification(db, company, _vec_out=vec_out)
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

                if vec_out:
                    embedding_rows.append((company.id, "purpose_clean", vec_out[0], company.purpose_language))

            except Exception as exc:  # noqa: BLE001
                logger.warning("NOGA reclassification failed for %s: %s", company.uid, exc)
                stats["errors"].append(f"{company.uid}: {type(exc).__name__}: {exc}")

        db.commit()

        if embedding_rows:
            try:
                from app.services.company_embedding_pipeline import upsert_embeddings_bulk
                upsert_embeddings_bulk(db, embedding_rows)
                db.commit()
                stats["embeddings_stored"] = stats.get("embeddings_stored", 0) + len(embedding_rows)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Embedding upsert alongside NOGA failed: %s", exc)

        offset += len(batch)

        if progress_cb:
            progress_cb(min(offset, total), total, stats)

    return stats
