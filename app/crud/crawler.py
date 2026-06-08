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
    """Mark the highest-scoring pending candidate as 'selected'.

    Any existing 'selected' row for this company is demoted back to 'pending'.
    Returns the newly selected candidate, or None if no pending candidates exist.
    """
    # Demote current selection
    db.query(CompanyUrlCandidate).filter_by(company_id=company_id, status="selected").update(
        {"status": "pending"}, synchronize_session=False
    )
    best = (
        db.query(CompanyUrlCandidate)
        .filter_by(company_id=company_id, status="pending")
        .order_by(CompanyUrlCandidate.score.desc().nullslast())
        .first()
    )
    if best:
        best.status = "selected"
        db.flush()
    return best


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


def claim_crawl_batch(
    db: Session,
    *,
    tier: str,
    batch_size: int = 20,
    canton: str | None = None,
) -> list[CompanyCrawlState]:
    """Atomically claim a batch of crawl states via SELECT FOR UPDATE SKIP LOCKED.

    HTTP tier:       picks up crawl_status='pending' AND tier='http'
    Playwright tier: picks up crawl_status='pending' AND tier='playwright'
                     PLUS crawl_status='js_required' (escalated by HTTP workers)
    """
    now = datetime.now(timezone.utc)

    canton_join = ""
    canton_clause = ""
    if canton:
        canton_join = "JOIN companies c ON c.id = cs.company_id"
        canton_clause = "AND c.canton = :canton"

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
        {canton_join}
        WHERE {status_clause}
          AND (cs.next_crawl_at IS NULL OR cs.next_crawl_at <= :now)
          AND cs.selected_url_id IS NOT NULL
        ORDER BY cs.company_id
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
