"""Import service for the SHAB public archive (shab.ch/api/v1/archive).

Fetches HR publications page-by-page and upserts rows into
``sogc_publications`` + ``sogc_changes``.

Two pagination modes
────────────────────
Page mode (no dates):
    Fetches the default 2018 window.  Hard API cap of ~50 K entries total.
    Use for quick tests.  Params: ``start_page``, ``end_page``.

Date-window mode (with dates):
    Splits a date range into ``window_days``-sized chunks, paginating each
    chunk independently.  Keeps each window under the ~50 K cap.  Use for
    full historical imports (e.g. 2013-present).
    Params: ``date_start`` (YYYY-MM-DD), ``date_end`` (YYYY-MM-DD),
            ``window_days`` (default 28).

Resume
──────
``resume_from`` maps to ``progress_done`` (DB counter).

•  Page mode    → absolute page number of last completed page.
•  Date-window  → number of completed windows.

sogc_id convention
──────────────────
``"shab_{archive_id}"`` — never collides with Zefix SOGC IDs (plain numbers).
"""

from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_SOGC_ID_PREFIX = "shab_"


def _make_sogc_id(archive_id: int | str) -> str:
    return f"{_SOGC_ID_PREFIX}{archive_id}"


def _detect_lang(text: str, canton: str | None) -> str:
    if text:
        try:
            from app.services.ml.language_detection import detect_purpose_language
            lang = detect_purpose_language(text)
            if lang in ("de", "fr", "it", "en"):
                return lang
        except Exception:
            pass
    if canton:
        c = canton.upper()
        if c in ("GE", "JU", "NE", "VD"):
            return "fr"
        if c == "TI":
            return "it"
    return "de"


def _append_shab_old_name(existing_json: str | None, name: str, pub_date: str | None) -> str:
    """Append a SHAB-sourced historical name to the company's old_names JSON array.

    Idempotent — skips entries whose "name" already appears in the list.
    """
    try:
        entries = json.loads(existing_json) if existing_json else []
        if not isinstance(entries, list):
            entries = []
    except Exception:
        entries = []
    if any(e.get("name") == name for e in entries):
        return existing_json or "[]"
    entries.append({"name": name, "source": "shab_archive", "date": pub_date})
    return json.dumps(entries)


def _get_company_by_old_uid(db: Session, old_uid: str) -> Any | None:
    """Look up a Company by an old cantonal HR number via array overlap on old_uids.

    Postgres-only (`&&` array operator); the SHAB import never runs on SQLite.
    Returns the ORM object (so old_names merges work) or None.

    Defensive against statement_timeout: this job interleaves reads with writes
    to the same GIN-indexed column within one long session, so a transient lock
    wait or pending-list scan can occasionally exceed the 30s cap. Treat a
    timeout as "not found locally" (falls through to the API lookup) instead of
    aborting the whole job. Runs inside a SAVEPOINT so a timeout only discards
    this lookup (autoflush included) rather than the rest of the open batch.
    """
    from sqlalchemy.exc import OperationalError

    from app.models.company import Company as CompanyModel

    sp = db.begin_nested()
    try:
        row = db.execute(
            text("SELECT id FROM companies WHERE old_uids && CAST(:arr AS text[]) LIMIT 1"),
            {"arr": [old_uid]},
        ).fetchone()
        sp.commit()
    except OperationalError as exc:
        sp.rollback()
        logger.warning("old_uids lookup timed out for %s, falling back to API: %s", old_uid, exc)
        return None
    if not row:
        return None
    return db.get(CompanyModel, row[0])


def _resolve_company_for_shab(
    db: Session,
    uid: str | None,
    title: str | None,
    canton: str | None,
    pub_date: str | None,
    uid_map: dict[str, Any] | None,
    stats: dict,
    *,
    old_uid: str | None = None,
    merge_old_name: bool = True,
) -> tuple[str | None, int | None]:
    """Return (company_uid, company_id) for a SHAB publication.

    Resolution order: modern CHE ``uid`` (batch cache → DB) then, if unmatched,
    the old cantonal ``old_uid`` (batch cache → old_uids array overlap). Pre-2014
    archive entries only carry the old number; a local miss here is later resolved
    by ``resolve_shab_old_uids`` (reverse UID Search), which also populates
    companies.old_uids so subsequent imports match locally.

    When matched, the returned company_uid is the company's CANONICAL (modern)
    uid, even if the match came via the old number.  If the SHAB title differs
    from the canonical name it is recorded in old_names (merge_old_name path).

    If no company is found, a minimal stub is created (source='shab_stub',
    status='CANCELLED') keyed by whichever identifier is available — the modern
    uid if present, else the old number, which is also stored in old_uids so a
    later backfill/import can reconcile it.  Stubs are cached in uid_map.
    """
    if not uid and not old_uid:
        return None, None

    # 1. Resolve by modern CHE uid first, then by old cantonal number.
    company = None
    if uid:
        if uid_map is not None:
            company = uid_map.get(uid)
        if company is None:
            from app import crud
            company = crud.get_company_by_uid(db, uid)

    if company is None and old_uid:
        if uid_map is not None:
            company = uid_map.get(old_uid)
        if company is None:
            company = _get_company_by_old_uid(db, old_uid)
        if company is not None:
            stats["matched_by_old_uid"] = stats.get("matched_by_old_uid", 0) + 1

    if company is not None:
        if merge_old_name and title and title.strip() and title.strip() != company.name:
            company.old_names = _append_shab_old_name(
                company.old_names, title.strip(), pub_date
            )
            db.flush()
        if uid_map is not None:
            if uid:
                uid_map[uid] = company
            if old_uid:
                uid_map[old_uid] = company
        return company.uid, company.id

    # 2. Company absent — create a stub for cancelled companies not in Zefix.
    stub_uid = uid or old_uid
    if not title or not title.strip():
        return stub_uid, None  # no name available; link identifier only

    from app.models.company import Company as CompanyModel
    from sqlalchemy.exc import IntegrityError

    stub = CompanyModel(
        uid=stub_uid,
        name=title.strip()[:512],
        status="CANCELLED",
        source="shab_stub",
        canton=(canton or "")[:8] or None,
        first_sogc_date=pub_date,
        sogc_date=pub_date,
        old_uids=[old_uid] if old_uid else None,
    )
    sp = db.begin_nested()
    try:
        db.add(stub)
        sp.commit()
    except IntegrityError:
        sp.rollback()
        from app import crud
        stub = crud.get_company_by_uid(db, stub_uid)
        if stub is None:
            raise

    db.flush()
    stats["stubs_created"] = stats.get("stubs_created", 0) + 1
    if uid_map is not None:
        if uid:
            uid_map[uid] = stub
        if old_uid:
            uid_map[old_uid] = stub
    return stub.uid, stub.id


def _date_windows(
    date_start: str,
    date_end: str,
    window_days: int,
) -> list[tuple[str, str]]:
    """Split a date range into (start, end) tuples of at most window_days each."""
    d_start = date.fromisoformat(date_start)
    d_end = date.fromisoformat(date_end)
    windows: list[tuple[str, str]] = []
    cur = d_start
    while cur <= d_end:
        win_end = min(cur + timedelta(days=window_days - 1), d_end)
        windows.append((cur.isoformat(), win_end.isoformat()))
        cur = win_end + timedelta(days=1)
    return windows


