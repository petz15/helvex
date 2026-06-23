"""SIMAP pre-2024 archive import service.

Fetches OB02 (Zuschlag/award) notices from archiv.simap.ch (2007–2023),
de-duplicates by project, and upserts into simap_awards + simap_award_vendors.

Company matching
----------------
The archive has no CHE UIDs.  Vendors are matched to our companies table by:
  1. Exact zip code (address_zip)
  2. pg_trgm string similarity on company name (threshold 0.50)

De-duplication
--------------
The archive has one publication per language per project (DE/FR/IT/…).  We
normalise to one row per project_id by preferring DE > FR > IT > others.

ID namespacing
--------------
Archive integer IDs are prefixed with "arch-" to prevent collisions with the
UUID-based IDs from the current simap.ch API:
  simap_project_id     → "arch-{projectid}"
  simap_publication_id → "arch-{id}"
  simap_vendor_id      → "arch-{id}"   (synthetic; no real vendor UUID exists)
"""
from __future__ import annotations

import logging
import time
from datetime import date
from typing import Any, Callable

import httpx
from sqlalchemy import literal_column, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.simap_award import SimapAward
from app.models.simap_award_vendor import SimapAwardVendor
from app.clients.simap_archive_client import search_archive_awards, get_archive_detail

logger = logging.getLogger(__name__)

# Archive covers 2007–2023; post-2024 data comes from the new simap.ch API.
ARCHIVE_EARLIEST_DATE = date(2007, 1, 1)
ARCHIVE_LATEST_DATE = date(2023, 12, 31)

_LANG_PREF = {"DE": 0, "FR": 1, "IT": 2}


def _pick_best_lang(pubs: list[dict]) -> dict:
    """From a group of publications sharing the same projectid, pick best language."""
    return min(pubs, key=lambda p: _LANG_PREF.get(p.get("lang", ""), 99))


def _parse_price(raw: str | None) -> float | None:
    """Parse archive price strings like "9'521'600.00" into a float."""
    if not raw:
        return None
    try:
        return float(raw.replace("'", "").replace(",", "."))
    except (ValueError, AttributeError):
        return None


def _extract_contractors(ob02_spec: dict) -> list[dict]:
    """Return a normalised list of contractor dicts from the OB02 spec block."""
    award = ob02_spec.get("OB02.AWARD") or {}
    contractor_list = award.get("PRIM.CONTRACTOR.LIST") or {}
    raw = contractor_list.get("PRIM.CONTRACTOR")
    if not raw:
        return []
    # API returns a dict for single contractor, a list for multiple
    if isinstance(raw, dict):
        return [raw]
    if isinstance(raw, list):
        return raw
    return []


def _match_company(db: Session, vendor_name: str, zip_code: str | None) -> int | None:
    """Fuzzy-match a vendor to our companies table.

    Uses pg_trgm similarity (threshold 0.50) + exact zip code.
    Returns company.id or None if no confident match.
    """
    if not vendor_name:
        return None

    # Try zip + name similarity first (fast path: uses GIN trgm index on name)
    if zip_code:
        row = db.execute(
            text("""
                SELECT id
                FROM companies
                WHERE address_zip = :zip
                  AND similarity(name, :vname) >= 0.50
                ORDER BY similarity(name, :vname) DESC
                LIMIT 1
            """),
            {"zip": zip_code.strip(), "vname": vendor_name.strip()},
        ).first()
        if row:
            return row[0]

    # Fallback: name only at higher threshold (avoid false positives without zip)
    row = db.execute(
        text("""
            SELECT id
            FROM companies
            WHERE similarity(name, :vname) >= 0.70
            ORDER BY similarity(name, :vname) DESC
            LIMIT 1
        """),
        {"vname": vendor_name.strip()},
    ).first()
    return row[0] if row else None


