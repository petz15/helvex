"""Job handlers for web crawling pipeline.

Three job types:
  web_url_populate   — seeds company_url_candidates from company_search_results
  web_crawl_http     — httpx crawler (crawler-http pods + ML worker idle-fill)
  web_crawl_playwright — Playwright crawler (ML worker idle-fill)
  web_select_url     — switches selected URL candidate for a company
  directory_crawl    — crawls directory profile pages (moneyhouse, local.ch, etc.)
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
from app.services.jobs.job_handlers import JobContext
from app.services.jobs.job_worker import JobPausedError

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

# Cap on the per-job sample of error strings. `stats` is json.dumps'd into
# job_runs.stats_json on EVERY batch, so an uncapped list made each write
# proportional to all errors so far — O(n^2) serialisation over a 700k-company
# run, on top of the list itself growing without bound in the worker's heap.
# A sample is all the UI shows; the full detail is in company_crawl_state.
_MAX_TRACKED_ERRORS = 50


def _track_error(stats: dict, msg: str) -> None:
    """Record an error, keeping at most _MAX_TRACKED_ERRORS samples."""
    stats["error_count"] = stats.get("error_count", 0) + 1
    errors = stats["errors"]
    if len(errors) < _MAX_TRACKED_ERRORS:
        errors.append(msg)
    elif len(errors) == _MAX_TRACKED_ERRORS:
        errors.append("… further errors omitted (see error_count)")


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
    """Seed company_url_candidates from existing company_search_results.

    **Backfill only.** Companies enriched through the Serper path already get
    their candidates and crawl state inline, via
    `web_enrichment._sync_url_candidates`. This job exists for the corpus that
    was enriched *before* that helper existed: their search results are already
    on disk, so converting them costs no API calls. Re-running Serper over those
    companies instead would re-pay for JSON already stored.

    Once the backlog clears this should find nothing. If it starts picking up
    new companies again, something enriched them without going through
    `_sync_url_candidates` — chase that rather than re-running this.

    Set-based: three statements per batch rather than five per company, and
    keyset-paginated on company id. The previous LIMIT/OFFSET version made
    Postgres discard `offset` rows before every page, so the job decelerated as
    it advanced (~154M discarded rows over a 392k-company run).
    """
    batch_size = int(ctx.params.get("batch_size", 500))
    stats: dict = {
        "processed": 0, "candidates_created": 0,
        "skipped_no_results": 0, "skipped_branch": 0, "errors": [],
    }

    # Exclude Zweigniederlassungen: legal_form_uid 0108 (Swiss branch) and 0111 (foreign branch).
    # These don't have independent websites — crawling them wastes quota and produces false matches.
    _BRANCH_LEGAL_FORM_UIDS_SQL = "('0108', '0111')"
    _BRANCH_NAME_FILTER = (
        "AND lower(c.name) NOT LIKE '%zweigniederlassung%' "
        "AND lower(c.name) NOT LIKE '%succursale%' "
        "AND lower(c.name) NOT LIKE '%filiale di%'"
    )
    _BASE_WHERE = (
        f"AND (c.legal_form_uid IS NULL OR c.legal_form_uid NOT IN {_BRANCH_LEGAL_FORM_UIDS_SQL}) "
        f"{_BRANCH_NAME_FILTER}"
    )

    # Skip companies already seeded. This is what makes the job incremental:
    # every company processed gains a company_crawl_state row (including the
    # no-candidate ones), so it drops out of this filter and a restart costs
    # nothing for work already done.
    _NOT_SEEDED = (
        "AND NOT EXISTS (SELECT 1 FROM company_crawl_state cs WHERE cs.company_id = c.id)"
    )

    total_q = ctx.db.execute(
        text(f"SELECT COUNT(*) FROM companies c JOIN company_search_results csr ON csr.company_id = c.id WHERE csr.results_raw IS NOT NULL {_BASE_WHERE} {_NOT_SEEDED}")  # noqa: S608
    ).scalar() or 0
    total = int(total_q)

    blocked = crawler_crud.get_effective_crawl_blocklist(ctx.db)

    # Keyset cursor, not OFFSET. Guarantees forward progress even if a batch
    # errors, without Postgres re-scanning everything before it each page.
    last_id = 0
    done = 0

    while True:
        ctx.assert_not_cancelled()
        _self_preempt_if_ml_queued(ctx)

        rows = ctx.db.execute(
            text(
                "SELECT c.id, csr.results_raw FROM companies c "
                "JOIN company_search_results csr ON csr.company_id = c.id "
                f"WHERE csr.results_raw IS NOT NULL {_BASE_WHERE} {_NOT_SEEDED} "
                "  AND c.id > :last_id "
                "ORDER BY c.id "
                "LIMIT :limit"
            ),
            {"limit": batch_size, "last_id": last_id},
        ).fetchall()

        if not rows:
            break

        now = datetime.now(timezone.utc)
        company_ids: list[int] = []
        candidate_rows: list[dict] = []
        for company_id, raw_results in rows:
            company_ids.append(int(company_id))
            candidates = crawler_crud.parse_google_results_raw(raw_results)
            if not candidates:
                stats["skipped_no_results"] += 1
                continue
            candidate_rows.extend(
                crawler_crud.build_candidate_rows(int(company_id), candidates, now)
            )

        try:
            stats["candidates_created"] += crawler_crud.bulk_upsert_url_candidates(
                ctx.db, candidate_rows
            )
            crawler_crud.bulk_select_best_candidates(ctx.db, company_ids, blocked)
            crawler_crud.bulk_create_crawl_states(ctx.db, company_ids)
            ctx.db.commit()
            stats["processed"] += len(company_ids)
        except Exception as exc:  # noqa: BLE001
            # A whole batch fails together now. The keyset cursor still advances,
            # so one bad batch can't wedge the job — re-run to pick it back up
            # (the NOT EXISTS filter makes that cheap).
            ctx.db.rollback()
            stats["errors"].append(f"batch {company_ids[0]}-{company_ids[-1]}: {exc}")
            logger.warning(
                "web_url_populate batch %d-%d failed: %s",
                company_ids[0], company_ids[-1], exc,
            )

        last_id = int(rows[-1][0])
        done += len(rows)

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
        f"{stats.get('skipped_branch', 0)} skipped (branch offices), "
        f"{len(stats['errors'])} errors"
    )
    return stats, done_msg


# ── web_crawl_http ────────────────────────────────────────────────────────────

async def _crawl_one_target(
    crawl_fn, state, candidate, *, max_pages, rate_limit_delay, company_timeout, sem, extra_kwargs=None
):
    async with sem:
        try:
            result = await asyncio.wait_for(
                crawl_fn(
                    state.company_id,
                    candidate.url,
                    url_candidate_id=candidate.id,
                    max_pages=max_pages,
                    rate_limit_delay=rate_limit_delay,
                    **(extra_kwargs or {}),
                ),
                timeout=company_timeout,
            )
            return ("ok", result, None)
        except asyncio.TimeoutError:
            return ("timeout", None, None)
        except Exception as exc:  # noqa: BLE001
            return ("error", None, exc)


async def _crawl_targets_concurrently(targets, *, crawl_fn, max_pages, rate_limit_delay, company_timeout, concurrency, on_result):
    """Crawl a batch of (state, candidate) pairs concurrently, bounded by `concurrency`.

    Each target's crawl is pure async I/O (httpx / Playwright) with a per-domain
    rate limiter, so different companies' fetches never block each other — the
    semaphore only caps how many run at once.

    Calls `on_result(state, candidate, kind, result, exc)` synchronously as soon
    as EACH crawl finishes — not once for the whole batch — so the caller can
    check for cancellation/pause at the same per-company granularity the old
    strictly-sequential loop had. Waiting for the whole batch (asyncio.gather)
    instead would gate that check behind the single slowest straggler (up to
    company_timeout), which is exactly what let a stale-job recovery sweep
    silently start a second execution of the same job underneath this one
    before this one ever noticed it had been evicted.

    If `on_result` raises (JobPausedError / JobCancelledError), remaining
    in-flight tasks are cancelled and the exception propagates.
    """
    sem = asyncio.Semaphore(max(1, concurrency))

    async def _wrapped(state, candidate, extra_kwargs):
        kind, result, exc = await _crawl_one_target(
            crawl_fn, state, candidate,
            max_pages=max_pages, rate_limit_delay=rate_limit_delay,
            company_timeout=company_timeout, sem=sem, extra_kwargs=extra_kwargs,
        )
        return state, candidate, kind, result, exc

    # Targets are (state, candidate) or (state, candidate, extra_kwargs) — the
    # third element carries per-company data the caller resolved from the DB in
    # its own thread (phase B's frontier seed), since the session cannot be
    # touched from inside the event loop.
    tasks = [
        asyncio.ensure_future(_wrapped(t[0], t[1], t[2] if len(t) > 2 else None))
        for t in targets
    ]
    try:
        for coro in asyncio.as_completed(tasks):
            state, candidate, kind, result, exc = await coro
            on_result(state, candidate, kind, result, exc)
            # `tasks` keeps every finished Task — and therefore its CrawlResult —
            # alive until the whole batch drains. on_result has already persisted
            # everything, so empty the payload now rather than holding all N
            # sites' page lists and inventories at once.
            if result is not None:
                result.pages.clear()
                result.inventory.clear()
    except BaseException:
        for t in tasks:
            t.cancel()
        raise


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
    concurrency: int = 1,
    phase: str = "identity",
    target_kwargs_fn=None,
) -> tuple[dict, str]:
    """Shared batch-crawl loop used by the HTTP, Playwright and content handlers.

    Releases stuck in_progress rows at start (crash recovery) and in a
    finally block (pause/fail recovery), so no company ever gets stranded.

    `phase` scopes every claim and release to one crawl phase, so an identity
    job and a content job can run side by side without stealing each other's
    rows. `target_kwargs_fn(db, state, candidate) -> dict` runs in this thread
    (DB-safe) to build per-company kwargs for the crawl function.
    """
    from app.services.platform import s3_client as _s3
    if _s3.is_crawl_bucket_configured():
        ok = _s3.check_crawl_s3_connectivity()
        if not ok:
            ctx.event("warning", "S3 crawl bucket unreachable or does not exist — HTML uploads disabled for this run")

    stats: dict = {
        "crawled": 0, "bot_blocked": 0, "js_required": 0,
        "http_error": 0, "timeout": 0, "no_content": 0, "errors": [],
    }

    # Release rows stuck in_progress from any previous crashed run
    released = crawler_crud.release_in_progress_states(ctx.db, tier=tier, phase=phase)
    ctx.db.commit()
    if released:
        ctx.event("info", f"Released {released} stuck in_progress rows from previous run")

    if rerun and phase == "content":
        reset_count = crawler_crud.reset_content_crawled(ctx.db, canton=canton)
        ctx.db.commit()
        if reset_count:
            ctx.event("info", f"Rerun: reset {reset_count} completed content crawls to pending")
    elif rerun and tier == "http":
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
            "WHERE crawl_phase = :phase AND ("
            "      (crawl_status = 'pending' AND tier = 'playwright') "
            "   OR crawl_status = 'js_required')"
        )
        total = int(ctx.db.execute(text(count_sql), {"phase": phase}).scalar() or 0)
    else:
        count_sql = (
            "SELECT COUNT(*) FROM company_crawl_state "
            "WHERE crawl_status = 'pending' AND tier = :tier AND crawl_phase = :phase"
        )
        total = int(ctx.db.execute(text(count_sql), {"tier": tier, "phase": phase}).scalar() or 0)

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
                phase=phase,
            )
            if not batch:
                break

            # Resolve candidates up front so a missing row doesn't consume a
            # concurrency slot below; failures here are recorded immediately.
            # target_kwargs_fn also runs here, in this thread, because it reads
            # the DB and the session must never be touched from the event loop.
            crawl_targets = []  # list of (state, candidate[, extra_kwargs])
            for state in batch:
                candidate = ctx.db.get(CompanyUrlCandidate, state.selected_url_id)
                if not candidate:
                    crawler_crud.mark_crawl_failed(ctx.db, state, "http_error", "No URL candidate found")
                    continue
                if target_kwargs_fn is not None:
                    crawl_targets.append((state, candidate, target_kwargs_fn(ctx.db, state, candidate)))
                else:
                    crawl_targets.append((state, candidate))

            def _handle_crawl_result(state, candidate, kind, result, exc):
                # Checked as each concurrent crawl completes (not once per whole
                # batch) — see _crawl_targets_concurrently for why that matters.
                ctx.assert_not_cancelled()

                if kind == "timeout":
                    crawler_crud.mark_crawl_failed(
                        ctx.db, state, "timeout",
                        f"Total crawl timeout after {company_timeout:.0f}s",
                    )
                    stats["timeout"] = stats.get("timeout", 0) + 1
                    _track_error(
                        stats,
                        f"company {state.company_id}: total timeout {company_timeout:.0f}s",
                    )
                    record_crawl_result(tier, "timeout")
                    return
                if kind == "error":
                    crawler_crud.mark_crawl_failed(ctx.db, state, "http_error", str(exc))
                    _track_error(stats, f"company {state.company_id}: {exc}")
                    record_crawl_result(tier, "error")
                    return

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
                    if phase == "content":
                        # Never delete_web_pages_for_company here — that would wipe
                        # the phase-A homepage/impressum rows the identity verdict
                        # and its extract were computed from.
                        crawler_crud.delete_content_pages_for_company(ctx.db, state.company_id)
                    else:
                        crawler_crud.delete_web_pages_for_company(ctx.db, state.company_id)
                    pages_crawled = []
                    fetched_urls: set[str] = set()
                    for i, p in enumerate(result.pages):
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
                            discovered_via=(
                                "crawl" if phase == "content"
                                else ("homepage" if i == 0 else "nav")
                            ),
                            priority=i,
                        )
                        pages_crawled.append(p.page_type)
                        fetched_urls.add(p.url)
                    if result.inventory:
                        crawler_crud.save_page_inventory(
                            ctx.db,
                            company_id=state.company_id,
                            entries=result.inventory,
                            already_saved_urls=fetched_urls,
                        )
                    crawler_crud.mark_crawl_done(ctx.db, state, pages_crawled)
                    if phase == "content":
                        # Both phases finished — nothing more to claim for this company.
                        crawler_crud.mark_phase_done(ctx.db, state.company_id)
                    candidate.status = "crawled"
                    candidate.last_crawled_at = now
                    stats["crawled"] += 1
                    record_crawl_result(tier, "crawled")

            # Crawl the whole batch concurrently (bounded by `concurrency`) in a
            # single event loop, handling each result as it completes. DB writes
            # stay strictly sequential in this thread — only the network I/O
            # runs concurrently.
            if crawl_targets:
                asyncio.run(
                    _crawl_targets_concurrently(
                        crawl_targets, crawl_fn=crawl_fn, max_pages=max_pages,
                        rate_limit_delay=rate_limit_delay, company_timeout=company_timeout,
                        concurrency=concurrency, on_result=_handle_crawl_result,
                    )
                )

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
        f"{stats.get('error_count', 0)} unexpected errors"
    )
    return stats, done_msg


def handle_web_crawl_http(ctx: JobContext) -> tuple[dict, str]:
    """HTTP-only crawler. Claims batches via SKIP LOCKED from company_crawl_state."""
    from app.services.enrichment.crawler_http import crawl_company_http

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
        # Page processing now runs off the event loop (crawler_common.PAGE_WORKERS),
        # so this semaphore genuinely bounds in-flight sites rather than queueing
        # behind the loop's own blocking work.
        concurrency=int(ctx.params.get("crawl_concurrency", 40)),
    )


# ── web_crawl_content (phase B) ───────────────────────────────────────────────

def _make_content_target_kwargs(max_depth: int):
    """Build the per-company frontier seeder for the phase-B full-site crawl.

    The returned function runs in the job thread, never inside the event loop —
    it touches the DB session, which SQLAlchemy does not allow concurrent use of.

    The sitemap inventory phase A already persisted means phase B usually needs
    zero discovery requests: no robots.txt, no sitemap.xml, no homepage re-fetch.
    """
    def _content_target_kwargs(db, state, candidate) -> dict:
        return {
            "seed_urls": crawler_crud.get_uncrawled_inventory_urls(db, state.company_id),
            "visited_urls": crawler_crud.get_crawled_page_urls(db, state.company_id),
            "max_depth": max_depth,
        }
    return _content_target_kwargs


def handle_web_crawl_content(ctx: JobContext) -> tuple[dict, str]:
    """Phase B — full-site crawl for companies whose identity phase A confirmed.

    Claims only crawl_phase='content' rows, so it runs concurrently with the
    identity crawler without contending for the same companies.
    """
    from app.services.enrichment.crawler_http import crawl_site_full

    limit_raw = ctx.params.get("limit")
    return _run_crawl_batch(
        ctx,
        tier="http",
        phase="content",
        batch_size=int(ctx.params.get("batch_size", 10)),
        canton=ctx.params.get("canton") or None,
        # Page budget per site. Much larger than phase A's 3 — the intent here is
        # the whole website, not a fixed page set.
        max_pages=int(ctx.params.get("max_pages", 60)),
        rate_limit_delay=float(ctx.params.get("rate_limit_delay", 0.5)),
        crawl_fn=crawl_site_full,
        rerun=bool(ctx.params.get("rerun", False)),
        order_by=str(ctx.params.get("order_by", "combined_score_desc")),
        limit=int(limit_raw) if limit_raw else None,
        # A 60-page site at ~0.5 s politeness delay needs a far longer budget
        # than phase A's 3 pages.
        company_timeout=float(ctx.params.get("company_timeout", 600.0)),
        concurrency=int(ctx.params.get("crawl_concurrency", 20)),
        target_kwargs_fn=_make_content_target_kwargs(int(ctx.params.get("max_depth", 3))),
    )


# ── web_crawl_playwright ──────────────────────────────────────────────────────

def handle_web_crawl_playwright(ctx: JobContext) -> tuple[dict, str]:
    """Playwright crawler. Claims companies where tier='playwright' or crawl_status='js_required'."""
    from app.services.enrichment.crawler_playwright import crawl_company_playwright

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
        # Each concurrent slot launches a full Chromium instance — keep this
        # low by default to avoid OOM on the ML worker pod.
        concurrency=int(ctx.params.get("crawl_concurrency", 2)),
    )


# ── web_extract ───────────────────────────────────────────────────────────────

def handle_web_extract(ctx: JobContext) -> tuple[dict, str]:
    """Extract structured data from crawled HTML stored in S3.

    Claims companies with pages flagged needs_extraction=TRUE, downloads each
    page's HTML from S3, runs the deterministic extractor, and upserts one
    resolved row per company into company_web_extract. Marks pages extracted so
    they are not reprocessed. No API cost.
    """
    from app.models.company import Company
    from app.services.enrichment import crawler_extract
    from app.services.platform import s3_client as _s3
    from app.services.enrichment import website_status as ws

    batch_size = int(ctx.params.get("batch_size", 200))
    stats: dict = {"extracted": 0, "empty": 0, "s3_errors": 0, "errors": [], "rescored": 0}
    _content_enqueued = False  # kick the phase-B crawler at most once per run
    # Loaded once per run; drives the company-level website verdict below.
    _thr = ws.load_thresholds(ctx.db)

    total = crawler_crud.count_companies_pending_extraction(ctx.db)
    done = 0

    while True:
        ctx.assert_not_cancelled()
        _self_preempt_if_ml_queued(ctx)

        company_ids = crawler_crud.claim_companies_for_extraction(ctx.db, batch_size=batch_size)
        if not company_ids:
            break

        # Batch-fetch name + Zefix UID + address for verification-aware extraction —
        # one query, no N+1.
        meta_rows = ctx.db.execute(
            text(
                "SELECT c.id, c.name, c.uid, c.address_zip, c.address_city, c.web_score, "
                "c.ai_score, c.noga_confidence, c.purpose_keywords "
                "FROM companies c "
                "WHERE c.id = ANY(:ids)"
            ),
            {"ids": company_ids},
        ).fetchall()
        meta = {r[0]: r for r in meta_rows}

        for company_id in company_ids:
            ctx.assert_not_cancelled()
            try:
                pages = crawler_crud.get_extractable_pages(ctx.db, company_id)
                row = meta.get(company_id)
                cname = row[1] if row else None
                czefix_uid = row[2] if row else None
                czip = row[3] if row else None
                ccity = row[4] if row else None

                # Group pages by url_candidate_id — a company can have pages from
                # multiple candidates (primary + fallback), each producing its own
                # extract row. Processing them separately keeps the PK clean.
                pages_by_candidate: dict[int, list] = {}
                for p in pages:
                    if p.url_candidate_id is not None:
                        pages_by_candidate.setdefault(p.url_candidate_id, []).append(p)

                any_extracted = False
                for url_candidate_id, cand_pages in pages_by_candidate.items():
                    site_url = next(
                        (p.url for p in cand_pages if p.page_type == "homepage"),
                        cand_pages[0].url if cand_pages else None,
                    )
                    page_blobs: list[tuple[str, bytes]] = []
                    for p in cand_pages:
                        try:
                            html = _s3.download_crawl_html(p.s3_key_html)
                            page_blobs.append((p.page_type, html))
                        except Exception as exc:  # noqa: BLE001
                            stats["s3_errors"] += 1
                            logger.debug("S3 download failed for %s: %s", p.s3_key_html, exc)

                    # Reject candidates whose homepage HTML is a business directory
                    # listing (e.g. treuhandvergleich.ch, moneyhouse.ch profile pages).
                    # Triggers the fallback-crawl mechanism to try the next candidate.
                    if page_blobs:
                        from app.services.scoring.scoring import is_directory_page
                        homepage_html = next(
                            (h for pt, h in page_blobs if pt == "homepage"), None
                        ) or page_blobs[0][1]
                        if is_directory_page(homepage_html, site_url or ""):
                            crawler_crud.reject_url_candidate(ctx.db, url_candidate_id)
                            stats["directory_blocked"] = stats.get("directory_blocked", 0) + 1
                            logger.info(
                                "Directory page detected — rejecting candidate %d (%s)",
                                url_candidate_id, site_url,
                            )
                            stats["empty"] += 1
                            continue

                    data = crawler_extract.resolve_company_extract(
                        page_blobs,
                        company_name=cname,
                        zefix_uid=czefix_uid,
                        site_url=site_url,
                        company_zip=czip,
                        company_city=ccity,
                        page_types=[p.page_type for p in cand_pages],
                    ) if page_blobs else {}
                    if data:
                        category, probability = ws.categorize_identity(
                            data.get("confidence"), data.get("uid_matches_zefix"),
                            bool(data.get("evidence")), _thr,
                        )
                        data["identity_category"] = category
                        data["identity_probability"] = probability
                        # Cross-UID attribution: if a UID was found on the page but
                        # belongs to a different company, wire the URL to that company
                        # as a candidate and flag this extract for human review.
                        if data.get("uid_matches_zefix") is False and data.get("uid"):
                            found_uid = data["uid"]
                            other = crud.get_company_by_uid(ctx.db, found_uid)
                            if other is not None and other.id != company_id:
                                data["review_flag"] = "uid_mismatch_cross_ref"
                                other_best = crawler_crud.get_best_web_extract(ctx.db, other.id)
                                # Cross-attribute if the target company has no extract yet or
                                # only a weak one. Use a fixed threshold (0.60) rather than
                                # comparing against the current extract's (uid-mismatch-capped)
                                # confidence, which was always ≤0.35 and caused over-skipping.
                                if (other_best is None or (other_best.confidence or 0) < 0.60):
                                    page_url = site_url or (cand_pages[0].url if cand_pages else None)
                                    if page_url:
                                        try:
                                            crawler_crud.add_cross_attributed_url_candidate(
                                                ctx.db, other.id, page_url
                                            )
                                            stats["cross_attributed"] = stats.get("cross_attributed", 0) + 1
                                        except Exception:  # noqa: BLE001
                                            pass
                        crawler_crud.upsert_web_extract(ctx.db, company_id, url_candidate_id, data)
                        stats["extracted"] += 1
                        any_extracted = True
                    else:
                        stats["empty"] += 1

                if not pages_by_candidate:
                    stats["empty"] += 1

                # ── Company-level website verdict (authoritative, crawl-only) ───────────
                # Verdict.web_score is crawl-confidence-based (round(conf*100)) — more
                # principled than the old ±40/−50 delta approach. Done once per company
                # after all candidate groups finish, so it sees the best extract.
                best = crawler_crud.get_best_web_extract(ctx.db, company_id)
                if best is not None:
                    verdict = ws.compute_verdict(ctx.db, company_id, _thr)

                    update_fields: dict = {
                        "website_status": verdict.status,
                        "website_count": verdict.website_count or None,
                        "website_url": verdict.website_url,
                    }
                    if verdict.web_score is not None:
                        prev_web_score = row[5] if row else None
                        if verdict.web_score != prev_web_score:
                            ai_score = row[6] if row else None
                            noga_confidence = row[7] if row else None
                            purpose_keywords = row[8] if row else None
                            update_fields["web_score"] = verdict.web_score
                            update_fields["combined_score"] = Company.compute_combined_score(
                                ai_score, noga_confidence, purpose_keywords,
                                web_score=verdict.web_score,
                            )
                            stats["rescored"] += 1

                    ctx.db.query(Company).filter(Company.id == company_id).update(update_fields)
                    verdict_key = "verdict_" + (verdict.status or "unknown")
                    stats[verdict_key] = stats.get(verdict_key, 0) + 1
                    if (verdict.website_count or 0) >= 2:
                        stats["multi_site"] = stats.get("multi_site", 0) + 1

                    # Identity resolution (quarantine / fallback / advance to phase B)
                    # applies only while the company is still in the identity phase.
                    # After phase B the same extract re-runs over a much larger page
                    # set; without this gate a confirmed company would re-enter the
                    # fallback chain and re-queue crawls it has already passed.
                    crawl_phase = ctx.db.execute(
                        text("SELECT crawl_phase FROM company_crawl_state WHERE company_id = :cid"),
                        {"cid": company_id},
                    ).scalar()
                    in_identity_phase = crawl_phase in (None, "identity")

                    confirmed = best.uid_matches_zefix is True or (
                        best.uid_matches_zefix is None and (best.confidence or 0) >= 0.65
                    )

                    # UID-mismatch auto-quarantine: reject the wrong-site candidate
                    # so it's never re-selected. Always triggers a fallback crawl.
                    if in_identity_phase and any_extracted and best.uid_matches_zefix is False:
                        crawler_crud.reject_url_candidate(ctx.db, best.url_candidate_id)
                        stats["quarantined"] = stats.get("quarantined", 0) + 1

                    # Identity confirmed — hand the company to the phase-B full-site
                    # crawler. advance_to_content_phase is a no-op unless the row is
                    # still in the identity phase, so this is safe to re-run.
                    if in_identity_phase and any_extracted and confirmed:
                        if crawler_crud.advance_to_content_phase(ctx.db, company_id):
                            stats["advanced_to_content"] = stats.get("advanced_to_content", 0) + 1

                    # Fallback: if UID mismatch, or no UID found with low confidence,
                    # try the next untried, non-blocked URL candidate.
                    if (
                        in_identity_phase
                        and any_extracted
                        and not confirmed
                    ):
                        already_tried = set(pages_by_candidate.keys())
                        fallback = crawler_crud.get_next_crawlable_candidate(
                            ctx.db, company_id, exclude_candidate_ids=already_tried
                        )
                        if fallback is not None:
                            try:
                                ctx.enqueue_job(
                                    job_type="web_crawl_single",
                                    label=f"Fallback crawl candidate {fallback.id} for company {company_id}",
                                    params={
                                        "company_id": company_id,
                                        "url_candidate_id": fallback.id,
                                        "max_pages": 3,
                                    },
                                    org_id=ctx.job.org_id,
                                    user_id=ctx.job.user_id,
                                )
                                stats["fallbacks"] = stats.get("fallbacks", 0) + 1
                            except Exception:  # noqa: BLE001
                                pass  # Dedup hit or queue full — not fatal
                        else:
                            # Candidates exhausted and identity never confirmed —
                            # retire the company rather than leaving it pending in
                            # the identity phase forever.
                            crawler_crud.mark_phase_done(ctx.db, company_id)
                            stats["identity_exhausted"] = stats.get("identity_exhausted", 0) + 1

                # Always mark processed so the queue drains even when nothing is found.
                crawler_crud.mark_pages_extracted(ctx.db, company_id)
            except Exception as exc:  # noqa: BLE001
                _track_error(stats, f"company {company_id}: {exc}")
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

        # Companies confirmed this batch are now queued for phase B. Kick the
        # content crawler so it runs in parallel on another pod, same pattern as
        # the crawl job's auto-enqueue of extraction. Dedup is off for this type,
        # so guard with _content_enqueued rather than relying on the queue.
        if stats.get("advanced_to_content", 0) > 0 and not _content_enqueued:
            try:
                ctx.enqueue_job(
                    job_type="web_crawl_content",
                    label="Full-site content crawl (auto — identity confirmed)",
                    params={},
                    org_id=None,
                    user_id=ctx.job.user_id,
                )
                ctx.db.commit()
                _content_enqueued = True
            except Exception as exc:  # noqa: BLE001
                logger.debug("Auto-enqueue web_crawl_content skipped: %s", exc)

        msg = (
            f"Extracted {done}/{total} — {stats['extracted']} with data, "
            f"{stats['empty']} empty, {stats.get('advanced_to_content', 0)} → phase B, "
            f"{stats['s3_errors']} S3 errors, {stats.get('error_count', 0)} errors"
        )
        crud.update_progress(ctx.db, ctx.job, message=msg, done=done, total=total, stats=dict(stats))
        crud.create_event(ctx.db, job_id=ctx.job.id, level="debug", message=msg)

    done_msg = (
        f"Extraction done — {stats['extracted']} companies with structured data, "
        f"{stats.get('advanced_to_content', 0)} advanced to full-site crawl, "
        f"{stats['empty']} empty, {stats['s3_errors']} S3 errors, "
        f"{stats.get('error_count', 0)} errors"
    )
    return stats, done_msg


# ── recompute_website_status ────────────────────────────────────────────────────

def handle_recompute_website_status(ctx: JobContext) -> tuple[dict, str]:
    """Recompute company-level website_status + website_count for all checked companies.

    Authoritative, crawl-only verdict from company_web_extract — no search-snippet
    fallback (identity rework phase 3). Running this backfills any company still
    carrying a pre-phase-3 search-only verdict down to unknown (NULL) unless it
    has actually been crawled. No API/crawl cost. Chunked by id (keyset) for
    700k-row safety.
    """
    from app.models.company import Company
    from app.services.enrichment import website_status as ws

    batch_size = int(ctx.params.get("batch_size", 1000))
    thr = ws.load_thresholds(ctx.db)

    total = int(ctx.db.execute(
        text("SELECT COUNT(*) FROM company_search_results")
    ).scalar() or 0)

    stats: dict = {"updated": 0, "multi_site": 0}
    last_id = int(ctx.resume_from or 0)
    done = 0

    while True:
        ctx.assert_not_cancelled()
        _self_preempt_if_ml_queued(ctx)

        rows = ctx.db.execute(
            text(
                "SELECT c.id, c.web_score, "
                "c.ai_score, c.noga_confidence, c.purpose_keywords "
                "FROM companies c "
                "JOIN company_search_results csr ON csr.company_id = c.id "
                "WHERE c.id > :last "
                "ORDER BY c.id LIMIT :lim"
            ),
            {"last": last_id, "lim": batch_size},
        ).fetchall()
        if not rows:
            break

        for row in rows:
            cid = row[0]
            try:
                # A SAVEPOINT (not a full commit) around each row: if this row's
                # UPDATE fails (e.g. a Postgres statement_timeout from lock
                # contention with a concurrent crawler/extract job), only this
                # row's work rolls back — the rest of the already-applied rows in
                # this batch, and the outer transaction itself, stay intact. A bare
                # `except` here without this would either (a) let the failure
                # escape uncaught and kill the whole job (this handler had no
                # try/except at all before — observed dying at 174000/302329), or
                # (b) if caught and full-rolled-back naively, silently lose every
                # already-processed-but-uncommitted row earlier in this same batch.
                with ctx.db.begin_nested():
                    verdict = ws.compute_verdict(ctx.db, cid, thr)
                    update_fields: dict = {
                        "website_status": verdict.status,
                        "website_count": verdict.website_count or None,
                        "website_url": verdict.website_url,
                    }
                    if verdict.web_score != row[1]:
                        update_fields["web_score"] = verdict.web_score
                        update_fields["combined_score"] = Company.compute_combined_score(
                            row[2], row[3], row[4], web_score=verdict.web_score
                        )
                        stats["rescored"] = stats.get("rescored", 0) + 1
                    ctx.db.query(Company).filter(Company.id == cid).update(update_fields)
                stats["updated"] += 1
                verdict_key = verdict.status or "unknown"
                stats[verdict_key] = stats.get(verdict_key, 0) + 1
                if (verdict.website_count or 0) >= 2:
                    stats["multi_site"] += 1
            except Exception as exc:  # noqa: BLE001
                stats["errors"] = stats.get("errors", 0) + 1
                logger.warning("recompute_website_status failed for company %d: %s", cid, exc)
            last_id = cid

        ctx.db.commit()
        done += len(rows)
        msg = (
            f"Recomputed {done}/{total} — "
            f"{stats.get('verified', 0)} verified, {stats.get('confirmed', 0)} confirmed, "
            f"{stats.get('likely', 0)} likely, {stats.get('unknown', 0)} unknown (not yet crawled), "
            f"{stats['multi_site']} multi-site, {stats.get('errors', 0)} errors"
        )
        crud.update_progress(ctx.db, ctx.job, message=msg, done=done, total=total, stats=dict(stats))
        crud.create_event(ctx.db, job_id=ctx.job.id, level="debug", message=msg)

    return stats, (
        f"Website status recomputed for {stats['updated']} companies "
        f"({stats['multi_site']} with multiple websites, {stats.get('errors', 0)} errors)"
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
      url_candidate_id  (optional) — target a specific URL candidate without changing the
                                     selection; used by the web_extract fallback mechanism.
                                     When set, existing pages are preserved (not deleted).
      max_pages         (optional, default 5)
      rate_limit_delay  (optional, default 0.5)
      force             (optional, default false) — re-crawl even if already crawled

    Flow:
      1. Check company has stored search results (company_search_results) → populate candidates if needed
      2. Ensure crawl_state exists with a selected URL (or use the specified candidate)
      3. Try HTTP crawler; escalate to Playwright if js_required
      4. Store results
    """
    from app.crud import company_search_result as csr_crud
    from app.models.company import Company
    from app.services.enrichment.crawler_http import crawl_company_http
    from app.services.enrichment.crawler_playwright import crawl_company_playwright

    company_id = int(ctx.params["company_id"])
    target_candidate_id: int | None = (
        int(ctx.params["url_candidate_id"]) if ctx.params.get("url_candidate_id") else None
    )
    max_pages = int(ctx.params.get("max_pages", 5))
    rate_limit_delay = float(ctx.params.get("rate_limit_delay", 0.5))
    force = bool(ctx.params.get("force", False))

    # ── Step 1: ensure URL candidates exist ──────────────────────────────
    ctx.status(f"Checking URL candidates for company {company_id}…")
    company = ctx.db.get(Company, company_id)
    if not company:
        raise ValueError(f"Company {company_id} not found")

    search_result = csr_crud.get_search_result(ctx.db, company_id)
    raw_results = search_result.results_raw if search_result else None
    if not raw_results:
        raise ValueError(
            f"Company {company_id} has no Google search results stored. "
            "Run the batch enrichment job first."
        )

    candidates = crawler_crud.parse_google_results_raw(raw_results)
    if candidates:
        crawler_crud.upsert_url_candidates(ctx.db, company_id, candidates)

    # ── Resolve which candidate to crawl ─────────────────────────────────
    # target_candidate_id: crawl a specific candidate (fallback mode) without
    # changing the primary selection or deleting existing pages.
    # No target: normal flow — use/pick the selected candidate.
    is_fallback = target_candidate_id is not None
    if is_fallback:
        selected = ctx.db.get(crawler_crud.CompanyUrlCandidate, target_candidate_id)
        if not selected or selected.company_id != company_id:
            return {"company_id": company_id}, f"Candidate {target_candidate_id} not found for company {company_id}"
        if crawler_crud.is_crawl_blocked(
            selected.url, crawler_crud.get_effective_crawl_blocklist(ctx.db)
        ):
            return {"company_id": company_id}, f"Candidate {target_candidate_id} is on the crawl blocklist"
    else:
        # Select best candidate if none is selected yet
        selected = crawler_crud.get_selected_candidate(ctx.db, company_id)
        if not selected:
            selected = crawler_crud.select_best_candidate(ctx.db, company_id)
        if not selected:
            ctx.db.commit()
            return {"company_id": company_id}, f"No crawlable URL found for company {company_id}"

    # ── Step 2: ensure crawl state exists (normal mode only) ─────────────
    if not is_fallback:
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
    ctx.status(f"Crawling {selected.url}{'  (fallback candidate)' if is_fallback else ''}…")

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
        if not is_fallback:
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

    # In fallback mode: keep existing pages from other candidates intact.
    # In normal mode: wipe old pages before saving the fresh crawl.
    if not is_fallback:
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

    if not is_fallback:
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