def _prefetch_pdfs_for_page(
    entries: list[dict],
    *,
    pdf_workers: int,
    stats: dict,
) -> dict[str, bytes | None]:
    """Download PDFs for all HR entries in a page concurrently.

    Returns {archive_id: bytes} on success, {archive_id: None} on failure
    (error already appended to stats["errors"]).  Non-HR entries are omitted.
    ``stats["pdfs_downloaded"]`` is incremented here so _process_entry skips it.
    """
    from app.clients.shab_archive_client import (
        fetch_pdf_bytes, parse_entry_fields, is_hr_heading,
    )

    hr_ids: list[str] = []
    for entry in entries:
        f = parse_entry_fields(entry)
        aid = f.get("archive_id")
        if aid and is_hr_heading(f.get("heading", ""), f.get("rubric", "")):
            hr_ids.append(str(aid))

    if not hr_ids:
        return {}

    def _dl(aid: str) -> tuple[str, bytes | Exception]:
        try:
            return aid, fetch_pdf_bytes(aid)
        except Exception as exc:  # noqa: BLE001
            return aid, exc

    pdf_map: dict[str, bytes | None] = {}
    with ThreadPoolExecutor(max_workers=min(pdf_workers, len(hr_ids))) as pool:
        for aid, result in pool.map(_dl, hr_ids):
            if isinstance(result, Exception):
                stats["errors"].append(f"[{aid}] PDF failed: {result}")
                pdf_map[aid] = None
            else:
                pdf_map[aid] = result
                stats["pdfs_downloaded"] += 1

    return pdf_map


def _build_shab_uid_map(db: Session, pdf_cache: dict[str, bytes | None]) -> dict[str, Any]:
    """Pre-resolve companies for a page of PDFs by BOTH modern CHE and old HR number.

    Extracts every CHE-UID and old cantonal number from the page's PDFs, then does
    two batched queries (``uid IN (…)`` and ``old_uids && ARRAY[…]``). The returned
    map is keyed by whichever identifier appears in each PDF, so _process_entry can
    resolve either without a per-entry DB hit.
    """
    from app.clients.shab_archive_client import (
        extract_text_from_pdf, extract_uid_from_text, extract_old_uid_from_text,
    )
    from app.models.company import Company

    uids: set[str] = set()
    old_uids: set[str] = set()
    for pdf_bytes in pdf_cache.values():
        if not pdf_bytes:
            continue
        raw = extract_text_from_pdf(pdf_bytes)
        if not raw:
            continue
        u = extract_uid_from_text(raw)
        if u:
            uids.add(u)
        ou = extract_old_uid_from_text(raw)
        if ou:
            old_uids.add(ou)

    uid_map: dict[str, Any] = {}
    if uids:
        for c in db.query(Company).filter(Company.uid.in_(uids)).all():
            uid_map[c.uid] = c
    if old_uids:
        rows = db.execute(
            text("SELECT id FROM companies WHERE old_uids && CAST(:arr AS text[])"),
            {"arr": list(old_uids)},
        ).fetchall()
        for (cid,) in rows:
            c = db.get(Company, cid)
            if c is None:
                continue
            for ou in (c.old_uids or []):
                if ou in old_uids:
                    uid_map[ou] = c
    return uid_map


def _process_entry(
    db: Session,
    entry: dict,
    *,
    stats: dict,
    pdf_delay: float,
    pdf_cache: dict[str, bytes | None] | None = None,
    uid_map: dict[str, Any] | None = None,
) -> None:
    """Download PDF, extract text, upsert SogcPublication + SogcChanges."""
    from app.clients.shab_archive_client import (
        fetch_pdf_bytes,
        extract_text_from_pdf,
        normalize_pdf_text,
        extract_uid_from_text,
        extract_old_uid_from_text,
        extract_canton_from_text,
        is_hr_heading,
        parse_entry_fields,
    )
    from app.models.sogc_publication import SogcPublication
    from app.models.sogc_change import SogcChange
    from app.services.registry.sogc_preprocessor import _detect_changes

    fields = parse_entry_fields(entry)
    archive_id = fields["archive_id"]
    rubric = fields["rubric"]
    heading = fields["heading"]
    pub_date = fields["pub_date"]
    canton = fields["canton"]
    title = fields["title"]
    pub_number = fields["pub_number"]

    stats["entries_scanned"] += 1

    if not archive_id:
        stats["skipped"] += 1
        return

    if not is_hr_heading(heading, rubric):
        stats["skipped"] += 1
        return

    stats["hr_entries"] += 1
    sogc_id = _make_sogc_id(archive_id)

    if pdf_cache is not None:
        # Concurrent mode — PDF was pre-fetched
        cached = pdf_cache.get(str(archive_id))
        if cached is None:
            return  # download failed; error already in stats
        pdf_bytes = cached
        # pdfs_downloaded already incremented in _prefetch_pdfs_for_page
    else:
        try:
            pdf_bytes = fetch_pdf_bytes(archive_id)
            stats["pdfs_downloaded"] += 1
            time.sleep(pdf_delay)
        except Exception as exc:
            stats["errors"].append(f"[{archive_id}] PDF failed: {exc}")
            return

    pdf_text_raw = extract_text_from_pdf(pdf_bytes)
    if not pdf_text_raw:
        stats["pdf_text_empty"] += 1
        logger.warning("SHAB archive: PDF text empty for id=%s (bytes=%d)", archive_id, len(pdf_bytes))
    # Extract structured fields from raw text (canton regex relies on newlines)
    uid = extract_uid_from_text(pdf_text_raw) if pdf_text_raw else None
    # Pre-2014 entries carry only the old cantonal HR number, not a CHE-UID.
    old_uid = extract_old_uid_from_text(pdf_text_raw) if pdf_text_raw else None
    pdf_canton = extract_canton_from_text(pdf_text_raw) if pdf_text_raw else None
    lang = _detect_lang(pdf_text_raw or title, pdf_canton or canton)
    # Normalise for storage: rejoin hyphenated line-breaks, collapse newlines
    pdf_text = normalize_pdf_text(pdf_text_raw) if pdf_text_raw else ""

    texts: dict[str, str | None] = {"de": None, "fr": None, "it": None, "en": None}
    if pdf_text:
        texts[lang] = pdf_text

    change_dicts = _detect_changes(texts)

    raw_meta = {
        "archive_id": archive_id,
        "subRubric": rubric,
        "publicationDate": pub_date,
        "canton": pdf_canton or canton,
        "title": title,
        "source": "shab_archive",
    }
    if uid:
        raw_meta["extracted_uid"] = uid
    if old_uid:
        raw_meta["extracted_old_uid"] = old_uid

    try:
        existing = db.query(SogcPublication).filter_by(sogc_id=sogc_id).first()

        if existing:
            pub = existing
            if pub_date:
                pub.pub_date = pub_date
            if rubric:
                pub.sub_rubric = rubric
            if pub_number:
                pub.pub_number = pub_number
            pub.detected_language = lang
            if pdf_text:
                setattr(pub, f"text_{lang}", pdf_text)
            pub.raw_json = json.dumps(raw_meta)
            pub.preprocessed_at = datetime.now(tz=timezone.utc)
            if (uid or old_uid) and not pub.company_uid:
                # Re-import: link company if not yet linked; skip name merging
                # (already done on first import) to avoid duplicate old_names entries.
                comp_uid, comp_id = _resolve_company_for_shab(
                    db, uid, title, pdf_canton or canton, pub_date,
                    uid_map, stats, old_uid=old_uid, merge_old_name=False,
                )
                if comp_uid:
                    pub.company_uid = comp_uid
                    pub.company_id = comp_id
            db.query(SogcChange).filter_by(sogc_publication_id=pub.id).delete()
            db.flush()
            for ch in change_dicts:
                db.add(SogcChange(
                    sogc_publication_id=pub.id,
                    change_type=ch["change_type"],
                    keywords_matched=ch.get("keywords_matched"),
                    raw_excerpt=(ch.get("raw_excerpt") or "")[:500],
                ))
            stats["updated"] += 1
        else:
            comp_uid, comp_id = _resolve_company_for_shab(
                db, uid, title, pdf_canton or canton, pub_date,
                uid_map, stats, old_uid=old_uid, merge_old_name=True,
            )
            pub = SogcPublication(
                sogc_id=sogc_id,
                company_uid=comp_uid,
                company_id=comp_id,
                pub_date=pub_date,
                sub_rubric=rubric,
                pub_number=pub_number,
                text_de=texts.get("de"),
                text_fr=texts.get("fr"),
                text_it=texts.get("it"),
                text_en=texts.get("en"),
                detected_language=lang,
                encoding_fixed=False,
                raw_json=json.dumps(raw_meta),
                preprocessed_at=datetime.now(tz=timezone.utc),
            )
            db.add(pub)
            db.flush()
            for ch in change_dicts:
                db.add(SogcChange(
                    sogc_publication_id=pub.id,
                    change_type=ch["change_type"],
                    keywords_matched=ch.get("keywords_matched"),
                    raw_excerpt=(ch.get("raw_excerpt") or "")[:500],
                ))
            stats["created"] += 1

        db.commit()

    except Exception as exc:  # noqa: BLE001
        try:
            db.rollback()
        except Exception:
            pass
        stats["errors"].append(f"[{archive_id}] DB: {type(exc).__name__}: {exc}")
        logger.warning("SHAB archive DB write failed id=%s: %s", archive_id, exc)


