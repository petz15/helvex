"""CRUD helpers for web crawler tables.

Three tables:
  company_url_candidates  — Serper.dev URL candidates per company
  company_crawl_state     — per-company crawl control (status, tier, bot flags)
  company_web_pages       — per-page crawl results with S3 references
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import bindparam as sa_bindparam, text
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


# Apex domain (last two labels) of a URL, in SQL. Must agree with
# _extract_apex_domain: strip scheme, take the host up to the first '/', drop any
# :port, then keep the final two dot-separated labels. Taking the bare host
# instead would let "ch.linkedin.com" past a blocklist that contains
# "linkedin.com" — the whole point of matching on the apex.
_APEX_DOMAIN_SQL = r"""
    substring(
        regexp_replace(
            split_part(regexp_replace(lower(url), '^https?://', ''), '/', 1),
            ':[0-9]+$', ''
        )
        from '([^.]+\.[^.]+)$'
    )
"""


def build_candidate_rows(company_id: int, candidates: list[dict[str, Any]], now: datetime) -> list[dict]:
    """Flatten parsed search results into insertable candidate rows.

    De-duplicates on url within the company: a single ON CONFLICT DO UPDATE
    statement cannot touch the same (company_id, url) twice, and Serper results
    do sometimes repeat a URL.
    """
    rows: list[dict] = []
    seen: set[str] = set()
    for cand in candidates:
        url = cand.get("link") or cand.get("url")
        if not url or url in seen:
            continue
        seen.add(url)
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
    return rows


def bulk_upsert_url_candidates(db: Session, rows: list[dict]) -> int:
    """Upsert candidate rows for MANY companies in one statement.

    The per-company `upsert_url_candidates` costs a round trip each; at 700k
    companies × ~9 candidates that is the difference between thousands of
    statements and millions. Returns rows affected (inserted + updated).
    """
    if not rows:
        return 0
    stmt = pg_insert(CompanyUrlCandidate).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["company_id", "url"],
        set_={
            "score": stmt.excluded.score,
            "title": stmt.excluded.title,
            "snippet": stmt.excluded.snippet,
            "position": stmt.excluded.position,
        },
    )
    result = db.execute(stmt)
    db.flush()
    return int(result.rowcount or 0)


def bulk_select_best_candidates(
    db: Session,
    company_ids: list[int],
    blocked: frozenset[str] | set[str],
) -> int:
    """Set-based equivalent of select_best_candidate over many companies.

    Same rule: demote any current selection, then mark the highest-scoring
    non-blocked pending candidate as 'selected'. Ties broken by id so the choice
    is deterministic across re-runs. Returns the number of companies that got a
    selection.
    """
    if not company_ids:
        return 0

    db.execute(
        text(
            "UPDATE company_url_candidates SET status = 'pending' "
            "WHERE company_id = ANY(CAST(:ids AS bigint[])) AND status = 'selected'"
        ),
        {"ids": list(company_ids)},
    )

    result = db.execute(
        text(
            f"""
            UPDATE company_url_candidates c
            SET status = 'selected'
            FROM (
                SELECT DISTINCT ON (company_id) id
                FROM company_url_candidates
                WHERE company_id = ANY(CAST(:ids AS bigint[]))
                  AND status = 'pending'
                  -- CAST is load-bearing: an untyped empty ARRAY[] makes
                  -- Postgres error with "cannot determine type of empty array".
                  AND COALESCE({_APEX_DOMAIN_SQL}, '') <> ALL(CAST(:blocked AS text[]))
                ORDER BY company_id, score DESC NULLS LAST, id
            ) best
            WHERE c.id = best.id
            """  # noqa: S608
        ),
        {"ids": list(company_ids), "blocked": list(blocked)},
    )
    db.flush()
    return int(result.rowcount or 0)


def bulk_create_crawl_states(db: Session, company_ids: list[int]) -> int:
    """Create a crawl state per company, pointing at its selected candidate.

    Companies with no crawlable candidate (none found, or every one blocked)
    still get a row — with `no_website` and a NULL selected_url_id. That matters
    for more than tidiness: the populate job's "skip companies already seeded"
    filter keys on the existence of this row, so without it those companies
    would be re-examined on every run and the backfill would never converge.

    ON CONFLICT DO NOTHING — never disturb a company already being crawled.
    """
    if not company_ids:
        return 0
    result = db.execute(
        text(
            """
            INSERT INTO company_crawl_state
                (company_id, selected_url_id, crawl_status, tier, crawl_phase)
            SELECT ids.company_id,
                   best.id,
                   CASE WHEN best.id IS NULL THEN 'no_website' ELSE 'pending' END,
                   'http',
                   'identity'
            FROM unnest(CAST(:ids AS bigint[])) AS ids(company_id)
            LEFT JOIN company_url_candidates best
                   ON best.company_id = ids.company_id
                  AND best.status = 'selected'
            ON CONFLICT (company_id) DO NOTHING
            """
        ),
        {"ids": list(company_ids)},
    )
    db.flush()
    return int(result.rowcount or 0)


def select_best_candidate(db: Session, company_id: int) -> CompanyUrlCandidate | None:
    """Mark the highest-scoring non-blocked pending candidate as 'selected'.

    Any existing 'selected' row for this company is demoted back to 'pending'.
    Directory sites and social media are skipped so the crawler never tries to
    scrape moneyhouse.ch, LinkedIn, etc. as company websites — including the
    admin-approved directory domains, which the static set does not carry
    (see get_effective_crawl_blocklist).
    Returns the newly selected candidate, or None if no crawlable candidates exist.
    """
    blocked = get_effective_crawl_blocklist(db)

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
        if not is_crawl_blocked(best.url, blocked):
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
    phase: str = "identity",
) -> list[CompanyCrawlState]:
    """Atomically claim a batch of crawl states via SELECT FOR UPDATE SKIP LOCKED.

    HTTP tier:       picks up crawl_status='pending' AND tier='http'
    Playwright tier: picks up crawl_status='pending' AND tier='playwright'
                     PLUS crawl_status='js_required' (escalated by HTTP workers)

    `phase` scopes the claim to one crawl phase, so identity and content workers
    claim disjoint rows and can run concurrently without coordinating.

    **`FOR UPDATE OF cs` — the `OF cs` is load-bearing.** A bare `FOR UPDATE`
    locks a row in EVERY table of the FROM list, and this statement joins
    `companies` whenever `canton` is set or `order_by` is score-based (which
    `web_crawl_content` does by default). That meant each claim took row locks on
    `companies` too, held until the whole batch committed — minutes, for phase B.
    Two ways that hurt, both observed:
      - `UPDATE companies` from any other job (NOGA reclassify, Zefix nightly
        bulk/detail, recompute_website_status) blocked behind the crawl batch and
        could hit the engine-wide 30 s statement_timeout in database.py.
      - `SKIP LOCKED` cuts the other way too: a company row locked by one of
        those jobs made the crawler silently skip its crawl-state row.
    The join is purely for ordering/filtering; this statement only ever writes
    `company_crawl_state`, so restricting the lock to `cs` is both correct and
    what removes the cross-job contention.
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
    params["phase"] = phase

    sql = text(f"""
        SELECT cs.company_id FROM company_crawl_state cs
        {join_clause}
        WHERE {status_clause}
          AND cs.crawl_phase = :phase
          AND (cs.next_crawl_at IS NULL OR cs.next_crawl_at <= :now)
          AND cs.selected_url_id IS NOT NULL
          {canton_clause}
        ORDER BY {order_clause}
        LIMIT :limit
        FOR UPDATE OF cs SKIP LOCKED
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
        # Record WHY this was escalated. Previously js_required rows carried no
        # evidence at all, so a backlog of them was indistinguishable between
        # "Cloudflare wall" (a local browser cannot beat a datacenter ASN) and
        # "SPA shell" (a local browser solves it for free) — which makes the
        # rows unroutable without re-crawling them.
        #
        # bot_protected is deliberately NOT set here: it stays the marker for a
        # confirmed hard block, which sync_terminal_website_status and the
        # `unreachable` verdict key on.
        if bot_protection_type:
            state.bot_protection_type = bot_protection_type
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


def release_in_progress_states(
    db: Session,
    tier: str,
    phase: str | None = None,
    *,
    stale_after_seconds: float | None = None,
) -> int:
    """Reset company_crawl_state rows stuck in 'in_progress' back to 'pending'.

    Crash recovery only: it exists so a pod dying mid-batch doesn't permanently
    strand companies. For 'playwright' tier, also releases rows where
    tier='playwright' since those were escalated by the HTTP crawler.

    `phase` scopes the release to one crawl phase. Identity and content workers
    run concurrently, so an unscoped release lets one job's sweep yank rows out
    from under the other job's in-flight batch.

    `stale_after_seconds` is what makes this safe to run while OTHER jobs of the
    same tier+phase are mid-batch — which is the whole point of `web_crawl_http`
    being in NO_DEDUP. Without it this sweep resets *every* in_progress row,
    including the batch a sibling pod claimed seconds ago, so both pods then
    crawl the same companies. Rows are claimed through the ORM, so `updated_at`
    is stamped at claim time and doubles as the claim clock; pass a threshold
    comfortably above the job's `company_timeout`. Callers releasing their OWN
    rows should use release_crawl_states_by_id instead — it needs no threshold.

    Returns the number of rows released.
    """
    if tier == "playwright":
        tier_clause = "tier IN ('http', 'playwright')"
    else:
        tier_clause = "tier = :tier"

    params: dict[str, Any] = {"tier": tier}
    phase_clause = ""
    if phase is not None:
        phase_clause = "AND crawl_phase = :phase"
        params["phase"] = phase

    stale_clause = ""
    if stale_after_seconds is not None:
        from datetime import timedelta
        params["cutoff"] = datetime.now(timezone.utc) - timedelta(seconds=stale_after_seconds)
        stale_clause = "AND updated_at < :cutoff"

    result = db.execute(
        text(
            f"UPDATE company_crawl_state SET crawl_status = 'pending' "  # noqa: S608
            f"WHERE crawl_status = 'in_progress' AND {tier_clause} "
            f"{phase_clause} {stale_clause}"
        ),
        params,
    )
    db.flush()
    return result.rowcount


def release_crawl_states_by_id(db: Session, company_ids: list[int] | set[int]) -> int:
    """Release specific rows this job claimed but did not finish.

    The precise counterpart to release_in_progress_states: a job knows exactly
    which companies it claimed, so on pause/cancel/crash-out it can hand back its
    own in-flight batch immediately without a staleness threshold and without
    touching any sibling job's rows. Only rows still sitting in 'in_progress' are
    reset — companies the batch already finished keep their terminal status.
    """
    ids = sorted(company_ids)  # deterministic lock order — see claim_crawl_batch
    if not ids:
        return 0
    released = (
        db.query(CompanyCrawlState)
        .filter(
            CompanyCrawlState.company_id.in_(ids),
            CompanyCrawlState.crawl_status == "in_progress",
        )
        .update({"crawl_status": "pending"}, synchronize_session=False)
    )
    db.flush()
    return int(released)


# ── Phase transitions ──────────────────────────────────────────────────────────

def advance_to_content_phase(db: Session, company_id: int) -> bool:
    """Move a company from phase A (identity) to phase B (content).

    Called from handle_web_extract once identity is confirmed. Idempotent and
    safe under concurrency: the WHERE clause only matches a row still sitting in
    the identity phase, so a re-run of extraction on an already-advanced company
    (or two extract workers racing on it) cannot re-queue a finished content
    crawl. Returns True if this call performed the transition.
    """
    result = db.execute(
        text(
            "UPDATE company_crawl_state "
            "SET crawl_phase = 'content', crawl_status = 'pending', tier = 'http', "
            "    next_crawl_at = NULL, consecutive_failures = 0, crawl_error_detail = NULL "
            "WHERE company_id = :cid AND crawl_phase = 'identity'"
        ),
        {"cid": company_id},
    )
    db.flush()
    return bool(result.rowcount)


def mark_phase_done(db: Session, company_id: int) -> None:
    """Mark a company as finished with both crawl phases."""
    db.execute(
        text("UPDATE company_crawl_state SET crawl_phase = 'done' WHERE company_id = :cid"),
        {"cid": company_id},
    )
    db.flush()


def get_uncrawled_inventory_urls(
    db: Session,
    company_id: int,
    limit: int = 500,
) -> list[tuple[str, str]]:
    """Return (page_type, url) for pages discovered but never fetched.

    Phase A's sitemap pass writes these rows (save_page_inventory, crawled=false),
    so phase B can seed its frontier from the DB instead of re-fetching
    robots.txt and sitemap.xml. Ordered by the same _SUBPAGE_PRIORITY rank the
    inventory was classified with, so the highest-signal pages are crawled first
    when the budget runs out.
    """
    rows = db.execute(
        text(
            "SELECT page_type, url FROM company_web_pages "
            "WHERE company_id = :cid AND crawled IS FALSE "
            "ORDER BY priority NULLS LAST, id "
            "LIMIT :lim"
        ),
        {"cid": company_id, "lim": limit},
    ).fetchall()
    return [(str(r[0]), str(r[1])) for r in rows]


def get_crawled_page_urls(db: Session, company_id: int) -> set[str]:
    """URLs already fetched for a company — phase B's visited-set seed."""
    rows = db.execute(
        text(
            "SELECT url, final_url FROM company_web_pages "
            "WHERE company_id = :cid AND crawled IS TRUE"
        ),
        {"cid": company_id},
    ).fetchall()
    out: set[str] = set()
    for url, final_url in rows:
        if url:
            out.add(str(url))
        if final_url:
            out.add(str(final_url))
    return out


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


def delete_web_pages_for_candidate(db: Session, company_id: int, url_candidate_id: int | None) -> int:
    """Delete a company's pages for ONE url candidate before re-crawling it.

    The identity phase uses this rather than delete_web_pages_for_company: with
    the batched fallback chain a company is crawled once per candidate, and
    wiping every page each time would destroy two things that the chain depends
    on — the per-candidate comparison shown in the Website panel, and
    get_next_crawlable_candidate's "already attempted" test, which keys on the
    existence of pages for a candidate. Without those pages the chain would
    re-pick the same candidate forever.

    Still prevents the duplicate rows the company-wide version existed to avoid:
    S3 keys are (company_id, url_candidate_id, page_type), so re-crawling the
    SAME candidate overwrites its objects and its old rows must go.
    """
    if url_candidate_id is None:
        return delete_web_pages_for_company(db, company_id)
    result = db.execute(
        text(
            "DELETE FROM company_web_pages "
            "WHERE company_id = :cid AND url_candidate_id = :uid"
        ),
        {"cid": company_id, "uid": url_candidate_id},
    )
    db.flush()
    return result.rowcount


def retarget_crawl_state_to_candidate(db: Session, company_id: int, candidate_id: int) -> bool:
    """Point a company's identity crawl at its next candidate and re-queue it.

    This is the batched replacement for enqueuing a per-company
    `web_crawl_single` job on every fallback. That approach turned the pipeline
    inside out: each retry became its own job row claiming a whole worker slot
    for ONE company (against batch_size=20 on the normal path) and waiting on
    the job poll interval, so companies whose site is the 2nd or 3rd candidate —
    the common case — crawled orders of magnitude slower than the first-guess
    ones. Re-queuing the existing state row instead lets the normal batch
    crawler pick the retry up with everything else.

    Only touches a row still in the identity phase, so it can never drag a
    company back out of phase B. Returns True if the retarget happened.
    """
    result = db.execute(
        text(
            "UPDATE company_crawl_state "
            "SET selected_url_id = :cand, crawl_status = 'pending', tier = 'http', "
            "    next_crawl_at = NULL, consecutive_failures = 0, "
            "    crawl_error_detail = NULL, bot_protected = FALSE, "
            "    bot_protection_type = NULL "
            "WHERE company_id = :cid AND crawl_phase = 'identity'"
        ),
        {"cid": company_id, "cand": candidate_id},
    )
    db.flush()
    return bool(result.rowcount)


# Crawl statuses that mean "we tried and could not read the site", as opposed to
# "we read it and it wasn't them".
_UNREACHABLE_CRAWL_STATUSES = ("bot_blocked", "http_error", "timeout", "no_content")


def escalate_to_external(db: Session, state: CompanyCrawlState) -> None:
    """Hand a company to the paid external scrape tier.

    Reached only after Playwright — a real browser — was itself bot-blocked.
    Re-queues as `pending` with tier='external' so the normal claim query picks
    it up; `web_crawl_external` is the only job whitelisted for that tier, which
    is what keeps billed fetches off the free crawlers' path.
    """
    state.crawl_status = "pending"
    state.tier = "external"
    state.next_crawl_at = None
    state.crawl_error_detail = "escalated to external scrape tier"
    db.flush()


def count_pending_external(db: Session, phase: str = "identity") -> int:
    """How many companies are waiting on the paid tier — the spend forecast."""
    return int(
        db.execute(
            text(
                "SELECT COUNT(*) FROM company_crawl_state "
                "WHERE crawl_status = 'pending' AND tier = 'external' "
                "  AND crawl_phase = :phase"
            ),
            {"phase": phase},
        ).scalar()
        or 0
    )


def sync_terminal_website_status(db: Session, company_ids: list[int] | set[int]) -> int:
    """Write `website_status` for companies whose identity phase concluded
    WITHOUT producing an extract row.

    Needed because `handle_web_extract` only writes a verdict when an extract
    exists — and it only ever claims companies that have pages. A company with no
    crawlable candidate has no pages, so it is never claimed at all, and one whose
    every candidate was bot-blocked produces no extract either. Both would keep a
    NULL website_status (visually identical to "not started") until somebody ran
    `recompute_website_status` by hand.

    Set-based and idempotent: the WHERE clauses skip rows already carrying the
    right value, so calling it after every crawl batch costs nothing once settled.
    Mirrors compute_verdict's taxonomy — see get_identity_outcome.
    """
    ids = sorted(company_ids)
    if not ids:
        return 0

    no_extract = (
        "NOT EXISTS (SELECT 1 FROM company_web_extract e "
        "            WHERE e.company_id = companies.id)"
    )
    failures = ", ".join(f"'{s}'" for s in _UNREACHABLE_CRAWL_STATUSES)
    updated = 0

    # `unreachable` first, and `none` explicitly excludes those statuses, so the
    # two conditions are mutually exclusive and order cannot matter.
    for status, state_clause in (
        (
            "unreachable",
            f"cs.crawl_status IN ({failures})",
        ),
        (
            "none",
            f"cs.crawl_status NOT IN ({failures}) "
            f"AND (cs.crawl_status = 'no_website' OR cs.crawl_phase = 'done')",
        ),
    ):
        stmt = text(
            f"UPDATE companies SET website_status = :status, "  # noqa: S608
            f"    website_count = NULL, website_url = NULL "
            f"WHERE id IN :ids "
            f"  AND (website_status IS NULL OR website_status <> :status) "
            f"  AND {no_extract} "
            f"  AND EXISTS (SELECT 1 FROM company_crawl_state cs "
            f"              WHERE cs.company_id = companies.id AND {state_clause})"
        ).bindparams(sa_bindparam("ids", value=ids, expanding=True))
        updated += db.execute(stmt, {"status": status}).rowcount or 0

    db.flush()
    return int(updated)


def get_identity_outcome(db: Session, company_id: int) -> str | None:
    """Why the identity phase produced no usable extract, if it has concluded.

    Returns 'no_candidates' | 'unreachable' | 'exhausted', or None while the
    company is still in flight. Lets compute_verdict tell "we looked and found
    nothing" apart from "we haven't looked yet" — both of which previously
    surfaced as a NULL website_status, i.e. an empty cell.
    """
    row = db.execute(
        text(
            "SELECT crawl_status, crawl_phase FROM company_crawl_state "
            "WHERE company_id = :cid"
        ),
        {"cid": company_id},
    ).first()
    if row is None:
        return None
    status, phase = row[0], row[1]
    if status == "no_website":
        return "no_candidates"
    if status in _UNREACHABLE_CRAWL_STATUSES:
        return "unreachable"
    if phase == "done":
        return "exhausted"
    return None


def delete_content_pages_for_company(db: Session, company_id: int) -> int:
    """Delete only phase-B (content) pages, preserving the phase-A identity crawl.

    Phase B must never call delete_web_pages_for_company: that would destroy the
    homepage and impressum rows the identity verdict was computed from, leaving
    a confirmed company with no evidence behind its own website_status.

    Identity pages are those whose page_type is in IDENTITY_PAGE_TYPES plus the
    homepage; everything else belongs to phase B. Inventory-only rows
    (crawled=false) are kept — they are phase B's frontier seed.
    """
    from app.services.enrichment.crawler_common import IDENTITY_PAGE_TYPES

    keep = tuple(IDENTITY_PAGE_TYPES | {"homepage"})
    result = db.execute(
        text(
            "DELETE FROM company_web_pages "
            "WHERE company_id = :cid AND crawled IS TRUE "
            "  AND page_type NOT IN :keep"
        ).bindparams(sa_bindparam("keep", expanding=True)),
        {"cid": company_id, "keep": list(keep)},
    )
    db.flush()
    return result.rowcount


def reset_content_crawled(db: Session, *, canton: str | None = None) -> int:
    """Reset finished or failed phase-B rows back to a pending content crawl.

    Covers both rows that completed the content phase (crawl_phase='done') and
    rows whose content crawl ended terminally — a failed phase B leaves
    crawl_phase='content' with a terminal status, which is claimable by nothing
    until a rerun picks it back up.

    Never touches phase 'identity' rows, so a rerun cannot drag a company back
    through identity resolution it has already passed.
    """
    canton_clause = ""
    params: dict[str, Any] = {}
    if canton:
        canton_clause = "AND company_id IN (SELECT id FROM companies WHERE canton = :canton)"
        params["canton"] = canton

    result = db.execute(
        text(
            f"UPDATE company_crawl_state "  # noqa: S608
            f"SET crawl_phase = 'content', crawl_status = 'pending', tier = 'http', "
            f"    crawl_error_detail = NULL, next_crawl_at = NULL, consecutive_failures = 0 "
            f"WHERE (crawl_phase = 'done' "
            f"       OR (crawl_phase = 'content' AND crawl_status IN "
            f"           ('crawled', 'bot_blocked', 'http_error', 'timeout', 'no_content'))) "
            f"{canton_clause}"
        ),
        params,
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
    discovered_via: str | None = None,
    priority: int | None = None,
) -> CompanyWebPage:
    page = CompanyWebPage(
        company_id=company_id,
        url_candidate_id=url_candidate_id,
        page_type=page_type,
        url=url,
        final_url=final_url,
        discovered_via=discovered_via,
        crawled=True,
        priority=priority,
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


def save_page_inventory(
    db: Session,
    *,
    company_id: int,
    entries: list[tuple[str, str]],
    already_saved_urls: set[str],
    discovered_via: str = "sitemap",
) -> int:
    """Persist inventory-only rows for discovered-but-not-fetched pages.

    entries: list of (page_type, url) from crawler_common.classify_all_urls.
    already_saved_urls: URLs already inserted this run via save_web_page (skipped
    here to avoid duplicate rows for the same URL within one crawl).
    Returns the number of inventory rows inserted.
    """
    now = datetime.now(timezone.utc)
    count = 0
    for priority, (page_type, url) in enumerate(entries):
        if url in already_saved_urls:
            continue
        db.add(CompanyWebPage(
            company_id=company_id,
            url_candidate_id=None,
            page_type=page_type,
            url=url,
            discovered_via=discovered_via,
            crawled=False,
            priority=priority,
            crawled_at=now,
            needs_extraction=False,
        ))
        count += 1
    if count:
        db.flush()
    return count


def get_page_inventory(db: Session, company_id: int) -> list[CompanyWebPage]:
    """All known pages for a company (fetched + inventory-only), priority-ordered."""
    return (
        db.query(CompanyWebPage)
        .filter(CompanyWebPage.company_id == company_id)
        .order_by(
            CompanyWebPage.crawled.desc(),
            CompanyWebPage.priority.asc().nullslast(),
            CompanyWebPage.id.asc(),
        )
        .all()
    )


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
    """Return True if any ML job type is currently queued AND claimable.

    Used by crawler progress callbacks to self-preempt and yield to ML jobs.
    Single SELECT 1 — negligible overhead.

    The `cancel_requested` filter must mirror `job_run.claim_next_job` exactly.
    When it didn't, the two disagreed about what "queued" means: a job left
    `queued` with `cancel_requested = true` was visible here but refused by the
    claimer, so a crawler yielded to a job that could never start, was re-claimed,
    yielded again — a hot loop at several requeues per second that no amount of
    queue reordering could break (job #12744 starving #12746, 2026-08-01).
    Only yield to work that can actually take the slot.
    """
    if not ml_types:
        return False
    result = db.execute(
        text(
            "SELECT 1 FROM job_runs "
            "WHERE status = 'queued' AND job_type IN :types "
            "  AND (cancel_requested IS FALSE OR cancel_requested IS NULL) "
            "LIMIT 1"
        ).bindparams(sa_bindparam("types", expanding=True)),
        {"types": list(ml_types)},
    ).fetchone()
    return result is not None


def get_selected_candidate(db: Session, company_id: int) -> CompanyUrlCandidate | None:
    return (
        db.query(CompanyUrlCandidate)
        .filter_by(company_id=company_id, status="selected")
        .first()
    )


def parse_google_results_raw(raw: Any) -> list[dict[str, Any]]:
    """Return a list of candidate dicts from stored search results.

    `raw` is company_search_results.results_raw — normally a native list (JSON
    column, ORM/psycopg2 already deserialize it), but this also accepts a JSON
    string defensively for any raw-SQL path that returns one as-is.
    """
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, list) else []
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
    social media). Prefer passing the set from get_effective_crawl_blocklist(),
    which also covers admin-approved directory domains.
    """
    if blocked is None:
        from app.services.scoring.scoring import CRAWL_BLOCKED_DOMAINS
        blocked = CRAWL_BLOCKED_DOMAINS
    domain = _extract_apex_domain(url)
    return bool(domain and domain in blocked)


# Cached union of the static blocklist and the DB-managed directory domains.
# select_best_candidate runs once per company across ~700k rows, so this must
# never become a per-company query.
_BLOCKLIST_TTL = 300.0
_blocklist_cache: frozenset[str] | None = None
_blocklist_cached_at: float = 0.0
_blocklist_lock = threading.Lock()


def get_effective_crawl_blocklist(db: Session) -> frozenset[str]:
    """CRAWL_BLOCKED_DOMAINS plus every admin-approved directory domain.

    The static set only carries the seed directories. Domains found by the
    discovery job are inserted precisely *because* they are not already blocked
    (handle_discover_directory_domains skips ones that are), so without this
    merge every domain that discovery ever adds stays eligible to be selected as
    a company website: the crawler spends a full multi-page crawl plus S3
    uploads on a directory listing, rejects it after the fact via
    is_directory_page, and then burns a fallback crawl on the next candidate.

    Only 'approved' domains are merged — 'pending_review' rows are unreviewed
    guesses, and wrongly blocking one would silently cost a real company site.

    Cached for _BLOCKLIST_TTL seconds; an approval takes effect within that.
    """
    global _blocklist_cache, _blocklist_cached_at
    from app.services.scoring.scoring import CRAWL_BLOCKED_DOMAINS

    now = time.monotonic()
    cached = _blocklist_cache
    if cached is not None and (now - _blocklist_cached_at) < _BLOCKLIST_TTL:
        return cached

    with _blocklist_lock:
        # Re-check inside the lock: another thread may have just refreshed it.
        if _blocklist_cache is not None and (time.monotonic() - _blocklist_cached_at) < _BLOCKLIST_TTL:
            return _blocklist_cache
        try:
            from app.crud.directory_crawl_domain import get_approved_directory_crawl_domains
            approved = get_approved_directory_crawl_domains(db)
        except Exception:  # noqa: BLE001
            # Never let a blocklist refresh failure stop a crawl — fall back to
            # the static set (the pre-existing behaviour).
            logger.warning("Directory blocklist refresh failed; using static set", exc_info=True)
            approved = set()
        _blocklist_cache = frozenset(CRAWL_BLOCKED_DOMAINS | {d.lower() for d in approved if d})
        _blocklist_cached_at = time.monotonic()
        return _blocklist_cache


def invalidate_crawl_blocklist_cache() -> None:
    """Force the next get_effective_crawl_blocklist() call to re-query the DB."""
    global _blocklist_cache, _blocklist_cached_at
    with _blocklist_lock:
        _blocklist_cache = None
        _blocklist_cached_at = 0.0


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
        blocked = get_effective_crawl_blocklist(db)

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