# ── directory_crawl ────────────────────────────────────────────────────────────

# Domains we actively crawl for directory profile data.
# Subset of _DIRECTORY_DOMAINS: profile/review/business-info sites only.
# Excludes government registries (already in Zefix), job boards, real estate,
# automotive classifieds, and paywalled / irrelevant sites.
DIRECTORY_CRAWL_DOMAINS: frozenset[str] = frozenset({
    # Local / general Swiss directories — free, per-company profile pages
    "local.ch",
    "search.ch",
    "yellowpages.ch",
    "yellowpages.swiss",
    "help.ch",
    "startups.ch",
    "pappers.ch",
    # Sector-specific Swiss directories
    "treuhandvergleich.ch",
    "consultingvergleich.ch",
    "treuhandsuisse.ch",
    "treuhandsuisse-zh.ch",
    "fiduciairesuisse-vd.ch",
    "kanzleiwelten.com",
    "spheriq.ch",
    "swiss-arc.ch",
    "swissbiotech.org",
    "ofri.ch",
    "auditorstats.ch",
    "ccis.ch",
    # Review platforms
    "provenexpert.com",
    "kununu.com",
    "yelp.com",
    # Industry databases — free tiers have useful text
    "kompass.ch",
    "kompass.com",
    # Excluded (paywall / JS-heavy / low value for Swiss SMEs):
    #   moneyhouse.ch      — useful data behind login; ToS restrictions
    #   business-monitor.ch — aggregated metrics, not descriptive text
    #   northdata.*        — free tier is sparse; login required for financials
    #   bloomberg.com      — only large companies; paywalled
    #   crunchbase.com     — mostly US/tech; limited Swiss SME coverage
})