def _paginate_window(
    db: Session,
    *,
    date_start: str | None,
    date_end: str | None,
    page_size: int,
    request_delay: float,
    pdf_delay: float,
    pdf_workers: int = 8,
    start_page: int = 0,
    stats: dict,
    abort_cb,
    progress_cb,
    progress_offset: int = 0,
    progress_total: int = 1,
) -> None:
    """Paginate one date window (or the default window if no dates given)."""
    from app.clients.shab_archive_client import (
        fetch_archive_page, get_entries, is_last_page,
    )

    page = start_page
    while True:
        if abort_cb:
            abort_cb()

        if page > start_page:
            time.sleep(request_delay)

        try:
            page_data = fetch_archive_page(
                page,
                size=page_size,
                date_start=date_start,
                date_end=date_end,
            )
        except RuntimeError as exc:
            logger.info("SHAB pagination cap reached at page %d: %s", page, exc)
            stats.setdefault("warnings", []).append(
                f"Pagination cap at page {page} [{date_start}–{date_end}]: {exc}"
            )
            break
        except Exception as exc:
            stats["errors"].append(f"Page {page} fetch failed: {exc}")
            break

        all_entries = get_entries(page_data)

        # ── Concurrent PDF download ───────────────────────────────────────────
        pdf_cache: dict[str, bytes | None] | None = None
        uid_map: dict[str, Any] | None = None

        if pdf_workers > 1 and all_entries:
            pdf_cache = _prefetch_pdfs_for_page(
                all_entries, pdf_workers=pdf_workers, stats=stats,
            )

            # ── Batch UID lookup (modern CHE + old cantonal HR number) ────────
            uid_map = _build_shab_uid_map(db, pdf_cache)

        for entry in all_entries:
            if abort_cb:
                abort_cb()
            _process_entry(
                db, entry,
                stats=stats,
                pdf_delay=pdf_delay,
                pdf_cache=pdf_cache,
                uid_map=uid_map,
            )

        stats["pages_fetched"] += 1

        if progress_cb:
            progress_cb(progress_offset, progress_total, stats)

        if is_last_page(page_data):
            break

        page += 1


def import_shab_archive(
    db: Session,
    *,
    # Page mode (no dates)
    start_page: int = 0,
    end_page: int | None = None,
    # Date-window mode
    date_start: str | None = None,
    date_end: str | None = None,
    window_days: int = 28,
    # Common
    page_size: int = 100,
    request_delay: float = 0.3,
    pdf_delay: float = 0.0,
    pdf_workers: int = 8,
    resume_from: int = 0,
    progress_cb=None,
    status_cb=None,
    abort_cb=None,
) -> dict[str, Any]:
    """Import SHAB archive publications into ``sogc_publications`` + ``sogc_changes``.

    Use ``date_start`` / ``date_end`` for full historical imports (recommended).
    ``window_days`` controls how many days per API window (keep ≤ 28 to stay
    under the ~50 K per-query cap).

    Without dates, falls back to the default 2018 API window (page-based,
    limited to ~50 K entries total) — useful for quick tests.
    """
    stats: dict[str, Any] = {
        "mode": "date_window" if date_start else "page",
        "pages_fetched": 0,
        "entries_scanned": 0,
        "hr_entries": 0,
        "pdfs_downloaded": 0,
        "pdf_text_empty": 0,
        "created": 0,
        "updated": 0,
        "stubs_created": 0,
        "skipped": 0,
        "errors": [],
        "warnings": [],
    }

    if date_start:
        # ── Date-window mode ──────────────────────────────────────────────────
        effective_end = date_end or date.today().isoformat()
        windows = _date_windows(date_start, effective_end, window_days)
        total_windows = len(windows)
        stats["total_windows"] = total_windows

        # Skip already-completed windows (resume_from = number done)
        start_window_idx = resume_from

        if status_cb:
            status_cb(
                f"SHAB archive: {total_windows} date windows "
                f"({date_start} → {effective_end}, {window_days}d each) — "
                f"starting from window {start_window_idx + 1}/{total_windows}…"
            )

        for win_idx, (w_start, w_end) in enumerate(windows):
            if win_idx < start_window_idx:
                continue

            if abort_cb:
                abort_cb()

            if status_cb:
                status_cb(
                    f"Window {win_idx + 1}/{total_windows}: {w_start} → {w_end} "
                    f"({stats['created']} created, {stats['updated']} updated so far)…"
                )

            _paginate_window(
                db,
                date_start=w_start,
                date_end=w_end,
                page_size=page_size,
                request_delay=request_delay,
                pdf_delay=pdf_delay,
                pdf_workers=pdf_workers,
                stats=stats,
                abort_cb=abort_cb,
                progress_cb=progress_cb,
                progress_offset=win_idx + 1,
                progress_total=total_windows,
            )

    else:
        # ── Page mode (quick test / default 2018 window) ──────────────────────
        current_page = max(start_page, resume_from)
        if status_cb:
            status_cb(
                f"SHAB archive page mode: pages {current_page}–"
                f"{'end' if end_page is None else end_page} (size={page_size})…"
            )

        page = current_page
        while end_page is None or page <= end_page:
            if abort_cb:
                abort_cb()
            if page > current_page:
                time.sleep(request_delay)

            from app.clients.shab_archive_client import (
                fetch_archive_page, get_entries, is_last_page,
            )
            try:
                page_data = fetch_archive_page(page, size=page_size)
            except RuntimeError as exc:
                stats["warnings"].append(f"Pagination cap at page {page}: {exc}")
                break
            except Exception as exc:
                stats["errors"].append(f"Page {page}: {exc}")
                break

            all_entries = get_entries(page_data)
            pdf_cache_p: dict[str, bytes | None] | None = None
            uid_map_p: dict[str, Any] | None = None
            if pdf_workers > 1 and all_entries:
                pdf_cache_p = _prefetch_pdfs_for_page(
                    all_entries, pdf_workers=pdf_workers, stats=stats,
                )
                uid_map_p = _build_shab_uid_map(db, pdf_cache_p)

            for entry in all_entries:
                if abort_cb:
                    abort_cb()
                _process_entry(
                    db, entry, stats=stats, pdf_delay=pdf_delay,
                    pdf_cache=pdf_cache_p, uid_map=uid_map_p,
                )

            stats["pages_fetched"] += 1

            if progress_cb:
                done = page - current_page + 1
                total = (end_page - current_page + 1) if end_page else done
                progress_cb(done, total, stats)

            if is_last_page(page_data):
                break
            page += 1

    return stats


