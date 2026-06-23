"""UID register import pipeline.

Uses a 2-char pair sweep to fetch all entities from the Swiss UID V5.0 PublicServices
SOAP API.

Why 2-char pairs:
  - V5.0 Search uses "contains" semantics (SQL LIKE '%term%'), not prefix/starts-with
  - Single-character search returns 0 results (API minimum length ~2)
  - Every Swiss company name contains at least one 2-char consecutive alpha substring,
    so iterating all 39²=1521 letter pairs (A-Z + ÄÖÜ + accented) guarantees completeness.
    Digits are excluded from the base pairs to avoid expansion bombs on "00"/"20" etc.
  - Max 30 results per call — no pagination. When a pair returns exactly 30, we
    recursively expand to 3-char, 4-char sub-prefixes until all buckets return < 30

Sweep strategy:
  - Iterate all 2-char pairs from _PAIR_CHARS ("AA", "AB", ..., "ÑÑ")
  - For each pair, call Search with organisationName=pair
  - If result count == 30 (cap), expand to 3-char sub-prefixes via iter_entities_by_prefix
  - active_only=False (default): all statuses (active, cancelled, liquidated)
  - active_only=True: filters to ACTIVE status only

For each entity:
  - Existing row (any source): update registration_type + uid_raw only.
  - New row: insert with source='uid'. Address/legal_form will be filled by uid_detail job.

Resume: resume_from maps to the flat pair index (0 = "AA", 1 = "AB", ...) so a
crashed job restarts from the last completed pair, not from scratch.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.crud.company_error import log_error as _log_error
from app.models.company import Company

logger = logging.getLogger(__name__)

_BATCH_SIZE = 500
_INTER_PAGE_DELAY = 1.0  # seconds between pair batches (individual calls already sleep in uid_client)


def _serialize_raw(raw: dict | None) -> str | None:
    if not raw:
        return None
    try:
        return json.dumps(raw, default=str, ensure_ascii=False)
    except Exception:
        return None


def _upsert_batch(db: Session, entities: list[dict[str, Any]]) -> tuple[int, int]:
    """Bulk-upsert a list of entity dicts. Returns (inserted, updated)."""
    if not entities:
        return 0, 0

    # Split into fields we always write (on conflict) vs fields only for new rows
    rows = []
    for e in entities:
        raw_json = _serialize_raw(e.get("_raw"))
        rows.append({
            "uid": e["uid"],
            "name": e.get("name") or e["uid"],
            "status": e.get("status"),
            "legal_form": e.get("legal_form"),
            "legal_form_short_name": e.get("legal_form_short_name"),
            "municipality": e.get("municipality"),
            "canton": e.get("canton"),
            "address": e.get("address"),
            "address_city": e.get("address_city"),
            "address_zip": e.get("address_zip"),
            "registration_type": e.get("registration_type"),
            "first_sogc_date": e.get("first_sogc_date"),
            "deletion_date": e.get("deletion_date"),
            "source": "uid",
            "uid_raw": raw_json,
        })

    # We use a raw SQL upsert to differentiate behaviour for new vs existing rows:
    #   - New rows:      insert all fields
    #   - Existing rows: only update registration_type + uid_raw (preserve enriched data)
    # This ensures Zefix-sourced companies keep their purpose, scores, addresses etc.
    stmt = (
        pg_insert(Company.__table__)
        .values(rows)
        .on_conflict_do_update(
            index_elements=["uid"],
            set_={
                "registration_type": pg_insert(Company.__table__).excluded.registration_type,
                "uid_raw": pg_insert(Company.__table__).excluded.uid_raw,
                # For uid-sourced rows, also refresh basic fields if we're the source:
                "status": text(
                    "CASE WHEN companies.source = 'uid' "
                    "THEN excluded.status ELSE companies.status END"
                ),
                "canton": text(
                    "CASE WHEN companies.canton IS NULL "
                    "THEN excluded.canton ELSE companies.canton END"
                ),
                "municipality": text(
                    "CASE WHEN companies.municipality IS NULL "
                    "THEN excluded.municipality ELSE companies.municipality END"
                ),
            },
        )
    )

    result = db.execute(stmt)
    # rowcount is the number of rows affected (inserted + updated in PostgreSQL)
    affected = result.rowcount or 0
    db.commit()
    return affected, 0


def _count_existing_by_uid(db: Session, uids: list[str]) -> set[str]:
    if not uids:
        return set()
    rows = db.execute(
        text("SELECT uid FROM companies WHERE uid = ANY(:uids)"),
        {"uids": uids},
    ).fetchall()
    return {r[0] for r in rows}


def import_uid_entities(
    db: Session,
    *,
    resume_from: int = 0,
    batch_size: int = _BATCH_SIZE,
    active_only: bool = False,
    progress_cb: Callable[[int, int, dict], None] | None = None,
) -> dict[str, Any]:
    """Run the UID import using a name-prefix sweep.

    active_only=True:  only ACTIVE entities.
    active_only=False: all statuses (active + cancelled + historical).

    resume_from: prefix index into _SWEEP_CHARS. The job worker stores
      progress_done in the DB; we restart from that prefix index so a
      crashed job resumes from the last completed prefix, not from scratch.

    Returns a stats dict compatible with the job handler convention.
    """
    from app.clients.uid_client import _PAIR_CHARS, iter_entities_by_prefix

    # Generate all 2-char pairs upfront for clean resume_from indexing.
    # Letters only (39 chars → 39²=1521 pairs) — digits excluded to avoid
    # recursive expansion bombs on pairs like "00"/"20" that match thousands
    # of company names (years, codes) and cause 10k+ API calls per pair.
    pairs = [c1 + c2 for c1 in _PAIR_CHARS for c2 in _PAIR_CHARS]
    total_pairs = len(pairs)

    stats: dict[str, Any] = {
        "inserted": 0,
        "updated_type": 0,
        "skipped_invalid": 0,
        "api_errors": 0,
        "current_prefix": "",
        "errors": [],
    }

    seen_uids: set[str] = set()

    for pair_idx, prefix in enumerate(pairs):
        # Resume: skip already-completed pairs
        if pair_idx < resume_from:
            continue

        stats["current_prefix"] = prefix

        try:
            entities = iter_entities_by_prefix(
                prefix,
                active_only=active_only,
                seen_uids=seen_uids,
            )
        except Exception as exc:
            logger.error("UID prefix sweep failed: prefix=%r: %s", prefix, exc)
            stats["api_errors"] += 1
            stats["errors"].append(f"[prefix={prefix}] {exc}")
            try:
                _log_error(
                    db,
                    company_id=None,
                    source="uid_import",
                    error_type="api_error_abort",
                    message=f"UID import prefix sweep failed (prefix={prefix!r}): {exc}",
                    detail={"prefix": prefix, "pair_idx": pair_idx, "last_error": str(exc)},
                )
                db.commit()
            except Exception:
                pass
            if stats["api_errors"] > 5:
                logger.error("Too many prefix failures — aborting UID import")
                return stats
            continue

        if not entities:
            if progress_cb:
                progress_cb(pair_idx + 1, total_pairs, stats)
            continue

        # Write in sub-batches of batch_size to bound memory and commit regularly
        for i in range(0, len(entities), batch_size):
            sub = entities[i : i + batch_size]
            valid = [e for e in sub if e.get("uid")]
            stats["skipped_invalid"] += len(sub) - len(valid)
            if not valid:
                continue

            uids_in_batch = [e["uid"] for e in valid]
            existing_uids = _count_existing_by_uid(db, uids_in_batch)
            new_count = sum(1 for e in valid if e["uid"] not in existing_uids)
            upd_count = len(valid) - new_count

            try:
                _upsert_batch(db, valid)
            except Exception as exc:
                logger.error("UID batch upsert failed (prefix=%r): %s", prefix, exc)
                stats["errors"].append(f"[prefix={prefix}] upsert: {exc}")
                try:
                    _log_error(
                        db,
                        company_id=None,
                        source="uid_import",
                        error_type="upsert_error",
                        message=f"Batch upsert failed (prefix={prefix!r}): {exc}",
                        detail={"prefix": prefix, "batch_size": len(valid)},
                    )
                    db.rollback()
                    db.commit()
                except Exception:
                    pass
                continue

            stats["inserted"] += new_count
            stats["updated_type"] += upd_count

        if progress_cb:
            progress_cb(pair_idx + 1, total_pairs, stats)

        time.sleep(_INTER_PAGE_DELAY)

    return stats


# ── Detail fetch (GetByUID) ───────────────────────────────────────────────────

_DETAIL_BATCH_SIZE = 100


def fetch_uid_details(
    db: Session,
    *,
    resume_from: int = 0,
    batch_size: int = _DETAIL_BATCH_SIZE,
    progress_cb: Callable[[int, int, dict], None] | None = None,
) -> dict[str, Any]:
    """Call GetByUID for every source='uid' company that has no address yet.

    Populates address, legal_form, municipality, canton, address_zip from the
    UID detail endpoint. Run this after uid_import to get location data.
    Geocoding can then be triggered separately via the re_geocode job.
    """
    from app.clients.uid_client import get_by_uid, detail_to_update

    stats: dict[str, Any] = {
        "updated": 0,
        "skipped_no_detail": 0,
        "api_errors": 0,
        "errors": [],
    }

    # Count companies needing detail fetch
    total: int = db.execute(
        text(
            "SELECT COUNT(*) FROM companies "
            "WHERE source = 'uid' AND address IS NULL AND id > :resume"
        ),
        {"resume": resume_from},
    ).scalar() or 0

    last_id = resume_from
    processed = 0

    while True:
        rows = db.execute(
            text(
                "SELECT id, uid FROM companies "
                "WHERE source = 'uid' AND address IS NULL AND id > :last_id "
                "ORDER BY id ASC LIMIT :limit"
            ),
            {"last_id": last_id, "limit": batch_size},
        ).fetchall()

        if not rows:
            break

        for row_id, uid_str in rows:
            try:
                entity = get_by_uid(uid_str)
            except Exception as exc:
                stats["api_errors"] += 1
                stats["errors"].append(f"[{uid_str}] GetByUID: {exc}")
                last_id = row_id
                processed += 1
                continue

            if not entity:
                stats["skipped_no_detail"] += 1
                last_id = row_id
                processed += 1
                continue

            update = detail_to_update(entity)
            if update:
                db.execute(
                    text(
                        "UPDATE companies SET "
                        + ", ".join(f"{k} = :{k}" for k in update)
                        + " WHERE id = :id"
                    ),
                    {**update, "id": row_id},
                )
                stats["updated"] += 1

            last_id = row_id
            processed += 1

        db.commit()

        if progress_cb:
            progress_cb(processed, total, stats)

    return stats