_DIR_DOMAIN_EXTRACT_SQL = """
    regexp_replace(
        split_part(
            regexp_replace(lower(url), '^https?://', ''),
            '/', 1
        ),
        '^www\\.', ''
    )
"""


def handle_directory_crawl(ctx: JobContext) -> tuple[dict, str]:
    """Crawl business directory profile pages for company context enrichment.

    Queries company_url_candidates for URLs on known directory sites, fetches
    HTML via httpx, extracts text + structured fields, and upserts into
    company_directory_data. No S3 — raw text stored directly in DB (capped).
    Feeds into Claude classification via _build_user_text() in claude_classify.py.
    """
    import asyncio

    import httpx

    from app.services.enrichment.directory_extract import extract_directory_page

    batch_size = int(ctx.params.get("batch_size", 100))
    rerun = bool(ctx.params.get("rerun", False))
    limit_raw = ctx.params.get("limit")
    limit: int | None = int(limit_raw) if limit_raw else None
    request_timeout = float(ctx.params.get("timeout", 15.0))
    rate_delay = float(ctx.params.get("rate_limit_delay", 0.3))

    # Prefer DB-managed approved list; fall back to hardcoded set if table is empty
    # (e.g. before the migration has run on a dev environment).
    db_domains = crud.get_approved_directory_crawl_domains(ctx.db)
    domain_list = list(db_domains) if db_domains else list(DIRECTORY_CRAWL_DOMAINS)

    already_done_clause = (
        ""
        if rerun
        else "AND NOT EXISTS (SELECT 1 FROM company_directory_data d WHERE d.company_id = u.company_id AND d.url = u.url)"
    )

    count_sql = text(
        f"SELECT COUNT(*) FROM company_url_candidates u "  # noqa: S608
        f"WHERE ({_DIR_DOMAIN_EXTRACT_SQL}) = ANY(:domains) "
        f"{already_done_clause}"
    )
    total = int(ctx.db.execute(count_sql, {"domains": domain_list}).scalar() or 0)
    if limit is not None:
        total = min(total, limit)

    stats: dict = {"crawled": 0, "failed": 0, "skipped": 0, "errors": []}
    offset = 0
    done = 0

    _headers = {
        "User-Agent": (
            "Mozilla/5.0 (compatible; HelvexBot/1.0; +https://helvex.ch)"
        ),
        "Accept-Language": "de-CH,de;q=0.9,fr-CH;q=0.8,en;q=0.7",
    }

    async def _fetch(url: str) -> bytes | None:
        try:
            async with httpx.AsyncClient(
                headers=_headers,
                follow_redirects=True,
                timeout=request_timeout,
                verify=True,
            ) as client:
                r = await client.get(url)
                if r.status_code < 400:
                    return r.content
        except Exception:
            pass
        return None

    while True:
        ctx.assert_not_cancelled()

        fetch_limit = batch_size if limit is None else min(batch_size, limit - done)
        if fetch_limit <= 0:
            break

        rows = ctx.db.execute(
            text(
                f"SELECT u.company_id, u.url, ({_DIR_DOMAIN_EXTRACT_SQL}) AS domain "  # noqa: S608
                f"FROM company_url_candidates u "
                f"WHERE ({_DIR_DOMAIN_EXTRACT_SQL}) = ANY(:domains) "
                f"{already_done_clause} "
                f"ORDER BY u.company_id, u.score DESC NULLS LAST "
                f"LIMIT :lim OFFSET :off"
            ),
            {"domains": domain_list, "lim": fetch_limit, "off": offset},
        ).fetchall()

        if not rows:
            break

        for company_id, url, domain in rows:
            ctx.assert_not_cancelled()
            try:
                import time as _time
                html = asyncio.run(_fetch(url))
                if rate_delay > 0:
                    _time.sleep(rate_delay)

                if html is None:
                    ctx.db.execute(
                        text(
                            "INSERT INTO company_directory_data "
                            "(company_id, url, domain, crawl_status, crawl_error, crawled_at) "
                            "VALUES (:cid, :url, :dom, 'failed', 'fetch_failed', now()) "
                            "ON CONFLICT (company_id, url) DO UPDATE SET "
                            "crawl_status='failed', crawl_error='fetch_failed', crawled_at=now()"
                        ),
                        {"cid": company_id, "url": url, "dom": domain},
                    )
                    stats["failed"] += 1
                    continue

                extracted = extract_directory_page(html, url)
                raw_text = extracted.get("raw_text")
                if not raw_text:
                    ctx.db.execute(
                        text(
                            "INSERT INTO company_directory_data "
                            "(company_id, url, domain, crawl_status, crawl_error, crawled_at) "
                            "VALUES (:cid, :url, :dom, 'failed', 'no_text', now()) "
                            "ON CONFLICT (company_id, url) DO UPDATE SET "
                            "crawl_status='failed', crawl_error='no_text', crawled_at=now()"
                        ),
                        {"cid": company_id, "url": url, "dom": domain},
                    )
                    stats["skipped"] += 1
                    continue

                ctx.db.execute(
                    text(
                        "INSERT INTO company_directory_data "
                        "(company_id, url, domain, crawl_status, raw_text, description, "
                        " rating, review_count, categories, crawled_at) "
                        "VALUES (:cid, :url, :dom, 'crawled', :raw, :desc, "
                        "        :rating, :rc, :cats, now()) "
                        "ON CONFLICT (company_id, url) DO UPDATE SET "
                        "domain=EXCLUDED.domain, crawl_status='crawled', crawl_error=NULL, "
                        "raw_text=EXCLUDED.raw_text, description=EXCLUDED.description, "
                        "rating=EXCLUDED.rating, review_count=EXCLUDED.review_count, "
                        "categories=EXCLUDED.categories, crawled_at=now()"
                    ),
                    {
                        "cid": company_id,
                        "url": url,
                        "dom": domain,
                        "raw": raw_text,
                        "desc": extracted.get("description"),
                        "rating": extracted.get("rating"),
                        "rc": extracted.get("review_count"),
                        "cats": extracted.get("categories"),
                    },
                )
                stats["crawled"] += 1

            except Exception as exc:  # noqa: BLE001
                stats["failed"] += 1
                stats["errors"].append(f"company {company_id} {url}: {exc}")
                logger.warning("directory_crawl error for company %d %s: %s", company_id, url, exc)
                try:
                    ctx.db.rollback()
                except Exception:
                    pass

        ctx.db.commit()
        done += len(rows)
        offset += len(rows)

        msg = (
            f"Directory crawl {done}/{total} — "
            f"{stats['crawled']} crawled, {stats['failed']} failed, "
            f"{stats['skipped']} empty, {len(stats['errors'])} errors"
        )
        crud.update_progress(ctx.db, ctx.job, message=msg, done=done, total=total, stats=dict(stats))
        crud.create_event(ctx.db, job_id=ctx.job.id, level="debug", message=msg)

    return stats, (
        f"Directory crawl done — {stats['crawled']} pages crawled, "
        f"{stats['failed']} failed, {stats['skipped']} empty, "
        f"{len(stats['errors'])} errors"
    )