def import_shab_old_pdfs(
    db: Session,
    *,
    date_start: str,
    date_end: str,
    request_delay: float = 1.0,
    resume_from: int = 0,
    job_run_id: int | None = None,
    progress_cb=None,
    status_cb=None,
    abort_cb=None,
) -> dict[str, Any]:
    """Import pre-2012 SHAB HR publications from the daily bulk PDF endpoint.

    Downloads one PDF per business day via
    ``GET /api/v1/archive/issue-of-today?date=YYYY-MM-DD``.
    Each PDF covers all cantons + non-HR publication types; this function
    extracts only the Handelsregister entries.

    ``sogc_id`` format: ``"shab_old_{YYYYMMDD}_{pub_number}"``
    Company UIDs are NOT available in these PDFs (old CH-xxx format only);
    ``company_uid`` and ``company_id`` are left NULL.

    ``resume_from``: number of days already completed (maps to ``progress_done``).
    """
    from app.clients.shab_archive_client import (
        fetch_daily_pdf_bytes,
        extract_text_from_pdf,
        parse_bulk_hr_entries,
        check_bulk_pdf_structure,
    )
    from app.models.sogc_publication import SogcPublication
    from app.models.sogc_change import SogcChange
    from app.services.registry.sogc_preprocessor import _detect_changes

    def _log_structural_error(
        _db: Session,
        _date: str,
        _error_type: str,
        _message: str,
        _detail: dict,
        _job_run_id: int | None,
    ) -> None:
        """Log a structural PDF error to the Error Center; never raises."""
        try:
            from app.crud.company_error import log_error as _log_err
            _log_err(
                _db,
                company_id=None,
                source="shab_old_pdf",
                error_type=_error_type,
                message=_message,
                detail={**_detail, "date": _date},
                job_run_id=_job_run_id,
            )
            _db.flush()
        except Exception as _exc:
            logger.warning("Error Center log failed for %s/%s: %s", _date, _error_type, _exc)

    stats: dict[str, Any] = {
        "mode": "old_pdf",
        "days_attempted": 0,
        "days_skipped": 0,
        "pdfs_downloaded": 0,
        "entries_parsed": 0,
        "created": 0,
        "updated": 0,
        "errors": [],
    }

    d_start = date.fromisoformat(date_start)
    d_end = date.fromisoformat(date_end)
    all_days: list[date] = []
    cur = d_start
    while cur <= d_end:
        if cur.weekday() < 5:  # Mon–Fri only
            all_days.append(cur)
        cur += timedelta(days=1)

    total_days = len(all_days)
    stats["total_days"] = total_days

    if status_cb:
        status_cb(
            f"SHAB old-PDF: {total_days} weekdays "
            f"({date_start} → {date_end}) — "
            f"starting from day {resume_from + 1}/{total_days}…"
        )

    for day_idx, day in enumerate(all_days):
        if day_idx < resume_from:
            continue

        if abort_cb:
            abort_cb()

        day_str = day.isoformat()
        stats["days_attempted"] += 1

        if status_cb and day_idx % 10 == 0:
            status_cb(
                f"Day {day_idx + 1}/{total_days}: {day_str} "
                f"({stats['created']} created, {stats['updated']} updated)…"
            )

        try:
            pdf_bytes = fetch_daily_pdf_bytes(day_str)
        except Exception as exc:
            stats["errors"].append(f"[{day_str}] PDF download failed: {exc}")
            logger.warning("SHAB old-PDF download failed for %s: %s", day_str, exc)
            continue

        if pdf_bytes is None:
            stats["days_skipped"] += 1
            logger.debug("No issue for %s (weekend/holiday)", day_str)
            if day_idx > resume_from:
                time.sleep(request_delay * 0.25)
            continue

        stats["pdfs_downloaded"] += 1
        pdf_size = len(pdf_bytes)

        pdf_text_raw = extract_text_from_pdf(pdf_bytes)
        if not pdf_text_raw:
            msg = f"PDF text extraction returned empty string (bytes={pdf_size})"
            stats["errors"].append(f"[{day_str}] {msg}")
            logger.warning("SHAB old-PDF: %s for %s", msg, day_str)
            _log_structural_error(
                db, day_str, "pdf_text_empty", msg,
                {"pdf_size": pdf_size}, job_run_id,
            )
            continue

        # ── Structural validation ─────────────────────────────────────────────
        try:
            struct = check_bulk_pdf_structure(pdf_text_raw, day_str)
        except Exception as exc:
            logger.warning("SHAB old-PDF structure check error for %s: %s", day_str, exc)
            struct = {"hr_end_found": True, "tagebuch_count": -1, "warnings": [], "critical": False}

        if struct["warnings"]:
            for w in struct["warnings"]:
                stats["errors"].append(w)
                logger.warning("SHAB old-PDF structure: %s", w)
            _log_structural_error(
                db, day_str,
                "pdf_structure_changed",
                struct["warnings"][0],
                {"pdf_size": pdf_size, **struct},
                job_run_id,
            )

        if struct["critical"]:
            stats["days_skipped"] += 1
            continue

        # ── Parse HR entries ──────────────────────────────────────────────────
        try:
            entries = parse_bulk_hr_entries(pdf_text_raw, day_str)
        except Exception as exc:
            msg = f"PDF parse raised {type(exc).__name__}: {exc}"
            stats["errors"].append(f"[{day_str}] {msg}")
            logger.warning("SHAB old-PDF parse error for %s: %s", day_str, exc)
            _log_structural_error(
                db, day_str, "pdf_parse_failed", msg,
                {"pdf_size": pdf_size}, job_run_id,
            )
            continue

        stats["entries_parsed"] += len(entries)

        # ── Post-parse quality checks ─────────────────────────────────────────
        if entries:
            none_canton = sum(1 for e in entries if e["canton"] is None)
            none_subtype = sum(1 for e in entries if e["sub_rubric"] == "HR")
            if none_canton == len(entries):
                warn = (
                    f"[{day_str}] Canton detection failed for all {len(entries)} entries "
                    "(canton section header format may have changed)."
                )
                stats["errors"].append(warn)
                logger.warning("SHAB old-PDF: %s", warn)
                _log_structural_error(
                    db, day_str, "canton_detection_failed", warn,
                    {"pdf_size": pdf_size, "entries": len(entries)}, job_run_id,
                )
            if none_subtype == len(entries):
                warn = (
                    f"[{day_str}] Subtype classification failed for all {len(entries)} entries "
                    "(Neueintragungen/Mutationen/Löschungen headers may have changed)."
                )
                stats["errors"].append(warn)
                logger.warning("SHAB old-PDF: %s", warn)
                _log_structural_error(
                    db, day_str, "subtype_detection_failed", warn,
                    {"pdf_size": pdf_size, "entries": len(entries)}, job_run_id,
                )
        elif pdf_size > 100_000:
            warn = (
                f"[{day_str}] PDF is {pdf_size // 1024}KB but 0 HR entries were parsed "
                "(entry delimiter 'Tagebuch Nr.' may have changed format)."
            )
            stats["errors"].append(warn)
            logger.warning("SHAB old-PDF: %s", warn)
            _log_structural_error(
                db, day_str, "zero_entries_large_pdf", warn,
                {"pdf_size": pdf_size, "tagebuch_count": struct.get("tagebuch_count", 0)},
                job_run_id,
            )

        from app.clients.uid_client import normalize_old_hr_number

        # Per-day cache so repeated old numbers within an issue hit the DB once.
        day_uid_map: dict[str, Any] = {}

        for entry in entries:
            if abort_cb:
                abort_cb()

            pub_number = entry["pub_number"]
            date_compact = day_str.replace("-", "")
            sogc_id = f"shab_old_{date_compact}_{pub_number}"

            lang = _detect_lang(entry["text"], entry.get("canton"))
            texts: dict[str, str | None] = {"de": None, "fr": None, "it": None, "en": None}
            if entry["text"]:
                texts[lang] = entry["text"]

            change_dicts = _detect_changes(texts)

            raw_meta = {
                "pub_number": pub_number,
                "ch_number": entry["ch_number"],
                "pub_date": day_str,
                "canton": entry.get("canton"),
                "title": entry.get("title", ""),
                "city": entry.get("city"),
                "sub_rubric": entry["sub_rubric"],
                "source": "shab_old_pdf",
            }

            try:
                # These PDFs carry only the old cantonal HR number (no CHE-UID); resolve
                # via companies.old_uids locally — a miss is picked up later by
                # resolve_shab_old_uids (reverse UID Search).
                # Inside the try so a DB error here fails just this entry, not the job.
                old_uid = normalize_old_hr_number(entry.get("ch_number"))
                comp_uid, comp_id = _resolve_company_for_shab(
                    db, None, entry.get("title"), entry.get("canton"), day_str,
                    day_uid_map, stats, old_uid=old_uid, merge_old_name=True,
                )
                if comp_uid:
                    raw_meta["resolved_company_uid"] = comp_uid

                existing = db.query(SogcPublication).filter_by(sogc_id=sogc_id).first()
                if existing:
                    pub = existing
                    pub.pub_date = day_str
                    pub.sub_rubric = entry["sub_rubric"]
                    pub.pub_number = pub_number
                    pub.detected_language = lang
                    if entry["text"]:
                        setattr(pub, f"text_{lang}", entry["text"])
                    if comp_uid and not pub.company_uid:
                        pub.company_uid = comp_uid
                        pub.company_id = comp_id
                    pub.raw_json = json.dumps(raw_meta)
                    pub.preprocessed_at = datetime.now(tz=timezone.utc)
                    db.query(SogcChange).filter_by(sogc_publication_id=pub.id).delete()
                    db.flush()
                    for ch in change_dicts:
                        db.add(SogcChange(
                            sogc_publication_id=pub.id,
                            change_type=ch["change_type"],
                            keywords_matched=ch.get("keywords_matched"),
                            raw_excerpt=(ch.get("raw_excerpt") or "")[:500],
                        ))
                    stats["updated"] += 1
                else:
                    pub = SogcPublication(
                        sogc_id=sogc_id,
                        company_uid=comp_uid,
                        company_id=comp_id,
                        pub_date=day_str,
                        sub_rubric=entry["sub_rubric"],
                        pub_number=pub_number,
                        text_de=texts.get("de"),
                        text_fr=texts.get("fr"),
                        text_it=texts.get("it"),
                        text_en=texts.get("en"),
                        detected_language=lang,
                        encoding_fixed=False,
                        raw_json=json.dumps(raw_meta),
                        preprocessed_at=datetime.now(tz=timezone.utc),
                    )
                    db.add(pub)
                    db.flush()
                    for ch in change_dicts:
                        db.add(SogcChange(
                            sogc_publication_id=pub.id,
                            change_type=ch["change_type"],
                            keywords_matched=ch.get("keywords_matched"),
                            raw_excerpt=(ch.get("raw_excerpt") or "")[:500],
                        ))
                    stats["created"] += 1

                db.commit()

            except Exception as exc:
                try:
                    db.rollback()
                except Exception:
                    pass
                stats["errors"].append(f"[{sogc_id}] DB: {type(exc).__name__}: {exc}")
                logger.warning("SHAB old-PDF DB write failed %s: %s", sogc_id, exc)

        if progress_cb:
            progress_cb(day_idx + 1, total_days, stats)

        if day_idx < len(all_days) - 1:
            time.sleep(request_delay)

    return stats


