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
from app.metrics import record_crawl_result
from app.models.company_url_candidate import CompanyUrlCandidate
from app.services.job_handlers import JobContext
from app.services.job_worker import JobPausedError

logger = logging.getLogger(__name__)

# High-priority job types that web_crawl_http should yield to when queued on
# the ML worker.  Includes all ML/NOGA types plus web_crawl_playwright so that
# HTTP idle-fill on the ML worker doesn't block playwright jobs from starting.
_ML_JOB_TYPES: frozenset[str] = frozenset({
    "reclassify_noga", "build_noga_embeddings", "detect_language_bulk",
    "reclassify_low_conf_noga", "tfidf_kmeans_cluster", "recompute_keywords",
    "reextract_keywords", "cluster_analysis", "discover_stopwords",
    "noga_v2_explain", "embed_purpose_full", "embed_purpose_clean",
    "web_crawl_playwright",
})

# Transient failures (timeout / connection error) get this many backoff retries
# before being marked terminally failed. Bot blocks and js_required are handled
# separately (escalation to Playwright), not retried in place.
_MAX_CRAWL_RETRIES = 3


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
        raise JobPausedError("High-priority job queued — crawler yielding", requeue=True)


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
        f"{stats['skipped_no_results']} skipped (no search results), "
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
    rerun: bool = False,
    order_by: str = "company_id_asc",
    limit: int | None = None,
    company_timeout: float = 120.0,
) -> tuple[dict, str]:
    """Shared batch-crawl loop used by both HTTP and Playwright handlers.

    Releases stuck in_progress rows at start (crash recovery) and in a
    finally block (pause/fail recovery), so no company ever gets stranded.
    """
    from app.services import s3_client as _s3
    if _s3.is_crawl_bucket_configured():
        ok = _s3.check_crawl_s3_connectivity()
        if not ok:
            ctx.event("warning", "S3 crawl bucket unreachable or does not exist — HTML uploads disabled for this run")

    stats: dict = {
        "crawled": 0, "bot_blocked": 0, "js_required": 0,
        "http_error": 0, "timeout": 0, "no_content": 0, "errors": [],
    }

    # Release rows stuck in_progress from any previous crashed run
    released = crawler_crud.release_in_progress_states(ctx.db, tier=tier)
    ctx.db.commit()
    if released:
        ctx.event("info", f"Released {released} stuck in_progress rows from previous run")

    if rerun and tier == "http":
        reset_count = crawler_crud.reset_http_crawled(ctx.db, canton=canton)
        ctx.db.commit()
        if reset_count:
            ctx.event("info", f"Rerun: reset {reset_count} previously crawled/failed HTTP rows to pending")
    elif rerun and tier == "playwright":
        reset_count = crawler_crud.reset_playwright_crawled(ctx.db, canton=canton)
        ctx.db.commit()
        if reset_count:
            ctx.event("info", f"Rerun: reset {reset_count} previously crawled/failed playwright rows to pending")

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

    if limit is not None:
        total = min(total, limit)
    done = 0
    _playwright_enqueued = False  # trigger at most once per run

    try:
        while True:
            ctx.assert_not_cancelled()
            _self_preempt_if_ml_queued(ctx)

            if limit is not None and done >= limit:
                break

            claim_size = batch_size if limit is None else min(batch_size, limit - done)
            batch = crawler_crud.claim_crawl_batch(
                ctx.db, tier=tier, batch_size=claim_size, canton=canton, order_by=order_by,
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
                        asyncio.wait_for(
                            crawl_fn(
                                state.company_id,
                                candidate.url,
                                url_candidate_id=candidate.id,
                                max_pages=max_pages,
                                rate_limit_delay=rate_limit_delay,
                            ),
                            timeout=company_timeout,
                        )
                    )
                except asyncio.TimeoutError:
                    crawler_crud.mark_crawl_failed(
                        ctx.db, state, "timeout",
                        f"Total crawl timeout after {company_timeout:.0f}s",
                    )
                    stats["timeout"] = stats.get("timeout", 0) + 1
                    stats["errors"].append(
                        f"company {state.company_id}: total timeout {company_timeout:.0f}s"
                    )
                    record_crawl_result(tier, "timeout")
                    continue
                except Exception as exc:  # noqa: BLE001
                    crawler_crud.mark_crawl_failed(ctx.db, state, "http_error", str(exc))
                    stats["errors"].append(f"company {state.company_id}: {exc}")
                    record_crawl_result(tier, "error")
                    continue

                now = datetime.now(timezone.utc)

                if result.failure_status:
                    fs = result.failure_status
                    if (
                        tier == "http"
                        and fs == "bot_blocked"
                        and result.bot_protection_type in ("cloudflare", "js_challenge", "captcha")
                    ):
                        # Hard bot block on the HTTP tier — hand off to Playwright
                        # (real browser) instead of failing terminally.
                        crawler_crud.mark_crawl_failed(ctx.db, state, "js_required")
                        stats["js_required"] = stats.get("js_required", 0) + 1
                        record_crawl_result(tier, "js_required")
                    elif (
                        fs in ("timeout", "http_error")
                        and (state.consecutive_failures or 0) < _MAX_CRAWL_RETRIES
                    ):
                        # Transient — reschedule with exponential backoff.
                        crawler_crud.schedule_crawl_retry(ctx.db, state, fs, result.failure_detail)
                        stats[fs] = stats.get(fs, 0) + 1
                        record_crawl_result(tier, fs)
                    else:
                        crawler_crud.mark_crawl_failed(
                            ctx.db, state,
                            status=fs,
                            detail=result.failure_detail,
                            bot_protection_type=result.bot_protection_type,
                        )
                        stats[fs] = stats.get(fs, 0) + 1
                        record_crawl_result(tier, fs)
                    candidate.last_crawled_at = now
                elif result.needs_playwright:
                    crawler_crud.mark_crawl_failed(ctx.db, state, "js_required")
                    stats["js_required"] += 1
                    record_crawl_result(tier, "js_required")
                else:
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
                    record_crawl_result(tier, "crawled")

            ctx.db.commit()
            done += len(batch)

            # When the HTTP tier finds js_required companies, kick off playwright
            # automatically. One job drains all js_required rows via SKIP LOCKED.
            if tier == "http" and stats.get("js_required", 0) > 0 and not _playwright_enqueued:
                try:
                    ctx.enqueue_job(
                        job_type="web_crawl_playwright",
                        label="Playwright crawl (auto — js_required)",
                        params={},
                        org_id=None,
                        user_id=ctx.job.user_id,
                    )
                    ctx.db.commit()
                    _playwright_enqueued = True
                except Exception as exc:  # noqa: BLE001
                    logger.debug("Auto-enqueue web_crawl_playwright skipped: %s", exc)

            # Trigger extraction after each batch so it runs in parallel on a
            # separate pod. Dedup returns the existing job if one is already
            # running (no-op). When extraction finishes mid-crawl, the next
            # batch creates a fresh job — keeping extraction continuously fed.
            if stats["crawled"] > 0:
                try:
                    ctx.enqueue_job(
                        job_type="web_extract",
                        label="HTML extraction (auto)",
                        params={},
                        org_id=None,
                        user_id=ctx.job.user_id,
                    )
                    ctx.db.commit()
                except Exception as exc:  # noqa: BLE001
                    logger.debug("Auto-enqueue web_extract skipped: %s", exc)

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
        # Roll back any aborted transaction first so the session is clean.
        # Without this a deadlock (or any mid-batch DB error) leaves the
        # session in InFailedSqlTransaction and the release UPDATE below fails too.
        try:
            ctx.db.rollback()
        except Exception:
            pass
        # Release any in_progress rows we claimed but didn't finish
        # (covers pause, cancel, unexpected exception, deadlock rollback)
        try:
            still_stuck = crawler_crud.release_in_progress_states(ctx.db, tier=tier)
            if still_stuck:
                ctx.db.commit()
        except Exception as exc:
            logger.warning("release_in_progress_states failed during cleanup: %s", exc)

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

    limit_raw = ctx.params.get("limit")
    return _run_crawl_batch(
        ctx,
        tier="http",
        batch_size=int(ctx.params.get("batch_size", 20)),
        canton=ctx.params.get("canton") or None,
        max_pages=int(ctx.params.get("max_pages", 5)),
        rate_limit_delay=float(ctx.params.get("rate_limit_delay", 0.5)),
        crawl_fn=crawl_company_http,
        rerun=bool(ctx.params.get("rerun", False)),
        order_by=str(ctx.params.get("order_by", "company_id_asc")),
        limit=int(limit_raw) if limit_raw else None,
        company_timeout=float(ctx.params.get("company_timeout", 120.0)),
    )