# ── discover_directory_domains ─────────────────────────────────────────────────

# Domains never worth surfacing for directory crawl review, regardless of frequency.
# Government registries (data already in Zefix), job boards, real estate, automotive.
_DISCOVERY_SKIP_DOMAINS: frozenset[str] = frozenset({
    "zefix.admin.ch", "uid.admin.ch", "handelsregister.ch", "hr-register.ch",
    "shab.ch", "admin.ch", "companyhouse.ch",
    "jobs.ch", "jobup.ch", "jobscout24.ch", "emplois-fribourg.ch",
    "homegate.ch", "immoscout24.ch", "immowelt.ch", "flatfox.ch", "newhome.ch",
    "scout24.ch", "autolina.ch", "autoscout24.ch", "promove.ch",
    "rocketreach.co", "rocketreach.com",
    "yandex.ru",
    "wikipedia.org",
    "comparis.ch",
    "konsumentenschutz.ch", "konsumentenbewertung.ch",
    "maptons.com",
    "die-bestatter.ch", "bestatter1.ch",
    "psychologie.ch",
    "sogenda.ch",
    "bloomberg.com",
    "crunchbase.com",
    "moneyhouse.ch",
    "business-monitor.ch",
    "northdata.com", "northdata.ch", "northdata.de", "northdata.eu",
    "linkedin.com", "xing.com", "facebook.com", "instagram.com",
    "twitter.com", "x.com", "youtube.com", "tiktok.com", "pinterest.com",
    "news.google.com",
})