def _build_award_row(pub: dict, detail: dict) -> dict[str, Any]:
    """Build the simap_awards upsert dict from a search publication + detail."""
    ob02 = detail.get("OB02") or {}
    spec = ob02.get("OB02.SPEC") or {}
    contract = spec.get("OB02.CONTRACT") or {}
    info = spec.get("OB02.INFORMATION") or {}
    auth = ob02.get("AUTHORITY") or {}
    auth_contact = auth.get("AUTH.CONTACT") or {}

    # Title: prefer CONT.SERVICES.CONT.NAME, then list description
    cont_obj = contract.get("CONT.SERVICES") or contract.get("CONT.WORKS") or contract.get("CONT.SUPPLIES") or {}
    title = cont_obj.get("CONT.NAME") or pub.get("description") or ""
    authority_name = auth_contact.get("AUTH.NAME") or pub.get("authName") or ""

    # CPV code(s) — take the first one
    cpv_list_raw = (cont_obj.get("CONT.CPV.LIST") or {}).get("CONT.CPV") or []
    if isinstance(cpv_list_raw, dict):
        cpv_list_raw = [cpv_list_raw]
    first_cpv = cpv_list_raw[0].get("CODE") if cpv_list_raw else None

    # Award decision date
    award_date_str = info.get("OB02.INFO.AWARD.DATE") or ""
    award_date: date | None = None
    if award_date_str:
        try:
            d, m, y = award_date_str.split(".")
            award_date = date(int(y), int(m), int(d))
        except Exception:
            pass

    # Number of submissions
    n_offers_raw = info.get("OB02.INFO.NUMBER.OFFERS") or ""
    n_offers: int | None = None
    if n_offers_raw:
        try:
            n_offers = int(n_offers_raw)
        except (ValueError, TypeError):
            pass

    # Publication date
    pub_date_str = pub.get("publicationDate") or ob02.get("SIMAP.PUB.DATE") or ""
    pub_date: date | None = None
    if pub_date_str:
        try:
            # publicationDate is YYYY-MM-DD from search; SIMAP.PUB.DATE is DD.MM.YYYY
            if "-" in pub_date_str:
                pub_date = date.fromisoformat(pub_date_str)
            else:
                d2, m2, y2 = pub_date_str.split(".")
                pub_date = date(int(y2), int(m2), int(d2))
        except Exception:
            pass

    if not pub_date:
        return {}

    # Language slot for title (we only have one language per detail call)
    lang = (pub.get("lang") or "DE").upper()
    title_de = title if lang == "DE" else None
    title_fr = title if lang == "FR" else None
    title_it = title if lang == "IT" else None
    auth_de = authority_name if lang == "DE" else None
    auth_fr = authority_name if lang == "FR" else None
    auth_it = authority_name if lang == "IT" else None

    # Proc type mapping
    proc_raw = ob02.get("OB.PROC") or {}
    process_type = proc_raw.get("VALUE")

    return {
        "simap_project_id": f"arch-{pub['projectid']}",
        "simap_publication_id": f"arch-{pub['id']}",
        "publication_date": pub_date,
        "award_decision_date": award_date,
        "pub_type": "award",
        "process_type": process_type,
        "project_type": None,
        "project_subtype": None,
        "creation_language": lang,
        "title_de": title_de,
        "title_fr": title_fr,
        "title_it": title_it,
        "proc_office_name_de": auth_de,
        "proc_office_name_fr": auth_fr,
        "proc_office_name_it": auth_it,
        "order_description_de": None,
        "order_description_fr": None,
        "order_description_it": None,
        "cpv_code": first_cpv,
        "number_of_submissions": n_offers,
        "total_price_selection": None,
        "total_price_range_min": None,
        "total_price_range_max": None,
        "total_price_currency": None,
        "lot_number": None,
        "lot_title_de": None,
        "lot_title_fr": None,
        "lot_title_it": None,
    }