def run_link_sogc_stubs(
    db: Session,
    *,
    batch_size: int = 500,
    progress_cb=None,
    status_cb=None,
    abort_cb=None,
) -> dict[str, Any]:
    """Back-fill company_id on sogc_publications and sogc_person_appearances.

    Works entirely from existing DB data — no API calls or PDF downloads.
    Finds all distinct company_uid values that have no company_id yet, then
    either links the existing Company row or creates a shab_stub for unknown UIDs.

    Uses keyset pagination (alphabetical by uid) to handle large row counts.
    """
    from sqlalchemy import text, update
    from app.models.sogc_publication import SogcPublication
    from app.models.sogc_person_appearance import SogcPersonAppearance
    from app.models.company import Company as CompanyModel
    from sqlalchemy.exc import IntegrityError

    stats: dict[str, Any] = {
        "uids_scanned": 0,
        "stubs_created": 0,
        "already_linked": 0,
        "publications_linked": 0,
        "appearances_linked": 0,
        "errors": [],
    }

    if status_cb:
        status_cb("Starting back-fill of unlinked UIDs…")

    cursor_uid = ""  # keyset: start before first UID alphabetically
    done = 0

    while True:
        if abort_cb:
            abort_cb()

        # Fetch next batch of distinct UIDs that are unlinked, cursor-paginated
        rows = db.execute(
            text(
                "SELECT DISTINCT company_uid FROM sogc_publications "
                "WHERE company_uid IS NOT NULL AND company_id IS NULL "
                "  AND company_uid > :cursor "
                "ORDER BY company_uid LIMIT :lim"
            ),
            {"cursor": cursor_uid, "lim": batch_size},
        ).fetchall()

        if not rows:
            break

        uids: list[str] = [r[0] for r in rows]
        cursor_uid = uids[-1]

        # Bulk-fetch matching companies
        companies = (
            db.query(CompanyModel)
            .filter(CompanyModel.uid.in_(uids))
            .all()
        )
        uid_to_company: dict[str, CompanyModel] = {c.uid: c for c in companies}

        for uid in uids:
            if abort_cb:
                abort_cb()
            stats["uids_scanned"] += 1

            try:
                company = uid_to_company.get(uid)

                if company is None:
                    # Need a stub — extract best available name from a publication
                    pub_row = db.execute(
                        text(
                            "SELECT raw_json FROM sogc_publications "
                            "WHERE company_uid = :uid AND raw_json IS NOT NULL "
                            "LIMIT 1"
                        ),
                        {"uid": uid},
                    ).fetchone()

                    title: str | None = None
                    canton: str | None = None
                    pub_date: str | None = None
                    if pub_row and pub_row[0]:
                        try:
                            rj = json.loads(pub_row[0])
                            # SHAB archive format
                            title = rj.get("title") or rj.get("meta", {}).get("title", {}).get("de")
                            canton = rj.get("canton")
                            pub_date = rj.get("pub_date")
                        except Exception:
                            pass

                    if not title:
                        # No usable name; link uid only (company_id stays NULL)
                        continue

                    stub = CompanyModel(
                        uid=uid,
                        name=title.strip()[:512],
                        status="CANCELLED",
                        source="shab_stub",
                        canton=(canton or "")[:8] or None,
                        first_sogc_date=pub_date,
                        sogc_date=pub_date,
                    )
                    sp = db.begin_nested()
                    try:
                        db.add(stub)
                        sp.commit()
                    except IntegrityError:
                        sp.rollback()
                        from app import crud
                        stub = crud.get_company_by_uid(db, uid)
                        if stub is None:
                            raise

                    db.flush()
                    company = stub
                    uid_to_company[uid] = stub
                    stats["stubs_created"] += 1
                else:
                    stats["already_linked"] += 1

                company_id = company.id

                # Bulk-update publications
                pub_result = db.execute(
                    update(SogcPublication)
                    .where(
                        SogcPublication.company_uid == uid,
                        SogcPublication.company_id.is_(None),
                    )
                    .values(company_id=company_id)
                )
                stats["publications_linked"] += pub_result.rowcount

                # Bulk-update person appearances
                app_result = db.execute(
                    update(SogcPersonAppearance)
                    .where(
                        SogcPersonAppearance.company_uid == uid,
                        SogcPersonAppearance.company_id.is_(None),
                    )
                    .values(company_id=company_id)
                )
                stats["appearances_linked"] += app_result.rowcount

            except Exception as exc:  # noqa: BLE001
                logger.warning("link_sogc_stubs error for uid=%s: %s", uid, exc)
                stats["errors"].append(f"{uid}: {exc}")

        db.commit()
        done += len(uids)

        if progress_cb:
            progress_cb(done, None, stats)

    return stats


