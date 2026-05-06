"""Zefix API import pipeline: bulk sweep, per-UID fetch, and Zefix detail collection."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app import crud
from app.clients.zefix_client import (
    ALPHANUMERIC,
    EXPANSION_CHARS,
    SWISS_CANTONS,
    ZEFIX_MAX_ENTRIES,
    _normalise_uid,
    _parse_legal_form,
    fetch_companies_by_prefix,
    get_company as zefix_get_company,
    search_companies,
)
from app.models.company import Company
from app.schemas.company import CompanyCreate, CompanyUpdate
from app.services._pipeline_utils import _is_control_signal_exception, _sleep_with_abort
from app.services.scoring import compute_flex_score_breakdown, get_default_scoring_config

logger = logging.getLogger(__name__)


def _load_scoring_config(db: Session) -> dict[str, str]:
    defaults = get_default_scoring_config()
    return {key: crud.get_setting(db, key, val) for key, val in defaults.items()}


# ── Zefix payload parsing ─────────────────────────────────────────────────────

def _extract_purpose_from_raw(raw: dict[str, Any]) -> str | None:
    """Extract purpose/zweck text from a Zefix payload."""
    purpose_raw = raw.get("purpose") or raw.get("purposes") or raw.get("purposeTexts") or None
    if isinstance(purpose_raw, list):
        texts: list[str] = []
        for item in purpose_raw:
            if isinstance(item, dict):
                text = (
                    item.get("de") or item.get("fr") or item.get("it") or item.get("en")
                    or next(iter(item.values()), None)
                )
                if text:
                    texts.append(str(text))
            elif item:
                texts.append(str(item))
        return " ".join(texts).strip() or None
    if isinstance(purpose_raw, dict):
        text = (
            purpose_raw.get("de") or purpose_raw.get("fr")
            or purpose_raw.get("it") or purpose_raw.get("en")
            or next(iter(purpose_raw.values()), None)
        )
        return str(text).strip() if text else None
    return str(purpose_raw).strip() if purpose_raw else None


def _is_detailed_zefix_raw_payload(raw: dict[str, Any]) -> bool:
    """Heuristic check for detailed Zefix payloads (per-UID/company-full style)."""
    detail_keys = (
        "purposeTexts", "sogcPub", "zefixDetailWeb", "headOffices",
        "furtherHeadOffices", "branchOffices", "hasTakenOver", "wasTakenOverBy",
        "auditCompanies", "oldNames", "capitalNominal", "cantonalExcerptWeb",
    )
    for k in detail_keys:
        v = raw.get(k)
        if v not in (None, "", [], {}):
            return True
    return False


def _extract_company_fields(
    raw: dict[str, Any],
    fallback_uid: str,
    *,
    scoring_config: dict[str, str] | None = None,
) -> CompanyCreate:
    name_raw = raw.get("name", "")
    if isinstance(name_raw, dict):
        name = (
            name_raw.get("de") or name_raw.get("fr") or name_raw.get("it")
            or next(iter(name_raw.values()), "")
        )
    else:
        name = str(name_raw)

    legal_form_display, legal_form_id, legal_form_uid, legal_form_short = _parse_legal_form(
        raw.get("legalForm")
    )

    address_parts = raw.get("address", {}) or {}
    address_str: str | None = None
    city = ""
    zip_code = ""
    if isinstance(address_parts, dict):
        a_parts: list[str] = []
        org = address_parts.get("organisation") or ""
        care_of = address_parts.get("careOf") or ""
        street = address_parts.get("street") or ""
        house_num = address_parts.get("houseNumber") or ""
        addon = address_parts.get("addon") or ""
        po_box = address_parts.get("poBox") or ""
        city = address_parts.get("city") or ""
        zip_code = address_parts.get("swissZipCode") or ""
        if org:
            a_parts.append(org)
        if care_of:
            a_parts.append(f"c/o {care_of}")
        street_line = f"{street} {house_num}".strip()
        if street_line:
            a_parts.append(street_line)
        if addon:
            a_parts.append(addon)
        if po_box:
            a_parts.append(f"Postfach {po_box}")
        zip_city = f"{zip_code} {city}".strip()
        if zip_city:
            a_parts.append(zip_city)
        address_str = ", ".join(a_parts) or None

    uid_normalised = _normalise_uid(str(raw.get("uid", fallback_uid)))
    purpose = _extract_purpose_from_raw(raw)

    ehraid_raw = raw.get("ehraId") or raw.get("ehraid") or raw.get("ehra_id")
    ehraid = str(ehraid_raw) if ehraid_raw is not None else None

    chid_raw = raw.get("chid")
    chid = str(chid_raw) if chid_raw is not None else None

    legal_seat_id_raw = raw.get("legalSeatId") or raw.get("legal_seat_id")
    legal_seat_id: int | None = None
    if legal_seat_id_raw is not None:
        try:
            legal_seat_id = int(legal_seat_id_raw)
        except (ValueError, TypeError):
            pass

    sogc_date_raw = raw.get("sogcDate") or raw.get("sogc_date")
    sogc_date = str(sogc_date_raw) if sogc_date_raw else None

    deletion_date_raw = raw.get("deletionDate") or raw.get("deletion_date")
    deletion_date = str(deletion_date_raw) if deletion_date_raw else None

    capital_nominal_raw = raw.get("capitalNominal")
    capital_nominal = str(capital_nominal_raw) if capital_nominal_raw is not None else None
    capital_currency = raw.get("capitalCurrency") or None
    head_offices = json.dumps(raw.get("headOffices")) if raw.get("headOffices") is not None else None
    cantonal_excerpt_web = raw.get("cantonalExcerptWeb") or None

    def _json_field(val: Any) -> str | None:
        return json.dumps(val) if val is not None else None

    further_head_offices = _json_field(raw.get("furtherHeadOffices"))
    branch_offices = _json_field(raw.get("branchOffices"))
    has_taken_over = _json_field(raw.get("hasTakenOver"))
    was_taken_over_by = _json_field(raw.get("wasTakenOverBy"))
    audit_companies = _json_field(raw.get("auditCompanies"))
    old_names = _json_field(raw.get("oldNames"))
    translation_raw = raw.get("translation") or []
    translations = json.dumps(translation_raw) if translation_raw else None
    sogc_pub = _json_field(raw.get("sogcPub"))

    zefix_web_raw = raw.get("zefixDetailWeb") or {}
    zefix_detail_web: str | None = None
    if isinstance(zefix_web_raw, dict):
        zefix_detail_web = (
            zefix_web_raw.get("de") or zefix_web_raw.get("fr")
            or zefix_web_raw.get("it") or zefix_web_raw.get("en") or None
        )
    elif isinstance(zefix_web_raw, str):
        zefix_detail_web = zefix_web_raw or None

    score_breakdown = compute_flex_score_breakdown(
        legal_form=legal_form_display,
        legal_form_short_name=legal_form_short,
        status=str(raw.get("status", "")) or None,
        canton=raw.get("canton") or None,
        municipality=raw.get("municipality") or raw.get("legalSeat") or None,
        config=scoring_config,
    )
    flex_score = int(score_breakdown["final_score"])

    return CompanyCreate(
        uid=uid_normalised,
        name=name,
        legal_form=legal_form_display,
        legal_form_id=legal_form_id,
        legal_form_uid=legal_form_uid,
        legal_form_short_name=legal_form_short,
        status=str(raw.get("status", "")) or None,
        municipality=raw.get("municipality") or raw.get("legalSeat") or None,
        canton=raw.get("canton") or None,
        purpose=purpose,
        address=address_str,
        flex_score=flex_score,
        flex_score_breakdown=json.dumps(score_breakdown),
        combined_score=Company.compute_combined_score(None, flex_score=flex_score),
        ehraid=ehraid,
        chid=chid,
        legal_seat_id=legal_seat_id,
        sogc_date=sogc_date,
        first_sogc_date=sogc_date,
        deletion_date=deletion_date,
        capital_nominal=capital_nominal,
        capital_currency=capital_currency,
        head_offices=head_offices,
        further_head_offices=further_head_offices,
        branch_offices=branch_offices,
        has_taken_over=has_taken_over,
        was_taken_over_by=was_taken_over_by,
        audit_companies=audit_companies,
        old_names=old_names,
        translations=translations,
        sogc_pub=sogc_pub,
        cantonal_excerpt_web=cantonal_excerpt_web,
        zefix_detail_web=zefix_detail_web,
        address_city=city if city else None,
        address_zip=zip_code if zip_code else None,
        zefix_raw=json.dumps(raw),
    )


# ── Per-UID import ────────────────────────────────────────────────��───────────

def import_company_from_zefix_uid(
    db: Session,
    uid: str | list[Any],
    *,
    pause_on_zefix_500: bool = False,
    status_cb: Any = None,
    abort_cb: Any = None,
) -> tuple[Company, bool]:
    """Import or update a company from Zefix by UID.

    Returns:
        (company, created)
    """
    uid_normalized = _normalise_uid(uid)
    if not uid_normalized:
        raise ValueError("UID is required")

    while True:
        try:
            raw = zefix_get_company(uid_normalized)
            break
        except httpx.HTTPStatusError as exc:
            if (
                pause_on_zefix_500
                and exc.response is not None
                and exc.response.status_code in {500, 502, 503, 504}
            ):
                _wait_for_zefix_availability(status_cb=status_cb, abort_cb=abort_cb)
                continue
            raise

    scoring_config = _load_scoring_config(db)
    company_data = _extract_company_fields(raw, uid_normalized, scoring_config=scoring_config)

    existing = crud.get_company_by_uid(db, company_data.uid)
    if existing:
        payload = company_data.model_dump(exclude={"uid", "website_url", "first_sogc_date"}, exclude_none=True)
        updated = crud.update_company(db, existing, CompanyUpdate(**payload))
        return updated, False

    created = crud.create_company(db, company_data)
    return created, True


# ── Zefix availability helpers ────────────────────────────────────────────────

def _is_name_too_short_error(response: Any) -> bool:
    """Return True when Zefix rejected the query specifically because the name is too short."""
    try:
        body = response.json() if callable(getattr(response, "json", None)) else {}
        msg = str(body).lower()
        return "name to short" in msg or "name too short" in msg
    except Exception:  # noqa: BLE001
        return False


def _wait_for_zefix_availability(
    *,
    status_cb: Any = None,
    abort_cb: Any = None,
    check_every_seconds: float = 60.0,
) -> None:
    """Pause execution when ZEFIX returns HTTP 500 and poll until it recovers."""
    interval = max(5.0, float(check_every_seconds))
    while True:
        if status_cb:
            status_cb(
                f"ZEFIX returned 500 Internal Server Error. Pausing import and rechecking availability in {int(interval)}s..."
            )
        _sleep_with_abort(interval, abort_cb=abort_cb)
        try:
            search_companies("AA", max_results=1, active_only=True)
            if status_cb:
                status_cb("ZEFIX is reachable again. Resuming import.")
            return
        except httpx.HTTPStatusError as exc:
            if exc.response is not None and exc.response.status_code == 500:
                if abort_cb:
                    abort_cb()
                continue
            raise
        except (httpx.TimeoutException, httpx.ConnectError):
            if abort_cb:
                abort_cb()
            continue


# ── Prefix sweep ──────────────────────────────────────────────────────────────

def _iter_prefix_with_fallback(
    canton: str | None,
    prefix: str,
    active_only: bool,
    request_delay: float,
    _depth: int = 0,
    _seen: set[str] | None = None,
    _min_query_len: int = 2,
    _too_short_log: list[str] | None = None,
    *,
    status_cb: Any = None,
    abort_cb: Any = None,
):
    """Yield companies for *prefix*, expanding to longer prefixes when the cap is hit.

    Generator — results yielded one-by-one so the caller can write them to the DB
    without accumulating a large list in RAM. Expands up to three levels deep.
    """
    _MAX_DEPTH = 2

    if _seen is None:
        _seen = set()

    expand = False
    results: list[Any] = []

    if len(prefix) < _min_query_len:
        expand = True
    else:
        try:
            while True:
                try:
                    results = fetch_companies_by_prefix(prefix, canton, active_only=active_only)
                    break
                except httpx.HTTPStatusError as exc:
                    if exc.response is not None and exc.response.status_code == 500:
                        _wait_for_zefix_availability(status_cb=status_cb, abort_cb=abort_cb)
                        continue
                    raise
            if len(results) >= ZEFIX_MAX_ENTRIES:
                expand = True
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 400:
                expand = True
                if _is_name_too_short_error(exc.response) and _too_short_log is not None:
                    _too_short_log.append(prefix)
            elif exc.response.status_code == 500:
                _wait_for_zefix_availability(status_cb=status_cb, abort_cb=abort_cb)
                return
            else:
                raise

    if not expand:
        for r in results:
            if r.uid and r.uid not in _seen:
                _seen.add(r.uid)
                yield r
        return

    if _depth >= _MAX_DEPTH:
        for r in results:
            if r.uid and r.uid not in _seen:
                _seen.add(r.uid)
                yield r
        return

    for char in EXPANSION_CHARS:
        sub_prefix = prefix + char
        try:
            yield from _iter_prefix_with_fallback(
                canton, sub_prefix, active_only, request_delay,
                _depth + 1, _seen, _min_query_len, _too_short_log,
                status_cb=status_cb, abort_cb=abort_cb,
            )
        except httpx.TimeoutException:
            raise
        except Exception as exc:  # noqa: BLE001
            if _is_control_signal_exception(exc):
                raise
            continue
        _sleep_with_abort(request_delay, abort_cb=abort_cb)


# ── Bulk import ───────────────────────────────────────────────────────────────

def bulk_import_zefix(
    db: Session,
    *,
    cantons: list[str] | None = None,
    active_only: bool = False,
    request_delay: float = 0.5,
    resume: bool = False,
    start_from_canton: str | None = None,
    empty_abort_threshold: int = 100,
    progress_cb: Any = None,
    status_cb: Any = None,
    abort_cb: Any = None,
) -> dict[str, Any]:
    """Import all companies from Zefix using an alphabet-prefix sweep per canton.

    For each canton, queries A–Z as name prefixes. If any single-letter query
    returns the API's hard cap (500), it automatically expands that letter into
    26 two-letter sub-prefixes to capture the full set.

    Args:
        cantons: Canton codes to scan. Defaults to all 26.
        active_only: Only import active register entries.
        request_delay: Seconds to sleep between API calls.
        resume: Continue from the last saved checkpoint if one exists.
    """
    target_cantons = cantons or SWISS_CANTONS
    scoring_config = _load_scoring_config(db)

    run = None
    start_canton_idx = 0
    start_prefix_idx = 0

    if resume:
        run = crud.get_last_incomplete_bulk(db)
        if run and run.last_canton and run.last_canton in target_cantons:
            start_canton_idx = target_cantons.index(run.last_canton)
            start_prefix_idx = (run.last_offset or 0) + 1
            existing_stats: dict[str, Any] = json.loads(run.stats_json or "{}")
        else:
            run = None

    if run is None:
        stale = crud.get_last_incomplete_bulk(db)
        if stale:
            crud.complete_run(db, stale, json.loads(stale.stats_json or "{}"))
        run = crud.create_run(db, "bulk")
        existing_stats = {}

        if start_from_canton:
            upper = start_from_canton.upper()
            if upper in target_cantons:
                start_canton_idx = target_cantons.index(upper)
            else:
                existing_stats.setdefault("errors", []).append(
                    f"start_from_canton '{start_from_canton}' not found in target list; starting from beginning"
                )

    stats: dict[str, Any] = {
        "cantons_done": existing_stats.get("cantons_done", 0),
        "created": existing_stats.get("created", 0),
        "updated": existing_stats.get("updated", 0),
        "errors": existing_stats.get("errors", []),
        "too_short_skips": existing_stats.get("too_short_skips", 0),
    }

    def _process_one_prefix(canton: str, char: str, prefix_idx: int) -> tuple[bool, int, bool]:
        if progress_cb:
            progress_cb(canton, char, stats["created"], stats["updated"])

        too_short_log: list[str] = []
        try:
            result_iter = _iter_prefix_with_fallback(
                canton, char,
                active_only=active_only,
                request_delay=request_delay,
                _too_short_log=too_short_log,
                status_cb=status_cb,
                abort_cb=abort_cb,
            )
        except Exception as exc:  # noqa: BLE001
            if _is_control_signal_exception(exc):
                raise
            stats["errors"].append(f"Canton {canton} prefix {char} [{type(exc).__name__}]: {exc}")
            crud.update_checkpoint(db, run, canton, prefix_idx, stats)
            _sleep_with_abort(request_delay, abort_cb=abort_cb)
            return True, 0, False

        timed_out = False
        results_count = 0
        try:
            for result in result_iter:
                if not result.uid:
                    continue
                results_count += 1
                _score_bd = compute_flex_score_breakdown(
                    legal_form=result.legal_form,
                    legal_form_short_name=result.legal_form_short_name,
                    status=result.status,
                    canton=result.canton or canton,
                    municipality=result.municipality,
                    config=scoring_config,
                )
                company_data = CompanyCreate(
                    uid=result.uid,
                    name=result.name,
                    legal_form=result.legal_form,
                    legal_form_id=result.legal_form_id,
                    legal_form_uid=result.legal_form_uid,
                    legal_form_short_name=result.legal_form_short_name,
                    status=result.status,
                    municipality=result.municipality,
                    canton=result.canton or canton,
                    purpose=result.purpose,
                    flex_score=int(_score_bd["final_score"]),
                    flex_score_breakdown=json.dumps(_score_bd),
                    combined_score=Company.compute_combined_score(None, flex_score=int(_score_bd["final_score"])),
                    ehraid=result.ehraid,
                    chid=result.chid,
                    legal_seat_id=result.legal_seat_id,
                    sogc_date=result.sogc_date,
                    deletion_date=result.deletion_date,
                )
                if crud.get_company_by_uid(db, result.uid):
                    stats["skipped"] = stats.get("skipped", 0) + 1
                    continue
                crud.create_company(db, company_data)
                stats["created"] += 1
        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            timed_out = True
            stats["errors"].append(
                f"Canton {canton} prefix {char} [timeout, queued for retry]: {exc}"
            )
        except Exception as exc:  # noqa: BLE001
            if _is_control_signal_exception(exc):
                raise
            stats["errors"].append(f"Canton {canton} prefix {char} mid-stream [{type(exc).__name__}]: {exc}")

        was_too_short = bool(too_short_log)
        if was_too_short:
            stats["too_short_skips"] = stats.get("too_short_skips", 0) + len(too_short_log)
        crud.update_checkpoint(db, run, canton, prefix_idx, stats)
        if progress_cb:
            progress_cb(canton, char, stats["created"], stats["updated"])
        _sleep_with_abort(request_delay, abort_cb=abort_cb)
        return not timed_out, results_count, was_too_short

    retry_queue: list[tuple[str, str, int]] = []
    consecutive_empty = 0
    aborted_early = False

    for canton in target_cantons[start_canton_idx:]:
        prefix_start = start_prefix_idx if canton == target_cantons[start_canton_idx] else 0

        for prefix_idx, char in enumerate(ALPHANUMERIC):
            if prefix_idx < prefix_start:
                continue

            ok, count, was_too_short = _process_one_prefix(canton, char, prefix_idx)
            if not ok:
                retry_queue.append((canton, char, prefix_idx))

            if was_too_short:
                consecutive_empty = 0
            elif count == 0:
                consecutive_empty += 1
            else:
                consecutive_empty = 0

            if empty_abort_threshold > 0 and consecutive_empty >= empty_abort_threshold:
                stats["errors"].append(
                    f"Aborted after {consecutive_empty} consecutive empty prefixes "
                    f"(last: {canton}/{char}) — Zefix may be down or rate-limiting"
                )
                aborted_early = True
                break

        if aborted_early:
            break

        stats["cantons_done"] += 1
        start_prefix_idx = 0
        consecutive_empty = 0
        _sleep_with_abort(request_delay, abort_cb=abort_cb)

    if retry_queue:
        stats["errors"].append(f"--- Retrying {len(retry_queue)} timed-out prefix(es) ---")
        still_failed: list[str] = []
        for canton, char, prefix_idx in retry_queue:
            ok, _, _ = _process_one_prefix(canton, char, prefix_idx)
            if not ok:
                still_failed.append(f"{canton}/{char}")
        if still_failed:
            stats["errors"].append(f"Permanently failed after retry: {', '.join(still_failed)}")

    crud.complete_run(db, run, stats)
    return stats


# ── Initial collect ───────────────────────────────────────────────────────────

def initial_collect(
    db: Session,
    *,
    names: list[str],
    uids: list[str],
    search_max_results: int = 25,
    active_only: bool = True,
    run_google: bool = True,
    canton: str | None = None,
    legal_form: str | None = None,
    resume_from: int = 0,
    progress_cb: Any = None,
    status_cb: Any = None,
    abort_cb: Any = None,
) -> dict[str, Any]:
    """Run a one-time collection from explicit UIDs and search terms."""
    from app.services.web_enrichment import _google_search_ready, enrich_company_website

    stats: dict[str, Any] = {
        "created": 0,
        "updated": 0,
        "google_enriched": 0,
        "google_no_result": 0,
        "errors": [],
    }

    target_uids: list[str] = []
    seen: set[str] = set()

    for uid in uids:
        uid_clean = uid.strip()
        if not uid_clean or uid_clean in seen:
            continue
        seen.add(uid_clean)
        target_uids.append(uid_clean)

    for name in names:
        name_clean = name.strip()
        if not name_clean:
            continue
        try:
            results = search_companies(
                name_clean,
                max_results=search_max_results,
                active_only=active_only,
                canton=canton,
                legal_form=legal_form,
            )
        except Exception as exc:  # noqa: BLE001
            stats["errors"].append(f"Search '{name_clean}' [{type(exc).__name__}]: {exc}")
            continue

        for result in results:
            uid_clean = (result.uid or "").strip()
            if not uid_clean or uid_clean in seen:
                continue
            seen.add(uid_clean)
            target_uids.append(uid_clean)

    total = len(target_uids)
    start_idx = max(0, min(resume_from, total))

    if run_google:
        ok, reason = _google_search_ready(db)
        if not ok:
            stats["errors"].append(f"Google enrichment skipped: {reason}.")
            run_google = False

    for idx, uid_clean in enumerate(target_uids[start_idx:], start=start_idx + 1):
        try:
            company, created = import_company_from_zefix_uid(
                db, uid_clean, pause_on_zefix_500=True, status_cb=status_cb, abort_cb=abort_cb,
            )
            stats["created" if created else "updated"] += 1
            if run_google:
                enriched, _ = enrich_company_website(db, company)
                if enriched:
                    stats["google_enriched"] += 1
                else:
                    stats["google_no_result"] += 1
        except Exception as exc:  # noqa: BLE001
            if _is_control_signal_exception(exc):
                raise
            stats["errors"].append(f"UID {uid_clean} [{type(exc).__name__}]: {exc}")
        if progress_cb:
            progress_cb(idx, total, stats)

    return stats


# ── Re-extract purpose from stored raw ───────────────────────────────────────

def reextract_purpose_from_zefix_raw(
    db: Session,
    *,
    batch_size: int = 500,
    resume_from: int = 0,
    only_missing_purpose: bool = True,
    progress_cb: Any = None,
) -> dict[str, Any]:
    """Re-extract purpose/zweck from stored detailed zefix_raw payloads."""
    stats: dict[str, Any] = {
        "updated": 0,
        "skipped_not_detailed": 0,
        "skipped_existing": 0,
        "skipped_empty_extracted": 0,
        "errors": [],
    }

    total = db.query(Company).filter(Company.zefix_raw.isnot(None)).count()
    offset = max(0, min(resume_from, total))

    while True:
        batch = (
            db.query(Company)
            .filter(Company.zefix_raw.isnot(None))
            .order_by(Company.id.asc())
            .offset(offset)
            .limit(batch_size)
            .all()
        )
        if not batch:
            break

        for company in batch:
            try:
                raw_text = (company.zefix_raw or "").strip()
                if not raw_text:
                    stats["skipped_not_detailed"] += 1
                    continue

                raw = json.loads(raw_text)
                if not isinstance(raw, dict) or not _is_detailed_zefix_raw_payload(raw):
                    stats["skipped_not_detailed"] += 1
                    continue

                if only_missing_purpose and (company.purpose or "").strip():
                    stats["skipped_existing"] += 1
                    continue

                extracted = _extract_purpose_from_raw(raw)
                if not extracted:
                    stats["skipped_empty_extracted"] += 1
                    continue

                if (company.purpose or "").strip() == extracted:
                    stats["skipped_existing"] += 1
                    continue

                crud.update_company(db, company, CompanyUpdate(purpose=extracted))
                stats["updated"] += 1

            except Exception as exc:  # noqa: BLE001
                logger.warning("Purpose re-extraction failed for company %s: %s", company.id, exc)
                stats["errors"].append(f"Company {company.id} [{type(exc).__name__}]: {exc}")

        db.commit()
        offset += len(batch)

        if progress_cb:
            progress_cb(min(offset, total), total, stats)

    return stats


# ── Enrichment coordination ───────────────────────────────────���───────────────

def enrich_company(
    db: Session,
    company: Company,
    *,
    noga: bool = True,
    clusters: bool = True,
) -> dict[str, int]:
    """Geocode, extract keywords, apply NOGA, and assign clusters for a single company.

    Each step is skipped if already done or prerequisites are missing.
    Returns counts: {"geocoded": 0|1, "keywords": 0|1, "noga_classified": 0|1, "cluster_assigned": 0|1}.
    """
    from app.services.geocoding_pipeline import geocode_and_update_company
    from app.services.noga import apply_noga_classification

    result = {"geocoded": 0, "keywords": 0, "noga_classified": 0, "cluster_assigned": 0}

    if geocode_and_update_company(db, company):
        result["geocoded"] = 1

    if company.purpose and not company.purpose_keywords:
        try:
            from app.services.cluster_pipeline import extract_keywords_incremental
            kw = extract_keywords_incremental(company)
            if kw:
                crud.update_company(db, company, CompanyUpdate(purpose_keywords=kw))
                company.purpose_keywords = kw
                result["keywords"] = 1
        except Exception:  # noqa: BLE001
            pass

    if noga:
        noga_update = apply_noga_classification(db, company)
        if noga_update is not None:
            crud.update_company(db, company, noga_update)
            result["noga_classified"] = 1

    if clusters and company.tfidf_cluster is None and company.purpose:
        try:
            from app.services.incremental_classify import classify_new_companies_inline
            r = classify_new_companies_inline(
                db, [company.id],
                run_noga=False,
                run_clusters=True,
                run_business_model=True,
            )
            result["cluster_assigned"] = r.get("cluster_assigned", 0)
        except Exception:  # noqa: BLE001
            pass

    return result


# ── Zefix detail collection ───────────────────────────────────────────────────

def run_zefix_detail_collect(
    db: Session,
    *,
    cantons: list[str] | None = None,
    uids: list[str] | None = None,
    score_if_missing: bool = True,
    only_missing_details: bool = False,
    resume_from: int = 0,
    request_delay: float = 0.3,
    progress_cb: Any = None,
    status_cb: Any = None,
    abort_cb: Any = None,
) -> dict[str, Any]:
    """Fetch full Zefix detail records for companies already in the database.

    Targeting priority:
        1. Explicit *uids* list — processes exactly those companies.
        2. *cantons* filter — all DB companies in those cantons.
        3. No filter — all companies in the database.
    """
    from sqlalchemy import or_, case
    from app.services.web_enrichment import rescore_from_stored_results

    stats: dict[str, Any] = {
        "selected": 0,
        "updated": 0,
        "noga_classified": 0,
        "scored": 0,
        "geocoded": 0,
        "errors": [],
    }
    noga_enabled = True

    run = crud.create_run(db, "detail")

    def _detail_priority_order(q):
        has_detail = case(
            (Company.zefix_raw.isnot(None), 1),
            else_=0,
        )
        return q.order_by(has_detail.asc(), Company.flex_score.desc().nullslast(), Company.id.asc())

    _missing_details_filter = or_(
        Company.address.is_(None),
        Company.purpose.is_(None),
        Company.cantonal_excerpt_web.is_(None),
    )

    if uids:
        companies_list: list[Company] = [
            c for uid in uids if (c := crud.get_company_by_uid(db, uid)) is not None
        ]
        if only_missing_details:
            companies_list = [
                c for c in companies_list
                if c.address is None or c.purpose is None or c.cantonal_excerpt_web is None
            ]
        total = len(companies_list)
        start_idx = max(0, min(resume_from, total))
        companies_iter = iter(companies_list[start_idx:])
    else:
        q = db.query(Company)
        if cantons:
            q = q.filter(Company.canton.in_(cantons))
        if only_missing_details:
            q = q.filter(_missing_details_filter)
        q = _detail_priority_order(q)
        rows: list[tuple[str, str | None]] = (
            q.with_entities(Company.uid, Company.canton).all()
        )
        total = len(rows)
        start_idx = max(0, min(resume_from, total))
        companies_iter = iter(rows[start_idx:])

    stats["selected"] = total

    for i, company in enumerate(companies_iter, start=start_idx + 1):
        try:
            updated, _ = import_company_from_zefix_uid(
                db,
                company.uid,
                pause_on_zefix_500=True,
                status_cb=status_cb,
                abort_cb=abort_cb,
            )
            stats["updated"] += 1

            if score_if_missing and updated.web_score is None:
                if rescore_from_stored_results(db, updated):
                    stats["scored"] += 1

            if noga_enabled:
                try:
                    enriched = enrich_company(db, updated)
                    stats["geocoded"] += enriched["geocoded"]
                    stats["noga_classified"] += enriched["noga_classified"]
                except FileNotFoundError as exc:
                    stats["errors"].append(f"NOGA classification disabled: {exc}")
                    noga_enabled = False
            else:
                enriched = enrich_company(db, updated, noga=False)
                stats["geocoded"] += enriched["geocoded"]

        except Exception as exc:  # noqa: BLE001
            if _is_control_signal_exception(exc):
                raise
            logger.warning("Detail collect failed for %s: %s", company.uid, exc)
            stats["errors"].append(f"{company.uid} [{type(exc).__name__}]: {exc}")

        if progress_cb:
            progress_cb(i, total, stats)

        if i % 50 == 0:
            crud.update_checkpoint(db, run, company.canton or "—", i, stats)

        _sleep_with_abort(request_delay, abort_cb=abort_cb)

    crud.complete_run(db, run, stats)
    return stats


# ── zefix_raw re-extraction ───────────────────────────────────────────────────

# All company columns that can be recovered from zefix_raw without any API call.
# Excluded: first_sogc_date (set once on creation, never overwritten),
# flex_score/flex_score_breakdown (dedicated recalculate job),
# website_url and enrichment columns (not from zefix_raw).
REEXTRACTABLE_FIELDS: frozenset[str] = frozenset({
    "name", "legal_form", "legal_form_id", "legal_form_uid", "legal_form_short_name",
    "status", "municipality", "canton", "purpose",
    "address", "address_city", "address_zip",
    "ehraid", "chid", "legal_seat_id", "sogc_date", "deletion_date",
    "capital_nominal", "capital_currency",
    "head_offices", "further_head_offices", "branch_offices",
    "has_taken_over", "was_taken_over_by", "audit_companies",
    "old_names", "translations", "sogc_pub",
    "cantonal_excerpt_web", "zefix_detail_web",
})


def _parse_id_filter(ids: list) -> tuple[list[int], list[str], list[str]]:
    """Split a mixed ID list into (internal_ids, uids, chids)."""
    internal_ids: list[int] = []
    uids: list[str] = []
    chids: list[str] = []
    for raw_id in ids:
        if isinstance(raw_id, int):
            internal_ids.append(raw_id)
        else:
            s = str(raw_id).strip()
            try:
                internal_ids.append(int(s))
            except ValueError:
                if s.upper().startswith("CHE-"):
                    uids.append(s)
                else:
                    chids.append(s)
    return internal_ids, uids, chids


def reextract_zefix_raw_fields(
    db: Session,
    *,
    fields: list[str] | None = None,
    ids: list | None = None,
    mode: str = "missing",
    batch_size: int = 500,
    resume_from: int = 0,
    progress_cb: Any = None,
    abort_cb: Any = None,
) -> dict:
    """Re-extract columns from the stored zefix_raw JSON blob without API calls.

    Args:
        fields: Column names to re-extract. None means all REEXTRACTABLE_FIELDS.
        ids: Restrict to these companies (internal int IDs, UIDs like CHE-…, or CHIDs).
             None means all companies.
        mode: "missing" — only rows where at least one requested field is NULL;
              "all" — every row regardless of current values.
        batch_size: DB commit interval.
        resume_from: Skip the first N qualifying rows (for job resumption).
        progress_cb: Callable(done, total, stats).
        abort_cb: Callable() that raises on cancellation.
    """
    from sqlalchemy import or_
    from app.models.company import Company as CompanyModel

    target_fields = sorted(
        REEXTRACTABLE_FIELDS if not fields else REEXTRACTABLE_FIELDS & set(fields)
    )
    if not target_fields:
        raise ValueError(f"No valid fields. Valid: {sorted(REEXTRACTABLE_FIELDS)}")

    stats: dict = {"updated": 0, "skipped_no_raw": 0, "errors": []}

    q = db.query(CompanyModel).filter(CompanyModel.zefix_raw.isnot(None))

    if ids:
        internal_ids, uids, chids = _parse_id_filter(ids)
        id_filters = []
        if internal_ids:
            id_filters.append(CompanyModel.id.in_(internal_ids))
        if uids:
            id_filters.append(CompanyModel.uid.in_(uids))
        if chids:
            id_filters.append(CompanyModel.chid.in_(chids))
        if id_filters:
            q = q.filter(or_(*id_filters))

    if mode == "missing":
        null_checks = [
            getattr(CompanyModel, f).is_(None)
            for f in target_fields
            if hasattr(CompanyModel, f)
        ]
        if null_checks:
            q = q.filter(or_(*null_checks))

    q = q.order_by(CompanyModel.id)
    total = q.count()
    offset = max(0, min(resume_from, total))
    processed = offset

    while True:
        if abort_cb:
            abort_cb()

        batch = q.offset(offset).limit(batch_size).all()
        if not batch:
            break

        for company in batch:
            if abort_cb:
                abort_cb()

            try:
                raw = json.loads(company.zefix_raw)
            except Exception:  # noqa: BLE001
                stats["skipped_no_raw"] += 1
                processed += 1
                continue

            extracted = _extract_company_fields(raw, company.uid)
            update_payload = {
                k: v for k, v in extracted.model_dump(include=set(target_fields)).items()
            }

            try:
                crud.update_company(db, company, CompanyUpdate(**update_payload))
                stats["updated"] += 1
            except Exception as exc:  # noqa: BLE001
                if _is_control_signal_exception(exc):
                    raise
                logger.warning("reextract failed for %s: %s", company.uid, exc)
                stats["errors"].append(f"{company.uid}: {exc}")

            processed += 1
            if progress_cb and processed % 100 == 0:
                progress_cb(processed, total, stats)

        db.commit()
        offset += len(batch)

    db.commit()
    if progress_cb:
        progress_cb(processed, total, stats)

    return stats
