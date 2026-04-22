"""SHAB (Schweizerisches Handelsamtsblatt) daily import service.

Fetches HR publications (new registrations, mutations, deletions) from the
SHAB public API and upserts companies into the local DB via the Zefix API.

Rubric legend:
  HR01 — Neueintragung (new company registration)
  HR02 — Mutation       (address, name, signer change, …)
  HR03 — Löschung       (company deleted / dissolved)
"""

from __future__ import annotations

import time
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from app import crud
from app.api.shab_client import (
    SUBR_DELETION,
    SUBR_MUTATION,
    SUBR_NEW,
    fetch_hr_publications,
    fetch_publication_detail,
)
from app.schemas.company import CompanyUpdate
from app.services.collection import enrich_company, import_company_from_zefix_uid

# UIDs queued for detail fetch are collected during import and submitted
# as a single job at the end to avoid enqueuing hundreds of tiny jobs.
_DETAIL_BATCH_SIZE = 500  # max UIDs per detail-fetch job


def yesterday() -> date:
    """Return yesterday's date in UTC."""
    return (datetime.now(tz=timezone.utc) - timedelta(days=1)).date()


def _extract_uid(value: Any) -> str | None:
    """Extract a UID string from SHAB payload values.

    SHAB sometimes returns ``meta.uid`` as a scalar string and sometimes as a
    nested list (for example ``["CHE-123.456.789"]``). This helper normalizes
    those variants to a single string.
    """
    if value is None:
        return None
    if isinstance(value, str):
        uid = value.strip()
        return uid or None
    if isinstance(value, list):
        for item in value:
            extracted = _extract_uid(item)
            if extracted:
                return extracted
        return None
    if isinstance(value, dict):
        for key in ("uid", "value", "text"):
            if key in value:
                extracted = _extract_uid(value.get(key))
                if extracted:
                    return extracted
        return None

    uid = str(value).strip()
    return uid or None


def import_shab_publications(
    db: Session,
    from_date: date,
    to_date: date,
    *,
    request_delay: float = 0.15,
    resume_from: int = 0,
    app=None,
    progress_cb=None,
    status_cb=None,
    abort_cb=None,
) -> dict[str, Any]:
    """Import SHAB HR publications for the given date range into the DB.

    HR01 / HR02 — upserts the company from the Zefix API using the UID
                  extracted from the SHAB publication detail.
    HR03         — marks the company as deleted if it exists in the local DB.

    Supports pause/cancel via abort_cb (raises JobCancelledError /
    JobPausedError) and resume via resume_from (skip the first N publications).

    Returns a stats dict.
    """
    stats: dict[str, Any] = {
        "publications_fetched": 0,
        "created": 0,
        "updated": 0,
        "deleted": 0,
        "skipped": 0,
        "geocoded": 0,
        "keywords": 0,
        "noga_classified": 0,
        "errors": [],
        "warnings": [],
        "detail_jobs_queued": 0,
    }
    # Track newly created UIDs (HR01) so we can enqueue a detail fetch at the end.
    new_uids: list[str] = []

    if status_cb:
        status_cb(f"Fetching SHAB publications {from_date} → {to_date}…")

    publications = fetch_hr_publications(from_date, to_date)
    total = len(publications)
    stats["publications_fetched"] = total

    if status_cb:
        status_cb(f"Found {total} SHAB HR publications — processing…")

    start = max(0, min(resume_from, total))
    for i, pub in enumerate(publications[start:], start=start + 1):
        if abort_cb:
            abort_cb()

        meta: dict = pub.get("meta") or {}
        pub_id: str = meta.get("id") or ""
        sub_rubric: str = meta.get("subRubric") or ""
        title_obj = meta.get("title") or {}
        title: str = title_obj.get("de") or title_obj.get("en") or ""

        if not pub_id:
            stats["skipped"] += 1
            stats["warnings"].append(f"Publication #{i} missing id — skipped")
            if progress_cb:
                progress_cb(i, total, stats)
            continue

        # UID is NOT present in the list response — fetch the detail endpoint.
        uid: str | None = _extract_uid(meta.get("uid"))
        if not uid:
            try:
                detail = fetch_publication_detail(pub_id)
                uid = _extract_uid((detail.get("meta") or {}).get("uid"))
            except Exception as exc:  # noqa: BLE001
                stats["errors"].append(f"[{pub_id[:8]}] detail fetch failed: {exc}")
                if progress_cb:
                    progress_cb(i, total, stats)
                time.sleep(request_delay)
                continue

        if not uid:
            # Some HR entries have no UID (e.g. foreign-branch notices, court orders)
            stats["skipped"] += 1
            stats["warnings"].append(
                f"[{pub_id[:8]}] no UID ({sub_rubric}): {title[:80]}"
            )
            if progress_cb:
                progress_cb(i, total, stats)
            time.sleep(request_delay)
            continue

        # Defensive normalization in case an unexpected payload shape leaks through.
        uid = _extract_uid(uid) or str(uid).strip()

        try:
            if sub_rubric in (SUBR_NEW, SUBR_MUTATION):
                company, created = import_company_from_zefix_uid(
                    db,
                    uid,
                    pause_on_zefix_500=True,
                    status_cb=status_cb,
                    abort_cb=abort_cb,
                )
                if created:
                    stats["created"] += 1
                    new_uids.append(uid)
                else:
                    stats["updated"] += 1

                try:
                    enriched = enrich_company(db, company)
                    stats["geocoded"] += enriched["geocoded"]
                    stats["keywords"] += enriched["keywords"]
                    stats["noga_classified"] += enriched["noga_classified"]
                except Exception:  # noqa: BLE001
                    pass

            elif sub_rubric == SUBR_DELETION:
                existing = crud.get_company_by_uid(db, uid)
                if existing:
                    pub_date_str = (meta.get("publicationDate") or "")[:10] or None
                    crud.update_company(
                        db,
                        existing,
                        CompanyUpdate(
                            status="Gelöscht",
                            deletion_date=pub_date_str,
                        ),
                    )
                    stats["deleted"] += 1
                else:
                    # Company not in our DB — nothing to mark
                    stats["skipped"] += 1

            else:
                stats["skipped"] += 1

        except Exception as exc:  # noqa: BLE001
            if exc.__class__.__name__ in {"JobPausedError", "JobCancelledError"}:
                raise
            # A DB error during one publication leaves the session in failed
            # transaction state until rollback; clear it so the import can continue.
            try:
                db.rollback()
            except Exception:  # noqa: BLE001
                pass
            stats["errors"].append(f"[{uid}] {type(exc).__name__}: {exc}")

        if progress_cb:
            progress_cb(i, total, stats)

        time.sleep(request_delay)

    # Enqueue detail-fetch jobs for newly created companies (HR01).
    # Batch into chunks to keep job labels readable and avoid overly large param blobs.
    if new_uids and app is not None:
        try:
            from app.services.job_worker import enqueue_job, kick_job_worker
            for batch_start in range(0, len(new_uids), _DETAIL_BATCH_SIZE):
                batch = new_uids[batch_start: batch_start + _DETAIL_BATCH_SIZE]
                enqueue_job(
                    app,
                    job_type="detail",
                    label=f"SHAB auto detail fetch — {len(batch)} new UID(s) ({from_date}–{to_date})",
                    params={"uids": batch, "score_if_missing": True},
                    db=db,
                )
                stats["detail_jobs_queued"] += 1
            kick_job_worker(app)
        except Exception as exc:  # noqa: BLE001
            stats["warnings"].append(f"Detail fetch auto-enqueue failed: {exc}")

    return stats
