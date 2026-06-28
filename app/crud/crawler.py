"""CRUD helpers for web crawler tables.

Three tables:
  company_url_candidates  — Serper.dev URL candidates per company
  company_crawl_state     — per-company crawl control (status, tier, bot flags)
  company_web_pages       — per-page crawl results with S3 references
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.company_crawl_state import CompanyCrawlState
from app.models.company_url_candidate import CompanyUrlCandidate
from app.models.company_web_extract import CompanyWebExtract
from app.models.company_web_page import CompanyWebPage

logger = logging.getLogger(__name__)


# ── URL candidates ─────────────────────────────────────────────────────────────

def upsert_url_candidates(
    db: Session,
    company_id: int,
    candidates: list[dict[str, Any]],
) -> list[CompanyUrlCandidate]:
    """Insert or update URL candidates from google_search_results_raw JSON.

    Each dict must have 'link' (url); optional: 'title', 'snippet', 'score', 'position'.
    Returns the upserted rows sorted by score descending.
    Uses ON CONFLICT DO UPDATE so re-runs and concurrent jobs never raise UniqueViolation.
    """
    now = datetime.now(timezone.utc)
    rows = []
    for cand in candidates:
        url = cand.get("link") or cand.get("url")
        if not url:
            continue
        rows.append({
            "company_id": company_id,
            "url": url,
            "title": cand.get("title"),
            "snippet": cand.get("snippet"),
            "score": cand.get("score"),
            "position": cand.get("position"),
            "status": "pending",
            "source": "serper",
            "first_seen_at": now,
        })

    if not rows:
        return []

    stmt = pg_insert(CompanyUrlCandidate).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["company_id", "url"],
        set_={
            "score": stmt.excluded.score,
            "title": stmt.excluded.title,
            "snippet": stmt.excluded.snippet,
            "position": stmt.excluded.position,
        },
    ).returning(CompanyUrlCandidate)
    result = db.execute(stmt)
    db.flush()
    upserted = list(result.scalars())
    return sorted(upserted, key=lambda r: (r.score or 0), reverse=True)


def select_best_candidate(db: Session, company_id: int) -> CompanyUrlCandidate | None:
    """Mark the highest-scoring non-blocked pending candidate as 'selected'.

    Any existing 'selected' row for this company is demoted back to 'pending'.
    Directory sites and social media (CRAWL_BLOCKED_DOMAINS) are skipped so the
    crawler never tries to scrape moneyhouse.ch, LinkedIn, etc. as company websites.
    Returns the newly selected candidate, or None if no crawlable candidates exist.
    """
    from app.services.scoring import CRAWL_BLOCKED_DOMAINS

    # Demote current selection
    db.query(CompanyUrlCandidate).filter_by(company_id=company_id, status="selected").update(
        {"status": "pending"}, synchronize_session=False
    )
    candidates = (
        db.query(CompanyUrlCandidate)
        .filter_by(company_id=company_id, status="pending")
        .order_by(CompanyUrlCandidate.score.desc().nullslast())
        .all()
    )
    for best in candidates:
        if not is_crawl_blocked(best.url, CRAWL_BLOCKED_DOMAINS):
            best.status = "selected"
            db.flush()
            return best
    return None


def switch_selected_candidate(
    db: Session,
    company_id: int,
    url_candidate_id: int,
) -> CompanyUrlCandidate | None:
    """Promote a specific candidate to 'selected', demoting the current one.

    Also resets the company_crawl_state to pending so it gets re-crawled.
    Returns the newly selected candidate or None if not found.
    """
    target = db.get(CompanyUrlCandidate, url_candidate_id)
    if not target or target.company_id != company_id:
        return None
    # Demote current selection
    db.query(CompanyUrlCandidate).filter_by(company_id=company_id, status="selected").update(
        {"status": "pending"}, synchronize_session=False
    )
    target.status = "selected"
    db.flush()
    # Reset crawl state so the new URL gets picked up
    state = db.get(CompanyCrawlState, company_id)
    if state:
        state.selected_url_id = url_candidate_id
        state.crawl_status = "pending"
        state.tier = "http"
        state.bot_protected = False
        state.bot_protection_type = None
        state.pages_crawled = None
        state.crawl_error_detail = None
        state.consecutive_failures = 0
        db.flush()
    return target


# ── Crawl state ────────────────────────────────────────────────────────────────

def get_or_create_crawl_state(
    db: Session,
    company_id: int,
    selected_url_id: int | None = None,
) -> CompanyCrawlState:
    """Return existing crawl state or create a new pending one."""
    state = db.get(CompanyCrawlState, company_id)
    if state is None:
        state = CompanyCrawlState(
            company_id=company_id,
            selected_url_id=selected_url_id,
            crawl_status="pending",
            tier="http",
        )
        db.add(state)
        db.flush()
    elif selected_url_id and state.selected_url_id is None:
        state.selected_url_id = selected_url_id
        db.flush()
    return state


_CRAWL_ORDER_BY: dict[str, str] = {
    "company_id_asc":      "cs.company_id ASC",
    "last_crawled_asc":    "cs.last_crawled_at ASC NULLS FIRST",
    "flex_score_desc":     "c.flex_score DESC NULLS LAST, cs.company_id ASC",
    "combined_score_desc": "c.combined_score DESC NULLS LAST, cs.company_id ASC",
}


def reset_http_crawled(db: Session, *, canton: str | None = None) -> int:
    """Reset all terminal HTTP-tier rows (including js_required escalations) back to pending.

    Returns the number of rows updated.
    """
    canton_clause = ""
    params: dict[str, Any] = {}
    if canton:
        canton_clause = (
            "AND company_id IN "
            "(SELECT id FROM companies WHERE canton = :canton)"
        )
        params["canton"] = canton

    result = db.execute(
        text(
            f"UPDATE company_crawl_state "  # noqa: S608
            f"SET crawl_status = 'pending', tier = 'http', "
            f"    crawl_error_detail = NULL, bot_protected = FALSE, "
            f"    bot_protection_type = NULL "
            f"WHERE crawl_status IN ('crawled', 'bot_blocked', 'http_error', 'timeout', 'no_content', 'js_required') "
            f"{canton_clause}"
        ),
        params,
    )
    db.flush()
    return result.rowcount


def reset_playwright_crawled(db: Session, *, canton: str | None = None) -> int:
    """Reset crawled/failed playwright-tier rows back to pending so they can be re-crawled.

    Returns the number of rows updated.
    """
    canton_clause = ""
    params: dict[str, Any] = {}
    if canton:
        canton_clause = (
            "AND company_id IN "
            "(SELECT id FROM companies WHERE canton = :canton)"
        )
        params["canton"] = canton

    result = db.execute(
        text(
            f"UPDATE company_crawl_state "  # noqa: S608
            f"SET crawl_status = 'pending', tier = 'playwright', "
            f"    crawl_error_detail = NULL, bot_protected = FALSE, "
            f"    bot_protection_type = NULL "
            f"WHERE tier = 'playwright' "
            f"  AND crawl_status IN ('crawled', 'bot_blocked', 'http_error', 'timeout', 'no_content') "
            f"{canton_clause}"
        ),
        params,
    )
    db.flush()
    return result.rowcount


def claim_crawl_batch(
    db: Session,
    *,
    tier: str,
    batch_size: int = 20,
    canton: str | None = None,
    order_by: str = "company_id_asc",
) -> list[CompanyCrawlState]:
    """Atomically claim a batch of crawl states via SELECT FOR UPDATE SKIP LOCKED.

    HTTP tier:       picks up crawl_status='pending' AND tier='http'
    Playwright tier: picks up crawl_status='pending' AND tier='playwright'
                     PLUS crawl_status='js_required' (escalated by HTTP workers)
    """
    now = datetime.now(timezone.utc)

    order_clause = _CRAWL_ORDER_BY.get(order_by, "cs.company_id ASC")
    needs_company_join = order_by in ("flex_score_desc", "combined_score_desc")

    if canton:
        join_clause = "JOIN companies c ON c.id = cs.company_id"
        canton_clause = "AND c.canton = :canton"
    elif needs_company_join:
        join_clause = "JOIN companies c ON c.id = cs.company_id"
        canton_clause = ""
    else:
        join_clause = ""
        canton_clause = ""

    if tier == "playwright":
        # js_required rows have crawl_status='js_required', NOT 'pending', so
        # they must be matched with a separate OR branch — not by AND-ing with
        # the 'pending' filter that applies to explicit playwright-tier rows.
        status_clause = (
            "("
            "  (cs.crawl_status = 'pending' AND cs.tier = 'playwright')"
            "  OR cs.crawl_status = 'js_required'"
            ")"
        )
        params: dict[str, Any] = {"now": now, "limit": batch_size}
    else:
        status_clause = "cs.crawl_status = 'pending' AND cs.tier = :tier"
        params = {"now": now, "limit": batch_size, "tier": tier}

    if canton:
        params["canton"] = canton

    sql = text(f"""
        SELECT cs.company_id FROM company_crawl_state cs
        {join_clause}
        WHERE {status_clause}
          AND (cs.next_crawl_at IS NULL OR cs.next_crawl_at <= :now)
          AND cs.selected_url_id IS NOT NULL
          {canton_clause}
        ORDER BY {order_clause}
        LIMIT :limit
        FOR UPDATE SKIP LOCKED
    """)  # noqa: S608

    rows = db.execute(sql, params).fetchall()
    if not rows:
        return []

    company_ids = [r[0] for r in rows]
    states = (
        db.query(CompanyCrawlState)
        .filter(CompanyCrawlState.company_id.in_(company_ids))
        .order_by(CompanyCrawlState.company_id)  # deterministic order prevents lock-inversion deadlock
        .all()
    )
    for s in states:
        s.crawl_status = "in_progress"
    db.flush()
    return states


def mark_crawl_done(
    db: Session,
    state: CompanyCrawlState,
    pages_crawled: list[str],
) -> None:
    now = datetime.now(timezone.utc)
    state.crawl_status = "crawled"
    state.pages_crawled = pages_crawled
    state.last_crawled_at = now
    state.consecutive_failures = 0
    state.crawl_error_detail = None
    db.flush()


_TERMINAL_CRAWL_STATUSES = frozenset({"bot_blocked", "http_error", "timeout", "no_content"})


def mark_crawl_failed(
    db: Session,
    state: CompanyCrawlState,
    status: str,
    detail: str | None = None,
    bot_protection_type: str | None = None,
) -> None:
    """Set a specific failure status on a crawl state.

    status: bot_blocked | js_required | http_error | timeout | no_content
    """
    now = datetime.now(timezone.utc)
    state.crawl_status = status
    state.last_crawled_at = now
    state.consecutive_failures = (state.consecutive_failures or 0) + 1
    state.crawl_error_detail = detail
    if status == "bot_blocked":
        state.bot_protected = True
        state.bot_protection_type = bot_protection_type or "unknown"
    if status == "js_required":
        state.tier = "playwright"
    db.flush()

    if status in _TERMINAL_CRAWL_STATUSES:
        try:
            from app.crud.company_error import log_error as _log_err
            _log_err(
                db,
                company_id=state.company_id,
                source="crawler",
                error_type=status,
                message=detail,
            )
        except Exception:  # noqa: BLE001
            pass


def schedule_crawl_retry(
    db: Session,
    state: CompanyCrawlState,
    status: str,
    detail: str | None = None,
    *,
    base_delay_minutes: int = 30,
) -> None:
    """Reschedule a transient failure for a later retry instead of failing terminally.

    Keeps crawl_status='pending' (so the same tier re-claims it) but sets
    next_crawl_at into the future using exponential backoff on consecutive_failures.
    The claim query already filters on next_crawl_at, so the row is skipped until
    the backoff elapses. Caller decides when retries are exhausted.
    """
    from datetime import timedelta

    now = datetime.now(timezone.utc)
    state.consecutive_failures = (state.consecutive_failures or 0) + 1
    backoff = base_delay_minutes * (2 ** (state.consecutive_failures - 1))
    state.crawl_status = "pending"
    state.last_crawled_at = now
    state.next_crawl_at = now + timedelta(minutes=backoff)
    state.crawl_error_detail = f"retry ({status}): {detail}" if detail else f"retry ({status})"
    db.flush()


def reset_crawl_for_recrawl(db: Session, state: CompanyCrawlState) -> None:
    """Reset a terminal crawl state back to pending for a re-crawl attempt."""
    state.crawl_status = "pending"
    state.tier = "http"
    state.bot_protected = False
    state.bot_protection_type = None
    state.crawl_error_detail = None
    state.next_crawl_at = None
    db.flush()


def release_in_progress_states(db: Session, tier: str) -> int:
    """Reset company_crawl_state rows stuck in 'in_progress' back to 'pending'.

    Called at the start of each crawl job run (covers resume-after-crash) and on
    job failure/pause, so pods dying mid-batch don't permanently strand companies.
    For 'playwright' tier, also releases rows where tier='playwright' since those
    were escalated by the HTTP crawler before being claimed.
    Returns the number of rows released.
    """
    if tier == "playwright":
        tier_clause = "tier IN ('http', 'playwright')"
    else:
        tier_clause = "tier = :tier"

    result = db.execute(
        text(
            f"UPDATE company_crawl_state SET crawl_status = 'pending' "  # noqa: S608
            f"WHERE crawl_status = 'in_progress' AND {tier_clause}"
        ),
        {"tier": tier},
    )
    db.flush()
    return result.rowcount


# ── Web pages ──────────────────────────────────────────────────────────────────

def delete_web_pages_for_company(db: Session, company_id: int) -> int:
    """Delete existing web page rows for a company before a fresh crawl.

    Keeps DB and S3 in sync: since S3 keys are keyed by (company_id, page_type)
    without a timestamp, a re-crawl overwrites the S3 file. Old rows pointing
    to the same key would mislead the extraction job. Returns rows deleted.
    """
    result = db.execute(
        text("DELETE FROM company_web_pages WHERE company_id = :cid"),
        {"cid": company_id},
    )
    db.flush()
    return result.rowcount


def save_web_page(
    db: Session,
    *,
    company_id: int,
    url_candidate_id: int | None,
    page_type: str,
    url: str,
    final_url: str | None = None,
    crawled_at: datetime,
    http_status: int | None = None,
    lang: str | None = None,
    word_count: int | None = None,
    image_count: int | None = None,
    video_count: int | None = None,
    has_contact_form: bool | None = None,
    s3_key_html: str | None = None,
    bot_blocked: bool = False,
) -> CompanyWebPage:
    page = CompanyWebPage(
        company_id=company_id,
        url_candidate_id=url_candidate_id,
        page_type=page_type,
        url=url,
        final_url=final_url,
        crawled_at=crawled_at,
        http_status=http_status,
        lang=lang,
        word_count=word_count,
        image_count=image_count,
        video_count=video_count,
        has_contact_form=has_contact_form,
        s3_key_html=s3_key_html,
        bot_blocked=bot_blocked,
        needs_extraction=not bot_blocked,
    )
    db.add(page)
    db.flush()
    return page


# ── Web extraction (web_extract job) ────────────────────────────────────────────

def count_companies_pending_extraction(db: Session) -> int:
    """Number of distinct companies with at least one page awaiting extraction."""
    return int(db.execute(
        text(
            "SELECT COUNT(DISTINCT company_id) FROM company_web_pages "
            "WHERE needs_extraction = TRUE AND s3_key_html IS NOT NULL"
        )
    ).scalar() or 0)


def claim_companies_for_extraction(db: Session, batch_size: int = 200) -> list[int]:
    """Return distinct company_ids that have unextracted pages with stored HTML.

    web_extract is deduplicated (one active job per org), so no row-level locking
    is needed — a single worker drains the queue. Ordered by company_id for
    deterministic, resumable progress.
    """
    rows = db.execute(
        text(
            "SELECT DISTINCT company_id FROM company_web_pages "
            "WHERE needs_extraction = TRUE AND s3_key_html IS NOT NULL "
            "ORDER BY company_id "
            "LIMIT :limit"
        ),
        {"limit": batch_size},
    ).fetchall()
    return [r[0] for r in rows]


def get_extractable_pages(db: Session, company_id: int) -> list[CompanyWebPage]:
    """All pages for a company that have stored HTML and still need extraction."""
    return (
        db.query(CompanyWebPage)
        .filter(
            CompanyWebPage.company_id == company_id,
            CompanyWebPage.needs_extraction.is_(True),
            CompanyWebPage.s3_key_html.isnot(None),
        )
        .all()
    )


def upsert_web_extract(db: Session, company_id: int, url_candidate_id: int, data: dict) -> None:
    """Insert or update the extraction row for a (company, URL candidate) pair."""
    now = datetime.now(timezone.utc)
    values = {
        "company_id": company_id,
        "url_candidate_id": url_candidate_id,
        "extracted_at": now,
        **data,
    }
    stmt = pg_insert(CompanyWebExtract).values(**values)
    update_cols = {k: getattr(stmt.excluded, k) for k in data}
    update_cols["extracted_at"] = stmt.excluded.extracted_at
    stmt = stmt.on_conflict_do_update(
        index_elements=["company_id", "url_candidate_id"],
        set_=update_cols,
    )
    db.execute(stmt)
    db.flush()


def get_best_web_extract(db: Session, company_id: int) -> "CompanyWebExtract | None":
    """Return the highest-confidence extraction result for a company.

    When a company has been crawled via multiple URL candidates, picks the row
    with the highest confidence score. Ties are broken by most recent extracted_at.
    """
    return (
        db.query(CompanyWebExtract)
        .filter(CompanyWebExtract.company_id == company_id)
        .order_by(
            CompanyWebExtract.confidence.desc().nulls_last(),
            CompanyWebExtract.extracted_at.desc(),
        )
        .first()
    )


def get_web_extracts_with_urls(db: Session, company_id: int):
    """Return all crawl extracts for a company joined to their candidate URL.

    Each row exposes: url, confidence, uid_matches_zefix, name_address_verified,
    purpose_sim (nullable — set by enrich_web_purpose_sim ML-worker job).
    Used by website_status.compute_verdict to aggregate per-candidate verification
    into a company-level website verdict + distinct-domain count.
    """
    return db.execute(
        text(
            "SELECT c.url AS url, e.confidence AS confidence, "
            "e.uid_matches_zefix AS uid_matches_zefix, "
            "e.name_address_verified AS name_address_verified, "
            "e.purpose_sim AS purpose_sim "
            "FROM company_web_extract e "
            "JOIN company_url_candidates c ON c.id = e.url_candidate_id "
            "WHERE e.company_id = :cid"
        ),
        {"cid": company_id},
    ).fetchall()


def reset_extraction_flags(db: Session) -> int:
    """Flag every crawled page that has stored HTML for re-extraction.

    Lets the extractor be improved and re-run against HTML already in S3 — no
    re-crawl, no network cost. Pages already pending stay pending. Returns the
    number of newly-flagged (previously extracted) pages.
    """
    result = db.execute(
        text(
            "UPDATE company_web_pages SET needs_extraction = TRUE "
            "WHERE s3_key_html IS NOT NULL AND needs_extraction = FALSE"
        )
    )
    db.flush()
    return result.rowcount


def mark_pages_extracted(db: Session, company_id: int) -> int:
    """Flip needs_extraction=FALSE + stamp extracted_at for a company's pages."""
    now = datetime.now(timezone.utc)
    result = db.execute(
        text(
            "UPDATE company_web_pages "
            "SET needs_extraction = FALSE, extracted_at = :now "
            "WHERE company_id = :cid AND needs_extraction = TRUE"
        ),
        {"now": now, "cid": company_id},
    )
    db.flush()
    return result.rowcount