# ── Reverse resolution of old cantonal HR numbers ─────────────────────────────

def _old_uid_from_raw(raw_json: str | None) -> str | None:
    """Pull a canonical old HR number out of a SogcPublication.raw_json blob.

    Mode B (archive) stores the already-normalised ``extracted_old_uid``; Mode A
    (old bulk PDF) stores the raw ``ch_number``. Both are normalised to the dotted
    canonical form so they match companies.old_uids and the reverse Search input.
    """
    if not raw_json:
        return None
    try:
        meta = json.loads(raw_json)
    except Exception:
        return None
    if not isinstance(meta, dict):
        return None
    from app.clients.uid_client import normalize_old_hr_number
    return normalize_old_hr_number(meta.get("extracted_old_uid") or meta.get("ch_number"))


def _append_company_old_uid(db: Session, company: Any, old_uid: str) -> None:
    """Idempotently add an old HR number to a company's old_uids array.

    Best-effort: this row may be locked by another job's still-open transaction
    (e.g. a batch enrichment run mid-batch on the same company). Flushing inside
    a SAVEPOINT means a lock-wait timeout only discards this one write — not the
    rest of the current batch — and the pub still links via the caller's already-
    resolved (company_uid, company_id); a future run will simply re-cache old_uid.
    """
    existing = list(company.old_uids or [])
    if old_uid in existing:
        return
    from sqlalchemy.exc import OperationalError

    company.old_uids = existing + [old_uid]
    sp = db.begin_nested()
    try:
        db.flush()
        sp.commit()
    except OperationalError as exc:
        sp.rollback()
        logger.warning(
            "old_uids write timed out for company_id=%s (lock contention?), "
            "will retry on a future run: %s", company.id, exc,
        )


def _create_uid_stub_from_entity(
    db: Session, entity: dict, old_uid: str, stats: dict
) -> Any | None:
    """Create a shab_stub Company from a reverse-Search result (has the modern UID).

    Unlike the title-only stubs made during import, this stub carries the proper
    modern CHE-UID and any address/status the register returned, plus old_uid.
    """
    from app.models.company import Company as CompanyModel
    from sqlalchemy.exc import IntegrityError

    modern = entity.get("uid")
    if not modern:
        return None

    stub = CompanyModel(
        uid=modern,
        name=(entity.get("name") or modern)[:512],
        status=entity.get("status") or "CANCELLED",
        source="shab_stub",
        canton=(entity.get("canton") or "")[:8] or None,
        municipality=entity.get("municipality"),
        address=entity.get("address"),
        address_zip=entity.get("address_zip"),
        address_city=entity.get("address_city"),
        registration_type=entity.get("registration_type"),
        old_uids=[old_uid],
    )
    sp = db.begin_nested()
    try:
        db.add(stub)
        sp.commit()
    except IntegrityError:
        sp.rollback()
        from app import crud
        stub = crud.get_company_by_uid(db, modern)
        if stub is None:
            raise
        _append_company_old_uid(db, stub, old_uid)

    db.flush()
    stats["stubs_created"] = stats.get("stubs_created", 0) + 1
    return stub


def _apply_api_resolution(
    db: Session, old_uid: str, entity: dict | None, stats: dict
) -> tuple[str | None, int | None]:
    """Turn a reverse-Search entity into a (company_uid, company_id) link.

    Existing company → cache old_uid onto it. Unknown company → create a modern-UID
    shab_stub. Returns (None, None) when the register had no match.
    """
    if not entity or not entity.get("uid"):
        return (None, None)
    modern = entity["uid"]
    from app import crud
    company = crud.get_company_by_uid(db, modern)
    if company is not None:
        _append_company_old_uid(db, company, old_uid)
        return (company.uid, company.id)
    stub = _create_uid_stub_from_entity(db, entity, old_uid, stats)
    if stub is not None:
        return (stub.uid, stub.id)
    return (modern, None)


def _is_known_old_uid_miss(db: Session, old_uid: str) -> bool:
    """True if a prior run already confirmed the UID register has no match for this number.

    Best-effort: an OperationalError (lock contention) is treated as "not cached" so
    the caller falls through to a real API attempt rather than aborting the batch.
    """
    from sqlalchemy.exc import OperationalError

    sp = db.begin_nested()
    try:
        row = db.execute(
            text("SELECT 1 FROM shab_old_uid_misses WHERE old_uid = :u"),
            {"u": old_uid},
        ).fetchone()
        sp.commit()
    except OperationalError:
        sp.rollback()
        return False
    return row is not None


