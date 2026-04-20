"""Async CSV export job logic.

Writes ALL companies matching the given filters (no row cap) to a temporary
file, uploads it to S3, and returns a stats dict that is persisted in the
JobRun.stats_json column:

  {
    "s3_key": "exports/42/export.csv",
    "expires_at": "2026-04-08T12:00:00+00:00",   # ISO-8601 UTC
    "row_count": 123456
  }

The file lives for 7 days (TTL enforced only via the presigned-URL expiry;
no automatic S3 lifecycle rule is required for correctness).
"""
from __future__ import annotations

import csv
import io
import logging
import os
import tempfile
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy.orm import Session

from app import crud
from app.crud.company import list_companies, _apply_filters
from app.models.company import Company
from app.models.org_company_state import OrgCompanyState
from app.services.s3_client import delete_object, export_s3_key, upload_file

logger = logging.getLogger(__name__)

_HEADERS = [
    "uid", "name", "legal_form", "status", "municipality", "canton",
    "website_url", "web_score", "flex_score", "ai_score", "combined_score",
    "review_status", "contact_status", "contact_name", "contact_email", "contact_phone",
    "tags", "ai_category", "tfidf_cluster", "purpose_keywords",
    "noga_code", "noga_label", "noga_level", "noga_confidence",
    "created_at", "updated_at",
]

_BATCH = 1000  # rows per DB page (avoids loading entire table in memory, reduced for large exports)


def _row(c: Any) -> list:
    return [
        c.uid, c.name, c.legal_form or "", c.status or "",
        c.municipality or "", c.canton or "",
        c.website_url or "",
        c.web_score if c.web_score is not None else "",
        c.flex_score if c.flex_score is not None else "",
        c.ai_score if c.ai_score is not None else "",
        getattr(c, "combined_score", None) or "",
        c.review_status or "", c.contact_status or "",
        c.contact_name or "", c.contact_email or "", c.contact_phone or "",
        c.tags or "", c.ai_category or "", c.tfidf_cluster or "",
        c.purpose_keywords or "",
        c.noga_code or "", c.noga_label or "", c.noga_level or "",
        c.noga_confidence if c.noga_confidence is not None else "",
        c.created_at.isoformat() if c.created_at else "",
        c.updated_at.isoformat() if c.updated_at else "",
    ]


def _bulk_org_states(db: Session, company_ids: list[int], org_id: int) -> dict[int, OrgCompanyState]:
    if not company_ids:
        return {}
    rows = (
        db.query(OrgCompanyState)
        .filter(OrgCompanyState.org_id == org_id, OrgCompanyState.company_id.in_(company_ids))
        .all()
    )
    return {r.company_id: r for r in rows}


def _overlay_org(company: Company, state: OrgCompanyState | None) -> Any:
    """Apply org workflow overlay in-place (returns same company object mutated)."""
    if state is None:
        return company
    for field in ("review_status", "contact_status", "contact_name",
                  "contact_email", "contact_phone", "tags"):
        val = getattr(state, field, None)
        if val is not None:
            setattr(company, field, val)
    return company