# ── Job queue helpers ──────────────────────────────────────────────────────────

def has_queued_ml_job(db: Session, ml_types: set[str]) -> bool:
    """Return True if any ML job type is currently queued.

    Used by crawler progress callbacks to self-preempt and yield to ML jobs.
    Single SELECT 1 — negligible overhead.
    """
    if not ml_types:
        return False
    result = db.execute(
        text(
            "SELECT 1 FROM job_runs "
            "WHERE status = 'queued' AND job_type = ANY(:types) "
            "LIMIT 1"
        ),
        {"types": list(ml_types)},
    ).fetchone()
    return result is not None


def get_crawl_state(db: Session, company_id: int) -> CompanyCrawlState | None:
    return db.get(CompanyCrawlState, company_id)


def get_selected_candidate(db: Session, company_id: int) -> CompanyUrlCandidate | None:
    return (
        db.query(CompanyUrlCandidate)
        .filter_by(company_id=company_id, status="selected")
        .first()
    )


def parse_google_results_raw(raw_json: str) -> list[dict[str, Any]]:
    """Parse google_search_results_raw JSON into a list of candidate dicts."""
    try:
        return json.loads(raw_json) if raw_json else []
    except (ValueError, TypeError):
        return []


# ── Domain-level crawl filter ──────────────────────────────────────────────────

