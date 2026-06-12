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

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_SOGC_ID_PREFIX = "shab_"


def _make_sogc_id(archive_id: int | str) -> str:
    return f"{_SOGC_ID_PREFIX}{archive_id}"


def _detect_lang(text: str, canton: str | None) -> str:
    if text:
        try:
            from app.services.language_detection import detect_purpose_language
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
        extract_canton_from_text,
        is_hr_heading,
        parse_entry_fields,
    )
    from app.models.sogc_publication import SogcPublication
    from app.models.sogc_change import SogcChange
    from app.services.sogc_preprocessor import _detect_changes

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
            if uid and not pub.company_uid:
                company = (uid_map or {}).get(uid) if uid_map is not None else None
                if company is None and uid_map is None:
                    from app import crud
                    company = crud.get_company_by_uid(db, uid)
                if company:
                    pub.company_uid = uid
                    pub.company_id = company.id
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
            company_uid = None
            company_id = None
            if uid:
                company = (uid_map or {}).get(uid) if uid_map is not None else None
                if company is None and uid_map is None:
                    from app import crud
                    company = crud.get_company_by_uid(db, uid)
                if company:
                    company_uid = uid
                    company_id = company.id
            pub = SogcPublication(
                sogc_id=sogc_id,
                company_uid=company_uid,
                company_id=company_id,
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
        fetch_archive_page, get_entries, get_total_elements,
        is_last_page, extract_text_from_pdf, normalize_pdf_text,
        extract_uid_from_text, parse_entry_fields, is_hr_heading,
    )
    from app.models.company import Company

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

            # ── Batch UID lookup ──────────────────────────────────────────────
            # Extract UIDs from every successfully-downloaded PDF so we can do
            # a single IN query instead of one per entry.
            uids: set[str] = set()
            for aid, pdf_bytes in pdf_cache.items():
                if pdf_bytes:
                    raw = extract_text_from_pdf(pdf_bytes)
                    uid = extract_uid_from_text(raw) if raw else None
                    if uid:
                        uids.add(uid)

            if uids:
                companies = (
                    db.query(Company)
                    .filter(Company.uid.in_(uids))
                    .all()
                )
                uid_map = {c.uid: c for c in companies}
            else:
                uid_map = {}

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
                from app.clients.shab_archive_client import (
                    extract_text_from_pdf as _etf, extract_uid_from_text as _euid,
                )
                from app.models.company import Company as _Company
                uids_p: set[str] = set()
                for _aid, _pb in pdf_cache_p.items():
                    if _pb:
                        _raw = _etf(_pb)
                        _uid = _euid(_raw) if _raw else None
                        if _uid:
                            uids_p.add(_uid)
                if uids_p:
                    uid_map_p = {
                        c.uid: c
                        for c in db.query(_Company).filter(_Company.uid.in_(uids_p)).all()
                    }
                else:
                    uid_map_p = {}

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