def _record_old_uid_miss(db: Session, old_uid: str) -> None:
    """Cache a confirmed "register has no match" result so future runs skip the API call.

    Keyed by the number, not the publication — the same old_uid is often cited by
    several publications (a company's later mutations).
    """
    db.execute(
        text(
            "INSERT INTO shab_old_uid_misses (old_uid, failed_at, attempts) "
            "VALUES (:u, now(), 1) "
            "ON CONFLICT (old_uid) DO UPDATE SET failed_at = now(), "
            "attempts = shab_old_uid_misses.attempts + 1"
        ),
        {"u": old_uid},
    )


def resolve_shab_old_uids(
    db: Session,
    *,
    resume_from: int = 0,  # advisory only; the company_uid IS NULL filter drives resume
    batch_size: int = 200,
    max_lookups: int | None = None,
    progress_cb=None,
    status_cb=None,
    abort_cb=None,
) -> dict[str, Any]:
    """Link pre-2014 SHAB publications that reference an OLD cantonal HR number.

    For every ``sogc_publications`` row with no ``company_uid`` but an old number in
    ``raw_json``: resolve the company by (1) local ``old_uids`` overlap — free —, on a
    miss (2) the ``shab_old_uid_misses`` cache of numbers a prior run already confirmed
    the register has no match for — also free — and only then (3) a reverse UID-register
    Search on ``otherOrganisationId`` (one API call per DISTINCT number, cached for the
    run). Resolved-via-API numbers are written back into the company's ``old_uids`` (and
    unknown companies get a proper modern-UID stub), so subsequent runs resolve them
    locally; confirmed non-matches are written to ``shab_old_uid_misses`` so subsequent
    runs don't re-spend the same slow, rate-limited API budget on numbers that will
    never resolve.

    Resumable/idempotent: linked rows drop out of the ``company_uid IS NULL`` set, so
    a re-run simply continues with what remains. ``max_lookups`` caps API calls per
    run (rate limit ~17/min); unresolved rows are left for a later run.
    """
    from app.clients.uid_client import search_by_old_uid

    stats: dict[str, Any] = {
        "pubs_scanned": 0,
        "with_old_uid": 0,
        "linked_local": 0,
        "resolved_api": 0,
        "api_lookups": 0,
        "skipped_cached_miss": 0,
        "unresolved": 0,
        "stubs_created": 0,
        "api_errors": 0,
        "budget_exhausted": False,
        "errors": [],
    }
    # old_uid -> (company_uid|None, company_id|None, source)
    resolved: dict[str, tuple[str | None, int | None, str]] = {}

    total: int = db.execute(
        text(
            "SELECT COUNT(*) FROM sogc_publications "
            "WHERE company_uid IS NULL AND raw_json IS NOT NULL"
        )
    ).scalar() or 0

    if status_cb:
        status_cb(f"Resolving old-UID SHAB publications — {total:,} unmatched to scan…")

    last_id = 0
    processed = 0

    while True:
        if abort_cb:
            abort_cb()

        rows = db.execute(
            text(
                "SELECT id, raw_json FROM sogc_publications "
                "WHERE company_uid IS NULL AND raw_json IS NOT NULL AND id > :last "
                "ORDER BY id ASC LIMIT :lim"
            ),
            {"last": last_id, "lim": batch_size},
        ).fetchall()

        if not rows:
            break

        for pub_id, raw_json in rows:
            last_id = pub_id
            processed += 1
            stats["pubs_scanned"] += 1

            old_uid = _old_uid_from_raw(raw_json)
            if not old_uid:
                continue
            stats["with_old_uid"] += 1

            if old_uid not in resolved:
                company = _get_company_by_old_uid(db, old_uid)
                if company is not None:
                    resolved[old_uid] = (company.uid, company.id, "local")
                elif _is_known_old_uid_miss(db, old_uid):
                    stats["skipped_cached_miss"] += 1
                    resolved[old_uid] = (None, None, "cached_miss")
                elif max_lookups is not None and stats["api_lookups"] >= max_lookups:
                    stats["budget_exhausted"] = True
                    continue  # defer to a later run
                else:
                    try:
                        entity = search_by_old_uid(old_uid, abort_cb=abort_cb)
                        stats["api_lookups"] += 1
                    except Exception as exc:  # noqa: BLE001
                        stats["api_errors"] += 1
                        stats["errors"].append(f"[{old_uid}] Search: {exc}")
                        continue
                    cu, ci = _apply_api_resolution(db, old_uid, entity, stats)
                    if cu is None:
                        # Confirmed non-match (not a transient error) — cache it.
                        _record_old_uid_miss(db, old_uid)
                    resolved[old_uid] = (cu, ci, "api")

            comp_uid, comp_id, src = resolved[old_uid]
            if comp_uid:
                db.execute(
                    text(
                        "UPDATE sogc_publications SET company_uid = :u, company_id = :c "
                        "WHERE id = :id"
                    ),
                    {"u": comp_uid, "c": comp_id, "id": pub_id},
                )
                stats["linked_local" if src == "local" else "resolved_api"] += 1
            else:
                stats["unresolved"] += 1

        db.commit()

        if progress_cb:
            progress_cb(processed, total, stats)

    return stats


# ── Targeted backfill: recover identifiers lost to historical extraction gaps ────

