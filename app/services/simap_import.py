"""SIMAP award import service.

Fetches contract award notices from simap.ch (public API, no auth required) and
upserts them into simap_awards + simap_award_vendors.  Swiss vendors are matched
to the companies table by CHE UID via the vendor profile endpoint.

Typical call volume per month: 300–900 projects × ~2 API calls each = 600–1800 requests.
At the default 0.12 s delay the full historical backfill (July 2024–present) takes ~30 min.
"""
from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

import httpx

from sqlalchemy import literal_column
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app import crud
from app.clients.simap_client import (
    get_publication_detail,
    get_vendor_profile,
    search_award_projects,
)
from app.models.simap_award import SimapAward
from app.models.simap_award_vendor import SimapAwardVendor

logger = logging.getLogger(__name__)

# SIMAP launched on the new platform in July 2024 — no award data exists before this.
SIMAP_EARLIEST_DATE = date(2024, 7, 1)


def _t(obj: dict | None, *langs: str) -> str | None:
    """Pick the first non-empty language slot from a SIMAP Translation object."""
    if not obj:
        return None
    for lang in langs:
        v = obj.get(lang)
        if v and isinstance(v, str) and v.strip():
            return v.strip()
    return None


def _extract_award_row(proj: dict, detail: dict) -> dict[str, Any]:
    """Build the simap_awards upsert dict from search + detail payloads."""
    base = detail.get("base") or {}
    decision = detail.get("decision") or {}
    procurement = detail.get("procurement") or {}
    project_info = detail.get("project-info") or {}
    lot = detail.get("lot")

    title = proj.get("title") or {}
    proc_office = proj.get("procOfficeName") or {}
    order_desc = procurement.get("orderDescription") or {}

    # Price range (used when totalPriceSelection == "price_range_of_selected_offers")
    price_range = decision.get("totalPriceRange") or {}
    price_range_inner = price_range.get("range") or {}

    # CPV code
    cpv = procurement.get("cpvCode") or {}
    cpv_code = cpv.get("code") if isinstance(cpv, dict) else None

    lot_title = lot.get("title") if lot else None

    return {
        "simap_project_id": proj["id"],
        "simap_publication_id": proj["publicationId"],
        "project_number": proj.get("projectNumber"),
        "publication_number": proj.get("publicationNumber"),
        "publication_date": date.fromisoformat(proj["publicationDate"]),
        "award_decision_date": (
            date.fromisoformat(decision["awardDecisionDate"])
            if decision.get("awardDecisionDate")
            else None
        ),
        "pub_type": base.get("type") or proj.get("pubType"),
        "process_type": base.get("processType") or proj.get("processType"),
        "project_type": base.get("projectType") or proj.get("projectType"),
        "project_subtype": proj.get("projectSubType"),
        "creation_language": base.get("creationLanguage"),
        "title_de": title.get("de"),
        "title_fr": title.get("fr"),
        "title_it": title.get("it"),
        "proc_office_name_de": proc_office.get("de"),
        "proc_office_name_fr": proc_office.get("fr"),
        "proc_office_name_it": proc_office.get("it"),
        "order_description_de": order_desc.get("de"),
        "order_description_fr": order_desc.get("fr"),
        "order_description_it": order_desc.get("it"),
        "cpv_code": cpv_code,
        "number_of_submissions": decision.get("numberOfSubmissions"),
        "total_price_selection": decision.get("totalPriceSelection"),
        "total_price_range_min": price_range_inner.get("from"),
        "total_price_range_max": price_range_inner.get("to"),
        "total_price_currency": price_range.get("currency"),
        "lot_number": lot.get("lotNumber") if lot else None,
        "lot_title_de": (lot_title or {}).get("de") if lot_title else None,
        "lot_title_fr": (lot_title or {}).get("fr") if lot_title else None,
        "lot_title_it": (lot_title or {}).get("it") if lot_title else None,
    }


def _upsert_award(db: Session, row: dict[str, Any]) -> tuple[SimapAward, bool]:
    """Insert or update a simap_award row. Returns (award, created)."""
    update_cols = {k: v for k, v in row.items() if k != "simap_project_id"}
    stmt = (
        pg_insert(SimapAward)
        .values(**row)
        .on_conflict_do_update(
            index_elements=["simap_project_id"],
            set_=update_cols,
        )
        .returning(SimapAward.id, literal_column("(xmax = 0)").label("inserted"))
    )
    result = db.execute(stmt)
    award_id, created = result.one()
    db.expire_all()
    award = db.get(SimapAward, award_id)
    return award, bool(created)


def _upsert_vendor(
    db: Session,
    award: SimapAward,
    vendor_data: dict[str, Any],
    vendor_profile: dict[str, Any],
    company_id: int | None,
) -> bool:
    """Insert or update a simap_award_vendor row. Returns True if newly created."""
    simap_vendor_id = vendor_data["vendorId"]
    addr = vendor_data.get("vendorAddress") or {}
    price_obj = vendor_data.get("price") or {}
    price_val = price_obj.get("price")
    currency = price_obj.get("currency")

    fields = {
        "award_id": award.id,
        "company_id": company_id,
        "simap_vendor_id": simap_vendor_id,
        "vendor_name": vendor_data.get("vendorName", ""),
        "vendor_uid": vendor_profile.get("uidNo"),
        "vendor_country": addr.get("countryId"),
        "vendor_city": addr.get("city"),
        "vendor_postal_code": addr.get("postalCode"),
        "price": price_val,
        "price_currency": currency,
        "rank": vendor_data.get("rank"),
    }
    update_cols = {k: v for k, v in fields.items() if k not in ("award_id", "simap_vendor_id")}
    stmt = (
        pg_insert(SimapAwardVendor)
        .values(**fields)
        .on_conflict_do_update(
            constraint="uq_simap_award_vendor",
            set_=update_cols,
        )
        .returning(literal_column("(xmax = 0)").label("inserted"))
    )
    created = bool(db.execute(stmt).scalar())
    return created