# ── web_crawl_playwright ──────────────────────────────────────────────────────

def handle_web_crawl_playwright(ctx: JobContext) -> tuple[dict, str]:
    """Playwright crawler. Claims companies where tier='playwright' or crawl_status='js_required'."""
    from app.services.crawler_playwright import crawl_company_playwright

    limit_raw = ctx.params.get("limit")
    return _run_crawl_batch(
        ctx,
        tier="playwright",
        batch_size=int(ctx.params.get("batch_size", 10)),
        canton=ctx.params.get("canton") or None,
        max_pages=int(ctx.params.get("max_pages", 5)),
        rate_limit_delay=float(ctx.params.get("rate_limit_delay", 0.5)),
        crawl_fn=crawl_company_playwright,
        rerun=bool(ctx.params.get("rerun", False)),
        order_by=str(ctx.params.get("order_by", "company_id_asc")),
        limit=int(limit_raw) if limit_raw else None,
        company_timeout=float(ctx.params.get("company_timeout", 180.0)),
    )


# ── web_extract ───────────────────────────────────────────────────────────────

def handle_web_extract(ctx: JobContext) -> tuple[dict, str]:
    """Extract structured data from crawled HTML stored in S3.

    Claims companies with pages flagged needs_extraction=TRUE, downloads each
    page's HTML from S3, runs the deterministic extractor, and upserts one
    resolved row per company into company_web_extract. Marks pages extracted so
    they are not reprocessed. No API cost.
    """
    import json

    from app.models.company import Company
    from app.services import crawler_extract
    from app.services import s3_client as _s3
    from app.services import scoring

    batch_size = int(ctx.params.get("batch_size", 200))
    stats: dict = {"extracted": 0, "empty": 0, "s3_errors": 0, "errors": [], "rescored": 0}

    total = crawler_crud.count_companies_pending_extraction(ctx.db)
    done = 0

    while True:
        ctx.assert_not_cancelled()
        _self_preempt_if_ml_queued(ctx)

        company_ids = crawler_crud.claim_companies_for_extraction(ctx.db, batch_size=batch_size)
        if not company_ids:
            break

        # Batch-fetch name + Zefix UID + address for verification-aware extraction.
        meta_rows = ctx.db.execute(
            text(
                "SELECT id, name, uid, address_zip, address_city, web_score, "
                "google_search_results_raw, ai_score, noga_confidence, purpose_keywords "
                "FROM companies WHERE id = ANY(:ids)"
            ),
            {"ids": company_ids},
        ).fetchall()
        meta = {r[0]: r for r in meta_rows}

        for company_id in company_ids:
            ctx.assert_not_cancelled()
            try:
                pages = crawler_crud.get_extractable_pages(ctx.db, company_id)
                # All pages for one company share the same url_candidate_id
                # (delete_web_pages_for_company wipes old pages before each crawl).
                url_candidate_id = pages[0].url_candidate_id if pages else None
                # Prefer the homepage URL for domain/name matching; else first page.
                site_url = next((p.url for p in pages if p.page_type == "homepage"), None)
                if site_url is None and pages:
                    site_url = pages[0].url
                page_blobs: list[tuple[str, bytes]] = []
                for p in pages:
                    try:
                        html = _s3.download_crawl_html(p.s3_key_html)
                        page_blobs.append((p.page_type, html))
                    except Exception as exc:  # noqa: BLE001
                        stats["s3_errors"] += 1
                        logger.debug("S3 download failed for %s: %s", p.s3_key_html, exc)

                row = meta.get(company_id)
                cname = row[1] if row else None
                czefix_uid = row[2] if row else None
                czip = row[3] if row else None
                ccity = row[4] if row else None
                data = crawler_extract.resolve_company_extract(
                    page_blobs,
                    company_name=cname,
                    zefix_uid=czefix_uid,
                    site_url=site_url,
                    company_zip=czip,
                    company_city=ccity,
                ) if page_blobs else {}
                if data and url_candidate_id is not None:
                    crawler_crud.upsert_web_extract(ctx.db, company_id, url_candidate_id, data)
                    stats["extracted"] += 1

                    # Idempotent web_score adjustment: always recompute from the raw
                    # (un-adjusted) Serper score using the BEST extract across all of
                    # this company's URL candidates, so repeated re-extracts or a later
                    # extraction from a different candidate don't compound or regress it.
                    best = crawler_crud.get_best_web_extract(ctx.db, company_id)
                    raw_score_json = row[6] if row else None
                    base_score = None
                    if raw_score_json:
                        try:
                            parsed = json.loads(raw_score_json)
                            base_score = parsed[0]["score"] if parsed else None
                        except Exception:  # noqa: BLE001
                            base_score = None
                    if base_score is not None and best is not None:
                        new_web_score = scoring.adjust_web_score_for_extraction(
                            base_score,
                            uid_matches_zefix=best.uid_matches_zefix,
                            name_address_verified=bool(best.name_address_verified),
                        )
                        prev_web_score = row[5] if row else None
                        if new_web_score != prev_web_score:
                            ai_score = row[7] if row else None
                            noga_confidence = row[8] if row else None
                            purpose_keywords = row[9] if row else None
                            ctx.db.query(Company).filter(Company.id == company_id).update({
                                "web_score": new_web_score,
                                "combined_score": Company.compute_combined_score(
                                    ai_score, noga_confidence, purpose_keywords
                                ),
                            })
                            stats["rescored"] += 1
                else:
                    stats["empty"] += 1
                # Always mark processed so the queue drains even when nothing is found.
                crawler_crud.mark_pages_extracted(ctx.db, company_id)
            except Exception as exc:  # noqa: BLE001
                stats["errors"].append(f"company {company_id}: {exc}")
                logger.warning("web_extract error for company %d: %s", company_id, exc)
                ctx.db.rollback()
                # Mark processed in a fresh transaction so a poison row can't loop forever.
                try:
                    crawler_crud.mark_pages_extracted(ctx.db, company_id)
                    ctx.db.commit()
                except Exception:  # noqa: BLE001
                    ctx.db.rollback()
                continue

        ctx.db.commit()
        done += len(company_ids)
        msg = (
            f"Extracted {done}/{total} — {stats['extracted']} with data, "
            f"{stats['empty']} empty, {stats['s3_errors']} S3 errors, "
            f"{len(stats['errors'])} errors"
        )
        crud.update_progress(ctx.db, ctx.job, message=msg, done=done, total=total, stats=dict(stats))
        crud.create_event(ctx.db, job_id=ctx.job.id, level="debug", message=msg)

    done_msg = (
        f"Extraction done — {stats['extracted']} companies with structured data, "
        f"{stats['empty']} empty, {stats['s3_errors']} S3 errors, "
        f"{len(stats['errors'])} errors"
    )
    return stats, done_msg


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
            f"Company {company_id} has no Google search results stored. "
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
        crawl_company_http(
            company_id, selected.url,
            url_candidate_id=selected.id,
            max_pages=max_pages,
            rate_limit_delay=rate_limit_delay,
        )
    )

    if result.needs_playwright:
        ctx.status(f"JS rendering required — switching to Playwright for {selected.url}…")
        result = asyncio.run(
            crawl_company_playwright(
                company_id, selected.url,
                url_candidate_id=selected.id,
                max_pages=max_pages,
                rate_limit_delay=rate_limit_delay,
            )
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