def backfill_shab_old_uid_extraction(
    db: Session,
    *,
    batch_size: int = 500,
    pdf_workers: int = 8,
    request_delay: float = 0.5,
    progress_cb=None,
    status_cb=None,
    abort_cb=None,
) -> dict[str, Any]:
    """Re-extract old_uid/uid for publications whose raw_json never captured one.

    Targeted, SOAP-free repair pass — NOT a full historical re-import. Some
    publications were imported by an older/buggier extraction pass (or hit a
    transient PDF-parse failure) and ended up with neither ``extracted_uid`` nor
    ``extracted_old_uid``/``ch_number`` in ``raw_json``. Those rows are invisible to
    ``resolve_shab_old_uids`` — there is nothing in them to look up — so they sit
    unresolved forever regardless of how well the reverse UID Search works.

    Re-fetches only the affected source PDFs: one ``archive_id`` fetch per Mode B
    row, one day fetch per distinct Mode A ``pub_date`` (entries on the same day
    share one PDF, so this is far cheaper than one-fetch-per-row), re-runs the
    CURRENT extraction functions, and updates ``raw_json``. When an identifier is
    recovered, immediately tries a free local ``companies.old_uids``/``uid`` match
    (same resolution path as a normal import) — a register (API) lookup for numbers
    still unmatched locally is left to ``resolve_shab_old_uids``'s next run; this
    job never calls the UID SOAP API itself.

    Rows still missing an identifier after a genuine re-check are stamped with
    ``old_uid_backfill_checked_at`` in ``raw_json`` so a future run doesn't re-fetch
    the same dead PDF indefinitely.
    """
    from app.clients.shab_archive_client import (
        fetch_pdf_bytes, extract_text_from_pdf, extract_uid_from_text,
        extract_old_uid_from_text, extract_canton_from_text,
        fetch_daily_pdf_bytes, parse_bulk_hr_entries,
    )
    from app.clients.uid_client import normalize_old_hr_number
    from app.models.sogc_publication import SogcPublication

    stats: dict[str, Any] = {
        "pubs_scanned": 0,
        "candidates_found": 0,
        "mode_b_refetched": 0,
        "mode_a_days_refetched": 0,
        "identifiers_recovered": 0,
        "linked_local": 0,
        "stubs_created": 0,
        "still_missing": 0,
        "errors": [],
    }

    total: int = db.execute(
        text("SELECT COUNT(*) FROM sogc_publications WHERE company_uid IS NULL AND raw_json IS NOT NULL")
    ).scalar() or 0

    if status_cb:
        status_cb(f"Scanning {total:,} unmatched publications for missing identifiers…")

    # ── Pass 1: find candidates. Python-side raw_json parse, same pattern as
    # resolve_shab_old_uids — raw_json is plain TEXT, no JSON index to filter on. ──
    mode_b: list[tuple[int, str]] = []                        # (pub_id, archive_id)
    mode_a: dict[str, list[tuple[int, str | None]]] = {}       # pub_date -> [(pub_id, pub_number), ...]

    last_id = 0
    scanned = 0
    while True:
        if abort_cb:
            abort_cb()
        rows = db.execute(
            text(
                "SELECT id, raw_json, pub_number, pub_date FROM sogc_publications "
                "WHERE company_uid IS NULL AND raw_json IS NOT NULL AND id > :last "
                "ORDER BY id ASC LIMIT :lim"
            ),
            {"last": last_id, "lim": batch_size},
        ).fetchall()
        if not rows:
            break

        for pub_id, raw_json, pub_number, pub_date in rows:
            last_id = pub_id
            scanned += 1
            stats["pubs_scanned"] += 1

            try:
                meta = json.loads(raw_json)
            except Exception:
                continue
            if not isinstance(meta, dict):
                continue
            if meta.get("old_uid_backfill_checked_at"):
                continue  # already re-checked by a prior backfill run, genuinely empty
            if meta.get("extracted_uid") or meta.get("extracted_old_uid") or meta.get("ch_number"):
                continue  # already has something for resolve_shab_old_uids to use

            source = meta.get("source")
            if source == "shab_archive" and meta.get("archive_id"):
                mode_b.append((pub_id, str(meta["archive_id"])))
                stats["candidates_found"] += 1
            elif source == "shab_old_pdf" and pub_date:
                mode_a.setdefault(pub_date, []).append((pub_id, pub_number))
                stats["candidates_found"] += 1

        if progress_cb:
            progress_cb(scanned, total, stats)

    mode_a_row_count = sum(len(v) for v in mode_a.values())
    if status_cb:
        status_cb(
            f"Scan done — {stats['candidates_found']:,} candidates "
            f"({len(mode_b):,} Mode B, {mode_a_row_count:,} Mode A across "
            f"{len(mode_a):,} days). Re-fetching…"
        )

    total_candidates = len(mode_b) + mode_a_row_count
    done = 0

    def _finalize_row(
        pub_id: int, meta: dict,
        found_uid: str | None, found_old_uid: str | None, found_canton: str | None,
    ) -> None:
        nonlocal done
        done += 1
        if found_uid:
            meta["extracted_uid"] = found_uid
        if found_old_uid:
            meta["extracted_old_uid"] = found_old_uid
        if found_canton and not meta.get("canton"):
            meta["canton"] = found_canton

        pub = db.get(SogcPublication, pub_id)
        if pub is None:
            return

        if found_uid or found_old_uid:
            stats["identifiers_recovered"] += 1
            comp_uid, comp_id = _resolve_company_for_shab(
                db, found_uid, meta.get("title"), found_canton or meta.get("canton"), pub.pub_date,
                None, stats, old_uid=found_old_uid, merge_old_name=True,
            )
            if comp_uid:
                pub.company_uid = comp_uid
                pub.company_id = comp_id
                stats["linked_local"] += 1
        else:
            stats["still_missing"] += 1
            meta["old_uid_backfill_checked_at"] = datetime.now(tz=timezone.utc).isoformat()

        pub.raw_json = json.dumps(meta)
        db.flush()

    # ── Mode B — one PDF per affected row, fetched concurrently in chunks ────────
    chunk_size = max(pdf_workers * 5, batch_size)
    for i in range(0, len(mode_b), chunk_size):
        if abort_cb:
            abort_cb()
        chunk = mode_b[i : i + chunk_size]

        def _dl(item: tuple[int, str]) -> tuple[int, str, bytes | None]:
            pub_id, archive_id = item
            try:
                return pub_id, archive_id, fetch_pdf_bytes(archive_id)
            except Exception as exc:  # noqa: BLE001
                stats["errors"].append(f"[archive_id={archive_id}] PDF fetch failed: {exc}")
                return pub_id, archive_id, None

        with ThreadPoolExecutor(max_workers=min(pdf_workers, len(chunk))) as pool:
            results = list(pool.map(_dl, chunk))

        for pub_id, _archive_id, pdf_bytes in results:
            if abort_cb:
                abort_cb()
            stats["mode_b_refetched"] += 1
            pub = db.get(SogcPublication, pub_id)
            if pub is None or not pdf_bytes:
                continue
            try:
                meta = json.loads(pub.raw_json) if pub.raw_json else {}
            except Exception:
                meta = {}
            raw_text = extract_text_from_pdf(pdf_bytes)
            found_uid = extract_uid_from_text(raw_text) if raw_text else None
            found_old_uid = extract_old_uid_from_text(raw_text) if raw_text else None
            found_canton = extract_canton_from_text(raw_text) if raw_text else None
            _finalize_row(pub_id, meta, found_uid, found_old_uid, found_canton)

        db.commit()
        if progress_cb:
            progress_cb(done, total_candidates, stats)

    # ── Mode A — one PDF per affected day, entries matched back by pub_number ────
    for day_idx, (day_str, entries) in enumerate(mode_a.items()):
        if abort_cb:
            abort_cb()
        try:
            pdf_bytes = fetch_daily_pdf_bytes(day_str)
        except Exception as exc:  # noqa: BLE001
            stats["errors"].append(f"[{day_str}] PDF fetch failed: {exc}")
            pdf_bytes = None

        stats["mode_a_days_refetched"] += 1

        parsed_by_number: dict[str, dict] = {}
        if pdf_bytes:
            raw_text = extract_text_from_pdf(pdf_bytes)
            if raw_text:
                try:
                    day_entries = parse_bulk_hr_entries(raw_text, day_str)
                    parsed_by_number = {e["pub_number"]: e for e in day_entries}
                except Exception as exc:  # noqa: BLE001
                    stats["errors"].append(f"[{day_str}] parse_bulk_hr_entries failed: {exc}")

        for pub_id, pub_number in entries:
            if abort_cb:
                abort_cb()
            pub = db.get(SogcPublication, pub_id)
            if pub is None:
                continue
            try:
                meta = json.loads(pub.raw_json) if pub.raw_json else {}
            except Exception:
                meta = {}

            entry = parsed_by_number.get(pub_number) if pub_number else None
            found_old_uid = normalize_old_hr_number(entry.get("ch_number")) if entry else None
            if not found_old_uid and entry and entry.get("text"):
                found_old_uid = extract_old_uid_from_text(entry["text"])
            found_canton = entry.get("canton") if entry else None
            _finalize_row(pub_id, meta, None, found_old_uid, found_canton)

        db.commit()
        if progress_cb:
            progress_cb(done, total_candidates, stats)

        if day_idx < len(mode_a) - 1:
            time.sleep(request_delay)

    return stats
