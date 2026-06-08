"""Job handlers for web crawling pipeline.

Three job types:
  web_url_populate   — seeds company_url_candidates from google_search_results_raw
  web_crawl_http     — httpx crawler (crawler-http pods + ML worker idle-fill)
  web_crawl_playwright — Playwright crawler (ML worker idle-fill)
  web_select_url     — switches selected URL candidate for a company
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import text

from app import crud
from app.crud import crawler as crawler_crud
from app.models.company_url_candidate import CompanyUrlCandidate
from app.services.job_handlers import JobContext
from app.services.job_worker import JobPausedError

logger = logging.getLogger(__name__)

# ML job types that the crawler should yield to when queued on the ML worker.
# Matches the JOB_TYPE_WHITELIST on the ml-worker deployment minus crawler types.
_ML_JOB_TYPES: frozenset[str] = frozenset({
    "reclassify_noga", "build_noga_embeddings", "detect_language_bulk",
    "reclassify_low_conf_noga", "tfidf_kmeans_cluster", "recompute_keywords",
    "reextract_keywords", "cluster_analysis", "discover_stopwords",
    "noga_v2_explain", "embed_purpose_full", "embed_purpose_clean",
})


def _self_preempt_if_ml_queued(ctx: JobContext) -> None:
    """Pause the current crawl job if an ML job is waiting in the queue.

    Only active on the ML worker — detected by the presence of ML job types
    in the worker's JOB_TYPE_WHITELIST env var.
    """
    import os
    whitelist_raw = os.environ.get("JOB_TYPE_WHITELIST", "")
    whitelist = {t.strip() for t in whitelist_raw.split(",") if t.strip()}
    if not whitelist.intersection(_ML_JOB_TYPES):
        # Not running on ML worker — skip preemption check
        return
    if crawler_crud.has_queued_ml_job(ctx.db, _ML_JOB_TYPES):
        crud.mark_pause_requested(ctx.db, ctx.job)
        raise JobPausedError("ML job queued — crawler yielding")


# ── web_url_populate ──────────────────────────────────────────────────────────

def handle_web_url_populate(ctx: JobContext) -> tuple[dict, str]:
    """Seed company_url_candidates from existing google_search_results_raw.

    For each company that has Serper.dev results stored, creates URL candidate
    rows and a company_crawl_state record. The highest-scoring candidate is
    automatically marked as 'selected'.

    Companies without google_search_results_raw are skipped (run the Serper
    enrichment job first).
    """
    batch_size = int(ctx.params.get("batch_size", 500))
    stats: dict = {"processed": 0, "candidates_created": 0, "skipped_no_results": 0, "errors": []}

    total_q = ctx.db.execute(
        text("SELECT COUNT(*) FROM companies WHERE google_search_results_raw IS NOT NULL")
    ).scalar() or 0
    total = int(total_q)

    offset = ctx.resume_from
    done = offset

    while True:
        ctx.assert_not_cancelled()
        _self_preempt_if_ml_queued(ctx)

        rows = ctx.db.execute(
            text(
                "SELECT id, google_search_results_raw FROM companies "
                "WHERE google_search_results_raw IS NOT NULL "
                "ORDER BY id "
                "LIMIT :limit OFFSET :offset"
            ),
            {"limit": batch_size, "offset": offset},
        ).fetchall()

        if not rows:
            break

        for company_id, raw_json in rows:
            try:
                candidates = crawler_crud.parse_google_results_raw(raw_json)
                if not candidates:
                    stats["skipped_no_results"] += 1
                    continue
                upserted = crawler_crud.upsert_url_candidates(ctx.db, company_id, candidates)
                best = crawler_crud.select_best_candidate(ctx.db, company_id)
                crawler_crud.get_or_create_crawl_state(
                    ctx.db,
                    company_id,
                    selected_url_id=best.id if best else None,
                )
                stats["candidates_created"] += len(upserted)
                stats["processed"] += 1
            except Exception as exc:  # noqa: BLE001
                stats["errors"].append(f"company {company_id}: {exc}")
                logger.warning("web_url_populate error for company %d: %s", company_id, exc)
                ctx.db.rollback()

        ctx.db.commit()
        done += len(rows)
        offset += batch_size

        msg = (
            f"Processed {done}/{total} — "
            f"{stats['candidates_created']} candidates, "
            f"{stats['skipped_no_results']} skipped, "
            f"{len(stats['errors'])} errors"
        )
        crud.update_progress(ctx.db, ctx.job, message=msg, done=done, total=total, stats=dict(stats))
        crud.create_event(ctx.db, job_id=ctx.job.id, level="debug", message=msg)

    done_msg = (
        f"Done — {stats['processed']} companies, "
        f"{stats['candidates_created']} URL candidates, "
        f"{stats['skipped_no_results']} skipped (no Serper results), "
        f"{len(stats['errors'])} errors"
    )
    return stats, done_msg


# ── web_crawl_http ────────────────────────────────────────────────────────────

def _run_crawl_batch(
    ctx: JobContext,
    *,
    tier: str,
    batch_size: int,
    canton: str | None,
    max_pages: int,
    rate_limit_delay: float,
    crawl_fn,
) -> tuple[dict, str]:
    """Shared batch-crawl loop used by both HTTP and Playwright handlers.

    Releases stuck in_progress rows at start (crash recovery) and in a
    finally block (pause/fail recovery), so no company ever gets stranded.
    """
    stats: dict = {
        "crawled": 0, "bot_blocked": 0, "js_required": 0,
        "http_error": 0, "timeout": 0, "no_content": 0, "errors": [],
    }

    # Release rows stuck in_progress from any previous crashed run
    released = crawler_crud.release_in_progress_states(ctx.db, tier=tier)
    ctx.db.commit()
    if released:
        ctx.event("info", f"Released {released} stuck in_progress rows from previous run")

    if tier == "playwright":
        count_sql = (
            "SELECT COUNT(*) FROM company_crawl_state "
            "WHERE (crawl_status = 'pending' AND tier = 'playwright') "
            "   OR crawl_status = 'js_required'"
        )
        total = int(ctx.db.execute(text(count_sql)).scalar() or 0)
    else:
        count_sql = (
            "SELECT COUNT(*) FROM company_crawl_state "
            "WHERE crawl_status = 'pending' AND tier = :tier"
        )
        total = int(ctx.db.execute(text(count_sql), {"tier": tier}).scalar() or 0)
    done = 0

    try:
        while True:
            ctx.assert_not_cancelled()
            _self_preempt_if_ml_queued(ctx)

            batch = crawler_crud.claim_crawl_batch(
                ctx.db, tier=tier, batch_size=batch_size, canton=canton
            )
            if not batch:
                break

            for state in batch:
                ctx.assert_not_cancelled()
                candidate = ctx.db.get(CompanyUrlCandidate, state.selected_url_id)
                if not candidate:
                    crawler_crud.mark_crawl_failed(ctx.db, state, "http_error", "No URL candidate found")
                    continue

                try:
                    result = asyncio.run(
                        crawl_fn(
                            state.company_id,
                            candidate.url,
                            max_pages=max_pages,
                            rate_limit_delay=rate_limit_delay,
                        )
                    )
                except Exception as exc:  # noqa: BLE001
                    crawler_crud.mark_crawl_failed(ctx.db, state, "http_error", str(exc))
                    stats["errors"].append(f"company {state.company_id}: {exc}")
                    continue

                now = datetime.now(timezone.utc)

                if result.failure_status:
                    crawler_crud.mark_crawl_failed(
                        ctx.db, state,
                        status=result.failure_status,
                        detail=result.failure_detail,
                        bot_protection_type=result.bot_protection_type,
                    )
                    stats[result.failure_status] = stats.get(result.failure_status, 0) + 1
                    candidate.last_crawled_at = now
                elif result.needs_playwright:
                    crawler_crud.mark_crawl_failed(ctx.db, state, "js_required")
                    stats["js_required"] += 1
                else:
                    # Delete stale rows from previous crawls before inserting
                    # fresh ones. S3 key is keyed by (company_id, page_type)
                    # so re-crawling overwrites the file; old rows would be stale.
                    crawler_crud.delete_web_pages_for_company(ctx.db, state.company_id)
                    pages_crawled = []
                    for p in result.pages:
                        crawler_crud.save_web_page(
                            ctx.db,
                            company_id=state.company_id,
                            url_candidate_id=state.selected_url_id,
                            page_type=p.page_type,
                            url=p.url,
                            final_url=p.final_url,
                            crawled_at=now,
                            http_status=p.http_status,
                            lang=p.lang,
                            word_count=p.word_count,
                            image_count=p.image_count,
                            video_count=p.video_count,
                            has_contact_form=p.has_contact_form,
                            s3_key_html=p.s3_key_html,
                        )
                        pages_crawled.append(p.page_type)
                    crawler_crud.mark_crawl_done(ctx.db, state, pages_crawled)
                    candidate.status = "crawled"
                    candidate.last_crawled_at = now
                    stats["crawled"] += 1

            ctx.db.commit()
            done += len(batch)

            tier_label = "Playwright" if tier == "playwright" else "HTTP"
            msg = (
                f"{tier_label} crawled {done}/{total} — "
                f"{stats['crawled']} ok, {stats.get('bot_blocked', 0)} bot, "
                f"{stats.get('js_required', 0)} js, {stats.get('http_error', 0)} err, "
                f"{stats.get('timeout', 0)} timeout"
            )
            crud.update_progress(ctx.db, ctx.job, message=msg, done=done, total=total, stats=dict(stats))
            crud.create_event(ctx.db, job_id=ctx.job.id, level="debug", message=msg)

    finally:
        # Release any in_progress rows we claimed but didn't finish
        # (covers pause, cancel, unexpected exception)
        still_stuck = crawler_crud.release_in_progress_states(ctx.db, tier=tier)
        if still_stuck:
            ctx.db.commit()

    tier_label = "Playwright" if tier == "playwright" else "HTTP"
    done_msg = (
        f"{tier_label} done — {stats['crawled']} crawled, "
        f"{stats.get('bot_blocked', 0)} bot-blocked, "
        f"{stats.get('js_required', 0)} {'queued for Playwright, ' if tier == 'http' else ''}"
        f"{stats.get('http_error', 0)} HTTP errors, "
        f"{stats.get('timeout', 0)} timeouts, "
        f"{stats.get('no_content', 0)} empty, "
        f"{len(stats['errors'])} unexpected errors"
    )
    return stats, done_msg


def handle_web_crawl_http(ctx: JobContext) -> tuple[dict, str]:
    """HTTP-only crawler. Claims batches via SKIP LOCKED from company_crawl_state."""
    from app.services.crawler_http import crawl_company_http

    return _run_crawl_batch(
        ctx,
        tier="http",
        batch_size=int(ctx.params.get("batch_size", 20)),
        canton=ctx.params.get("canton") or None,
        max_pages=int(ctx.params.get("max_pages", 5)),
        rate_limit_delay=float(ctx.params.get("rate_limit_delay", 0.5)),
        crawl_fn=crawl_company_http,
    )


# ── web_crawl_playwright ──────────────────────────────────────────────────────

def handle_web_crawl_playwright(ctx: JobContext) -> tuple[dict, str]:
    """Playwright crawler. Claims companies where tier='playwright' or crawl_status='js_required'."""
    from app.services.crawler_playwright import crawl_company_playwright

    return _run_crawl_batch(
        ctx,
        tier="playwright",
        batch_size=int(ctx.params.get("batch_size", 10)),
        canton=ctx.params.get("canton") or None,
        max_pages=int(ctx.params.get("max_pages", 5)),
        rate_limit_delay=float(ctx.params.get("rate_limit_delay", 0.5)),
        crawl_fn=crawl_company_playwright,
    )


# ── web_select_url ────────────────────────────────────────────────────────────

def handle_web_select_url(ctx: JobContext) -> tuple[dict, str]:
    """Switch the selected URL candidate for a company and reset crawl state."""
    company_id = int(ctx.params["company_id"])
    url_candidate_id = int(ctx.params["url_candidate_id"])

    candidate = crawler_crud.switch_selected_candidate(ctx.db, company_id, url_candidate_id)
    if candidate is None:
        raise ValueError(
            f"URL candidate {url_candidate_id} not found or does not belong to company {company_id}"
        )
    ctx.db.commit()
    stats = {"company_id": company_id, "url_candidate_id": url_candidate_id, "url": candidate.url}
    return stats, f"Selected URL {candidate.url} for company {company_id}"


# ── web_crawl_single ──────────────────────────────────────────────────────────

def handle_web_crawl_single(ctx: JobContext) -> tuple[dict, str]:
    """Crawl a single company end-to-end.

    Params:
      company_id        (required) — DB id of the company
      max_pages         (optional, default 5)
      rate_limit_delay  (optional, default 0.5)
      force             (optional, default false) — re-crawl even if already crawled

    Flow:
      1. Check company has google_search_results_raw → populate candidates if needed
      2. Ensure crawl_state exists with a selected URL
      3. Try HTTP crawler; escalate to Playwright if js_required
      4. Store results
    """
    from app.services.crawler_http import crawl_company_http
    from app.services.crawler_playwright import crawl_company_playwright

    company_id = int(ctx.params["company_id"])
    max_pages = int(ctx.params.get("max_pages", 5))
    rate_limit_delay = float(ctx.params.get("rate_limit_delay", 0.5))
    force = bool(ctx.params.get("force", False))

    # ── Step 1: ensure URL candidates exist ──────────────────────────────
    ctx.status(f"Checking URL candidates for company {company_id}…")
    company = ctx.db.execute(
        text("SELECT id, google_search_results_raw FROM companies WHERE id = :id"),
        {"id": company_id},
    ).fetchone()
    if not company:
        raise ValueError(f"Company {company_id} not found")

    raw_json = company[1]
    if not raw_json:
        raise ValueError(
            f"Company {company_id} has no Serper.dev results. "
            "Run the batch enrichment job first."
        )

    candidates = crawler_crud.parse_google_results_raw(raw_json)
    if candidates:
        upserted = crawler_crud.upsert_url_candidates(ctx.db, company_id, candidates)
    else:
        upserted = []

    # Select best candidate if none is selected yet
    selected = crawler_crud.get_selected_candidate(ctx.db, company_id)
    if not selected:
        selected = crawler_crud.select_best_candidate(ctx.db, company_id)
    if not selected:
        ctx.db.commit()
        return {"company_id": company_id}, f"No crawlable URL found for company {company_id}"

    # ── Step 2: ensure crawl state exists ────────────────────────────────
    state = crawler_crud.get_or_create_crawl_state(ctx.db, company_id, selected.id)

    if not force and state.crawl_status == "crawled":
        ctx.db.commit()
        return (
            {"company_id": company_id, "url": selected.url, "skipped": True},
            f"Company {company_id} already crawled — pass force=true to re-crawl",
        )

    # Reset state if forcing or if previously failed
    if force or state.crawl_status not in ("pending", "in_progress"):
        state.crawl_status = "pending"
        state.tier = "http"
        state.bot_protected = False
        state.bot_protection_type = None
        state.crawl_error_detail = None

    ctx.db.commit()
    ctx.status(f"Crawling {selected.url}…")

    # ── Step 3: try HTTP, escalate to Playwright if needed ───────────────
    result = asyncio.run(
        crawl_company_http(company_id, selected.url, max_pages=max_pages, rate_limit_delay=rate_limit_delay)
    )

    if result.needs_playwright:
        ctx.status(f"JS rendering required — switching to Playwright for {selected.url}…")
        result = asyncio.run(
            crawl_company_playwright(company_id, selected.url, max_pages=max_pages, rate_limit_delay=rate_limit_delay)
        )

    # ── Step 4: store results ─────────────────────────────────────────────
    now = datetime.now(timezone.utc)

    if result.failure_status:
        crawler_crud.mark_crawl_failed(
            ctx.db, state,
            status=result.failure_status,
            detail=result.failure_detail,
            bot_protection_type=result.bot_protection_type,
        )
        selected.last_crawled_at = now
        ctx.db.commit()
        stats = {
            "company_id": company_id, "url": selected.url,
            "failure": result.failure_status, "detail": result.failure_detail,
        }
        return stats, f"Crawl failed ({result.failure_status}): {result.failure_detail}"

    crawler_crud.delete_web_pages_for_company(ctx.db, company_id)
    pages_crawled = []
    for p in result.pages:
        crawler_crud.save_web_page(
            ctx.db,
            company_id=company_id,
            url_candidate_id=selected.id,
            page_type=p.page_type,
            url=p.url,
            final_url=p.final_url,
            crawled_at=now,
            http_status=p.http_status,
            lang=p.lang,
            word_count=p.word_count,
            image_count=p.image_count,
            video_count=p.video_count,
            has_contact_form=p.has_contact_form,
            s3_key_html=p.s3_key_html,
        )
        pages_crawled.append(p.page_type)

    crawler_crud.mark_crawl_done(ctx.db, state, pages_crawled)
    selected.status = "crawled"
    selected.last_crawled_at = now
    ctx.db.commit()

    stats = {
        "company_id": company_id,
        "url": selected.url,
        "pages_crawled": pages_crawled,
        "used_playwright": result.needs_playwright,
    }
    return stats, f"Crawled {len(pages_crawled)} page(s) for company {company_id}: {', '.join(pages_crawled)}"