def _upsert_award(db: Session, row: dict) -> tuple[SimapAward, bool]:
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
    pub_id: int,
    contractor: dict,
    company_id: int | None,
) -> bool:
    addr = contractor.get("ADDRESS") or {}
    price_obj = contractor.get("CONTRACTOR.PRICE") or {}
    price_val = _parse_price(price_obj.get("CONTRACTOR.PRICE.FROM"))
    currency = price_obj.get("CURRENCY")
    synthetic_vendor_id = f"arch-{pub_id}"

    fields = {
        "award_id": award.id,
        "company_id": company_id,
        "simap_vendor_id": synthetic_vendor_id,
        "vendor_name": contractor.get("CONTRACTOR.NAME") or "",
        "vendor_uid": None,
        "vendor_country": addr.get("COUNTRY"),
        "vendor_city": addr.get("CITY"),
        "vendor_postal_code": addr.get("ZIPCODE"),
        "price": price_val,
        "price_currency": currency,
        "rank": None,
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
    return bool(db.execute(stmt).scalar())


def import_simap_archive(
    db: Session,
    from_date: date | None = None,
    to_date: date | None = None,
    *,
    request_delay: float = 0.10,
    records_per_page: int = 1000,
    progress_cb: Callable[[int, int, dict], None] | None = None,
    status_cb: Callable[[str], None] | None = None,
    abort_cb: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Import pre-2024 SIMAP archive award notices into simap_awards.

    Paginates through all OB02 publications in the given date range,
    de-duplicates by projectid, fetches detail for each unique project,
    fuzzy-matches the contractor to our companies table, then upserts.

    Returns:
        Stats dict: created, updated, skipped, matched, unmatched, foreign, errors.
    """
    stats: dict[str, Any] = {
        "created": 0,
        "updated": 0,
        "skipped": 0,
        "matched": 0,
        "unmatched": 0,
        "foreign": 0,
        "errors": [],
    }

    from_str = from_date.isoformat() if from_date else None
    to_str = to_date.isoformat() if to_date else None

    if status_cb:
        status_cb(f"Fetching SIMAP archive {from_str or 'all'} → {to_str or 'all'} …")

    # First call to get the total count
    try:
        first_page = search_archive_awards(from_str, to_str, page_no=1, records_per_page=records_per_page)
    except httpx.HTTPError as exc:
        msg = f"Archive search first page failed: {exc}"
        logger.error(msg)
        stats["errors"].append(msg)
        return stats

    total_pubs = first_page.get("total", 0)
    total_pages = first_page.get("pages", 0)

    if status_cb:
        status_cb(f"Found {total_pubs:,} OB02 publications across {total_pages} pages")

    # Track which project_ids we've already processed (de-duplication)
    # We process page by page; within each page we group by projectid and pick best lang.
    # Across pages a projectid might recur — tracked in seen_project_ids.
    seen_project_ids: set[int] = set()
    done = 0  # unique projects processed

    with httpx.Client(timeout=30.0) as http:
        for page_no in range(1, total_pages + 1):
            if abort_cb:
                abort_cb()

            # Fetch search page (skip first page re-fetch)
            if page_no == 1:
                page_data = first_page
            else:
                try:
                    time.sleep(request_delay)
                    page_data = search_archive_awards(
                        from_str, to_str, page_no=page_no,
                        records_per_page=records_per_page, client=http,
                    )
                except httpx.HTTPError as exc:
                    msg = f"Search page {page_no} failed: {exc}"
                    logger.warning(msg)
                    stats["errors"].append(msg)
                    continue

            publications = page_data.get("publication") or []
            if not publications:
                break

            # Group by projectid and pick best language within this page
            by_project: dict[int, list[dict]] = {}
            for pub in publications:
                pid = pub.get("projectid")
                if pid is None:
                    continue
                by_project.setdefault(pid, []).append(pub)

            for project_id, pubs_for_project in by_project.items():
                if project_id in seen_project_ids:
                    continue
                seen_project_ids.add(project_id)

                best_pub = _pick_best_lang(pubs_for_project)
                pub_id = best_pub["id"]

                # Fetch detail
                try:
                    time.sleep(request_delay)
                    detail = get_archive_detail(pub_id, client=http)
                except httpx.HTTPError as exc:
                    msg = f"Detail fetch failed for pub {pub_id}: {exc}"
                    logger.warning(msg)
                    stats["errors"].append(msg)
                    stats["skipped"] += 1
                    continue

                # Build award row
                try:
                    award_row = _build_award_row(best_pub, detail)
                except Exception as exc:
                    msg = f"Parse error pub {pub_id}: {exc}"
                    logger.warning(msg)
                    stats["errors"].append(msg)
                    stats["skipped"] += 1
                    continue

                if not award_row:
                    stats["skipped"] += 1
                    continue

                award, created = _upsert_award(db, award_row)
                if created:
                    stats["created"] += 1
                else:
                    stats["updated"] += 1

                # Resolve contractors
                ob02 = detail.get("OB02") or {}
                spec = (ob02.get("OB02.SPEC") or {})
                contractors = _extract_contractors(spec)

                for contractor in contractors:
                    vendor_name = contractor.get("CONTRACTOR.NAME") or ""
                    addr = contractor.get("ADDRESS") or {}
                    vendor_zip = addr.get("ZIPCODE")
                    vendor_country = addr.get("COUNTRY", "")

                    company_id: int | None = None
                    if vendor_country and vendor_country.upper() != "CH":
                        stats["foreign"] += 1
                    elif vendor_name:
                        company_id = _match_company(db, vendor_name, vendor_zip)
                        if company_id:
                            stats["matched"] += 1
                        else:
                            stats["unmatched"] += 1

                    _upsert_vendor(db, award, pub_id, contractor, company_id)

                db.commit()

                done += 1
                if progress_cb:
                    progress_cb(done, len(seen_project_ids), dict(stats))

            if status_cb and page_no % 10 == 0:
                status_cb(
                    f"Page {page_no}/{total_pages} — {done} projects, "
                    f"{stats['created']} new, {stats['matched']} CH matched"
                )

    return stats