def _extract_apex_domain(url: str) -> str:
    """Return 'example.ch' from any URL. Empty string on parse error."""
    try:
        from urllib.parse import urlparse as _up
        host = _up(url).hostname or ""
        parts = host.lstrip("www.").split(".")
        return ".".join(parts[-2:]) if len(parts) >= 2 else host
    except Exception:  # noqa: BLE001
        return ""


def is_crawl_blocked(url: str, blocked: frozenset[str] | set[str] | None = None) -> bool:
    """Return True if the URL's domain is on the crawl blocklist.

    Uses the module-level CRAWL_BLOCKED_DOMAINS set by default (directory sites +
    social media). Pass a custom set to override (e.g. with DB-managed entries merged in).
    """
    if blocked is None:
        from app.services.scoring import CRAWL_BLOCKED_DOMAINS
        blocked = CRAWL_BLOCKED_DOMAINS
    domain = _extract_apex_domain(url)
    return bool(domain and domain in blocked)


def get_next_crawlable_candidate(
    db: Session,
    company_id: int,
    exclude_candidate_ids: set[int],
    blocked: frozenset[str] | set[str] | None = None,
) -> CompanyUrlCandidate | None:
    """Return the next-best URL candidate eligible for a fallback crawl.

    Skips: already-tried candidates (exclude_candidate_ids), candidates that
    already have pages in S3 (meaning a crawl was attempted), and domains on
    the crawl blocklist. Returns the highest-score remaining candidate, or None.
    """
    if blocked is None:
        from app.services.scoring import CRAWL_BLOCKED_DOMAINS
        blocked = CRAWL_BLOCKED_DOMAINS

    # Candidates that already have at least one page saved (attempted before)
    attempted_subq = (
        db.query(CompanyWebPage.url_candidate_id)
        .filter(CompanyWebPage.company_id == company_id)
        .subquery()
    )
    candidates = (
        db.query(CompanyUrlCandidate)
        .filter(
            CompanyUrlCandidate.company_id == company_id,
            CompanyUrlCandidate.status.in_(["pending", "selected"]),
            ~CompanyUrlCandidate.id.in_(db.query(attempted_subq.c.url_candidate_id)),
        )
        .order_by(CompanyUrlCandidate.score.desc().nullslast())
        .all()
    )
    for c in candidates:
        if c.id in exclude_candidate_ids:
            continue
        if is_crawl_blocked(c.url, blocked):
            continue
        return c
    return None