def import_simap_awards(
    db: Session,
    from_date: date,
    to_date: date,
    *,
    request_delay: float = 0.12,
    resume_cursor: str | None = None,
    app: Any = None,
    progress_cb: Callable[[int, int, dict], None] | None = None,
    status_cb: Callable[[str], None] | None = None,
    abort_cb: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Import SIMAP award publications for a date range.

    Paginates through all award projects in the given range, fetches each
    publication detail, resolves vendor CHE UIDs, and upserts into the DB.

    Args:
        db: SQLAlchemy session.
        from_date: Start of the date range (inclusive).
        to_date: End of the date range (inclusive).
        request_delay: Seconds to sleep between SIMAP API calls.
        resume_cursor: ``lastItem`` pagination cursor from a previous interrupted run.
        app: FastAPI app instance (for job worker kick).
        progress_cb: Called after each project with (done, total_estimate, stats).
        status_cb: Called with a status message string.
        abort_cb: Called at each page boundary — raises JobCancelledError/JobPausedError.

    Returns:
        Stats dict with keys: created, updated, skipped, vendor_matched,
        vendor_foreign, vendor_no_uid, errors, last_cursor.
    """
    stats: dict[str, Any] = {
        "created": 0,
        "updated": 0,
        "skipped": 0,
        "vendor_matched": 0,
        "vendor_foreign": 0,
        "vendor_no_uid": 0,
        "errors": [],
        "last_cursor": resume_cursor,
    }

    from_date = max(from_date, SIMAP_EARLIEST_DATE)
    if from_date > to_date:
        return stats

    cursor = resume_cursor
    done = 0
    total_estimate = 0  # updated as we learn the actual volume

    if status_cb:
        status_cb(f"Fetching SIMAP awards {from_date} → {to_date} …")

    while True:
        if abort_cb:
            abort_cb()

        # ── Fetch one page of search results ─────────────────────────────────
        try:
            page = search_award_projects(from_date, to_date, last_item=cursor)
        except httpx.HTTPError as exc:
            msg = f"Search page error (cursor={cursor}): {exc}"
            logger.warning(msg)
            stats["errors"].append(msg)
            break

        projects = page.get("projects", [])
        pagination = page.get("pagination", {})
        next_cursor = pagination.get("lastItem")

        if not projects:
            break

        total_estimate += len(projects)

        # ── Process each project on this page ────────────────────────────────
        for proj in projects:
            proj_id = proj["id"]
            pub_id = proj["publicationId"]

            try:
                time.sleep(request_delay)
                detail = get_publication_detail(proj_id, pub_id)
            except httpx.HTTPError as exc:
                msg = f"Detail fetch failed for project {proj_id}: {exc}"
                logger.warning(msg)
                stats["errors"].append(msg)
                stats["skipped"] += 1
                continue

            # Build and upsert the award row
            try:
                award_row = _extract_award_row(proj, detail)
            except Exception as exc:
                msg = f"Parse error for project {proj_id}: {exc}"
                logger.warning(msg)
                stats["errors"].append(msg)
                stats["skipped"] += 1
                continue

            award, created = _upsert_award(db, award_row)
            if created:
                stats["created"] += 1
            else:
                stats["updated"] += 1

            # ── Resolve vendors ───────────────────────────────────────────────
            decision = detail.get("decision") or {}
            vendors = decision.get("vendors") or []

            for v in vendors:
                vendor_id = v.get("vendorId")
                if not vendor_id:
                    stats["vendor_no_uid"] += 1
                    continue

                try:
                    time.sleep(request_delay)
                    profile = get_vendor_profile(vendor_id)
                except httpx.HTTPError as exc:
                    logger.warning("Vendor profile fetch failed %s: %s", vendor_id, exc)
                    profile = {}

                che_uid = profile.get("uidNo")
                vendor_country = (v.get("vendorAddress") or {}).get("countryId", "")

                company_id: int | None = None
                if che_uid:
                    company = crud.get_company_by_uid(db, che_uid)
                    if company:
                        company_id = company.id
                        stats["vendor_matched"] += 1
                    else:
                        stats["vendor_no_uid"] += 1
                elif vendor_country and vendor_country != "CH":
                    stats["vendor_foreign"] += 1
                else:
                    stats["vendor_no_uid"] += 1

                _upsert_vendor(db, award, v, profile, company_id)

            db.commit()

            done += 1
            stats["last_cursor"] = next_cursor

            if progress_cb:
                progress_cb(done, max(done, total_estimate), dict(stats))

        # ── Advance cursor ────────────────────────────────────────────────────
        if not next_cursor or next_cursor == cursor:
            break
        cursor = next_cursor

        if status_cb:
            status_cb(
                f"Page done — {done} projects processed, "
                f"{stats['created']} new, {stats['updated']} updated"
            )

    return stats