def handle_discover_directory_domains(ctx: JobContext) -> tuple[dict, str]:
    """Scan company_url_candidates for high-frequency domains not yet in the managed list.

    Domains appearing for >= min_companies companies that are not already blocked
    (CRAWL_BLOCKED_DOMAINS), not already in directory_crawl_domains, and not on
    the permanent skip list are inserted as pending_review for admin evaluation.

    Run this periodically (e.g. after large Google enrichment batches) to surface
    new directories appearing in search results.
    """
    from app.crud.crawler import get_high_frequency_candidate_domains

    min_companies = int(ctx.params.get("min_companies", 30))
    limit = int(ctx.params.get("limit", 200))

    ctx.status(f"Scanning URL candidates for domains appearing in ≥{min_companies} companies…")

    candidates = get_high_frequency_candidate_domains(ctx.db, min_companies=min_companies, limit=limit)

    from app.services.scoring.scoring import CRAWL_BLOCKED_DOMAINS
    existing = {
        r[0]
        for r in ctx.db.execute(
            text("SELECT value FROM directory_crawl_domains")
        ).fetchall()
    }

    stats = {"found": 0, "inserted": 0, "already_known": 0, "skipped_blocked": 0}
    for row in candidates:
        domain = row["domain"]
        count = row["company_count"]
        stats["found"] += 1

        if domain in _DISCOVERY_SKIP_DOMAINS:
            continue
        if domain in CRAWL_BLOCKED_DOMAINS and domain not in existing:
            stats["skipped_blocked"] += 1
            continue
        if domain in existing:
            # Update company_count so the review UI shows current numbers
            crud.upsert_directory_crawl_domain(
                ctx.db, domain, company_count=count,
            )
            stats["already_known"] += 1
            continue

        crud.upsert_directory_crawl_domain(
            ctx.db, domain,
            source="auto_discovered",
            company_count=count,
        )
        stats["inserted"] += 1

    ctx.db.commit()

    msg = (
        f"Discovery done — {stats['inserted']} new pending_review, "
        f"{stats['already_known']} already known (counts updated), "
        f"{stats['skipped_blocked']} skipped (blocked), "
        f"{stats['found']} total candidates scanned"
    )
    ctx.event("info", msg)
    return stats, msg