def reject_url_candidate(db: Session, url_candidate_id: int) -> None:
    """Mark a URL candidate as rejected (wrong site, UID mismatch, etc.)."""
    db.query(CompanyUrlCandidate).filter(CompanyUrlCandidate.id == url_candidate_id).update(
        {"status": "rejected"}, synchronize_session=False
    )
    db.flush()


# ── Domain frequency analysis ──────────────────────────────────────────────────

def get_high_frequency_candidate_domains(
    db: Session,
    min_companies: int = 50,
    limit: int = 100,
) -> list[dict]:
    """Return hostnames that appear as URL candidates for many distinct companies.

    Useful for surfacing new directory/aggregator domains that should be added
    to the crawl blocklist. Hostname is the bare domain (www. stripped).
    """
    rows = db.execute(
        text(
            """
            SELECT
                regexp_replace(
                    split_part(
                        regexp_replace(lower(url), '^https?://', ''),
                        '/', 1
                    ),
                    '^www\\.', ''
                ) AS hostname,
                count(distinct company_id) AS company_count
            FROM company_url_candidates
            WHERE url IS NOT NULL AND url <> ''
            GROUP BY 1
            HAVING count(distinct company_id) >= :min_companies
            ORDER BY company_count DESC
            LIMIT :limit
            """
        ),
        {"min_companies": min_companies, "limit": limit},
    ).fetchall()
    return [{"domain": str(r[0]), "company_count": int(r[1])} for r in rows]


# ── Cross-company UID attribution ──────────────────────────────────────────────

def add_cross_attributed_url_candidate(
    db: Session,
    company_id: int,
    url: str,
    score: float = 0.5,
) -> CompanyUrlCandidate | None:
    """Add a URL candidate for a company discovered via cross-UID attribution.

    Called when web_extract finds a UID on a page that matches a *different*
    company than the one being extracted. The URL is added to the other company
    as a low-score candidate (not auto-selected) so it can be crawled later.
    Does nothing if the URL already exists for this company.
    """
    existing = (
        db.query(CompanyUrlCandidate)
        .filter(CompanyUrlCandidate.company_id == company_id, CompanyUrlCandidate.url == url)
        .first()
    )
    if existing:
        return existing
    candidate = CompanyUrlCandidate(
        company_id=company_id,
        url=url,
        score=score,
        status="pending",
        title="[cross-UID attribution]",
        snippet="Added automatically: UID found on this URL matched this company during another company's crawl.",
    )
    db.add(candidate)
    db.flush()
    return candidate