def run_csv_export(
    db: Session,
    *,
    params: dict,
    user_id: int,
    org_id: int | None,
    progress_cb: Callable[[int, int, dict], None] | None = None,
) -> dict:
    """Execute the export: write CSV to temp file, upload to S3, return stats dict.

    Honours the ``row_limit`` param injected at enqueue time from the org's tier limit.
    None means unlimited (superadmin only).
    """
    # Build filter kwargs from job params
    row_limit: int | None = params.get("row_limit")  # None = unlimited (superadmin)
    filter_kwargs: dict[str, Any] = {k: v for k, v in params.items()
                                     if k not in ("user_id", "org_id", "row_limit")}

    # First pass: count total rows for progress reporting
    from sqlalchemy import func as sqlfunc
    count_q = _apply_filters(
        db.query(sqlfunc.count(Company.id)),
        name_filter=filter_kwargs.get("q"),
        uid_filter=filter_kwargs.get("uid"),
        canton=filter_kwargs.get("canton"),
        review_status=filter_kwargs.get("review_status"),
        contact_status=filter_kwargs.get("contact_status"),
        google_searched=filter_kwargs.get("google_searched"),
        min_web_score=filter_kwargs.get("min_web_score"),
        min_flex_score=filter_kwargs.get("min_flex_score"),
        min_ai_score=filter_kwargs.get("min_ai_score"),
        tags=filter_kwargs.get("tags"),
        tfidf_cluster=filter_kwargs.get("tfidf_cluster"),
        purpose_keywords=filter_kwargs.get("purpose_keywords"),
        noga_code=filter_kwargs.get("noga_code"),
        noga_label=filter_kwargs.get("noga_label"),
        noga_level=filter_kwargs.get("noga_level"),
        exclude_tags=filter_kwargs.get("exclude_tags"),
        exclude_review_status=filter_kwargs.get("exclude_review_status"),
        exclude_canton=filter_kwargs.get("exclude_canton"),
        exclude_contact_status=filter_kwargs.get("exclude_contact_status"),
        exclude_noga_code=filter_kwargs.get("exclude_noga_code"),
        exclude_noga_label=filter_kwargs.get("exclude_noga_label"),
        exclude_noga_level=filter_kwargs.get("exclude_noga_level"),
        business_model=filter_kwargs.get("business_model"),
    )
    total_matching: int = count_q.scalar() or 0
    # Cap the total reported to the tier limit so the progress bar is accurate.
    total = min(total_matching, row_limit) if row_limit is not None else total_matching

    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, encoding="utf-8", newline="") as fh:
        tmp_path = fh.name
        writer = csv.writer(fh)
        writer.writerow(_HEADERS)

        written = 0
        page = 1
        while True:
            # Always use _BATCH as page_size so (page-1)*page_size gives the correct
            # DB offset. Stopping at row_limit is done by breaking after writing,
            # not by shrinking the query page — otherwise the offset calculation breaks.
            companies = list_companies(
                db,
                page=page,
                page_size=_BATCH,
                sort=filter_kwargs.get("sort", "-updated"),
                name_filter=filter_kwargs.get("q"),
                uid_filter=filter_kwargs.get("uid"),
                canton=filter_kwargs.get("canton"),
                review_status=filter_kwargs.get("review_status"),
                contact_status=filter_kwargs.get("contact_status"),
                google_searched=filter_kwargs.get("google_searched"),
                min_web_score=filter_kwargs.get("min_web_score"),
                min_flex_score=filter_kwargs.get("min_flex_score"),
                min_ai_score=filter_kwargs.get("min_ai_score"),
                tags=filter_kwargs.get("tags"),
                tfidf_cluster=filter_kwargs.get("tfidf_cluster"),
                purpose_keywords=filter_kwargs.get("purpose_keywords"),
                noga_code=filter_kwargs.get("noga_code"),
                noga_label=filter_kwargs.get("noga_label"),
                noga_level=filter_kwargs.get("noga_level"),
                exclude_tags=filter_kwargs.get("exclude_tags"),
                exclude_review_status=filter_kwargs.get("exclude_review_status"),
                exclude_canton=filter_kwargs.get("exclude_canton"),
                exclude_contact_status=filter_kwargs.get("exclude_contact_status"),
                exclude_noga_code=filter_kwargs.get("exclude_noga_code"),
                exclude_noga_label=filter_kwargs.get("exclude_noga_label"),
                exclude_noga_level=filter_kwargs.get("exclude_noga_level"),
                business_model=filter_kwargs.get("business_model"),
            )
            if not companies:
                break

            if org_id:
                ids = [c.id for c in companies]
                org_states = _bulk_org_states(db, ids, org_id)
                companies = [_overlay_org(c, org_states.get(c.id)) for c in companies]

            for c in companies:
                if row_limit is not None and written >= row_limit:
                    break
                writer.writerow(_row(c))
                written += 1

            fh.flush()
            if progress_cb:
                progress_cb(written, total, {"written": written})

            # Stop if we hit the tier cap or ran out of rows.
            if row_limit is not None and written >= row_limit:
                break
            if len(companies) < _BATCH:
                break
            page += 1

    try:
        s3_key = export_s3_key(user_id)
        # Remove old file (ignore errors — same key will be overwritten anyway)
        try:
            delete_object(s3_key)
        except Exception:  # noqa: BLE001
            pass
        upload_file(tmp_path, s3_key)
        expires_at = (datetime.now(tz=timezone.utc) + timedelta(days=7)).isoformat()
        capped = row_limit is not None and total_matching > row_limit
        _upgrade_to: str | None = None
        if capped and row_limit is not None:
            from app.services.tiers import EXPORT_LIMITS as _EL
            for _t in ("simple", "explorer", "researcher", "strategist"):
                if _EL.get(_t, 0) > row_limit:
                    _upgrade_to = _t
                    break
        return {
            "s3_key": s3_key,
            "expires_at": expires_at,
            "row_count": written,
            "capped": capped,
            "tier_limit": row_limit,
            "total_matching": total_matching,
            "upgrade_to": _upgrade_to,
        }
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
