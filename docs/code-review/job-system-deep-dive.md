# Deep Dive — Background Job Dispatcher (`app/services/job_worker.py`)

Read this before reviewing any PR that touches job execution. Companion to
[risk-map.md](risk-map.md) — this function is the #1 hub node in the
codebase (394 graph connections) and has no detected test coverage.

## The shape of `_run_job(app, job_id)` (lines 323–1381, 1059 lines)

One function, `# noqa: C901` (the linter's complexity check is explicitly
suppressed — an acknowledgment, not an accident, that this exceeds normal
complexity limits). Its docstring still says *"`app` may be None when called
from an RQ worker"* — stale; RQ/Redis worker mode has been fully removed
(`grep -r USE_RQ app/` returns nothing, `app/worker_entrypoint.py` doesn't
exist). Harmless today, but worth deleting next time this function is
touched so it doesn't mislead a reviewer into thinking a second execution
path exists.

Execution order:

1. **Load + guard.** Fetch the `JobRun` row; if already `cancelled`, refund
   credits and exit before doing any work.
2. **Atomic claim** (`crud.atomic_claim_job`) — a single
   `UPDATE job_runs SET status='running' WHERE id=? AND status IN
   ('queued','paused')`. If `rowcount == 0`, another pod already claimed it
   and this call returns immediately. This is the entire multi-pod
   safety mechanism for job execution — there is no distributed lock beyond
   this one UPDATE's atomicity.
3. **Heartbeat daemon** — a background thread stamps `last_heartbeat_at`
   every 30s. This is what lets `requeue_interrupted_jobs()` (called at
   startup) distinguish "pod died, re-queue this job" from "job is alive on
   another pod, leave it alone." If you ever change the 30s interval, check
   the recovery-side staleness threshold in the same change — they're two
   separate constants that have to stay compatible.
4. **`_assert_not_cancelled()` closure** — checkpoint function called
   throughout every job body. Raises `JobCancelledError`/`JobPausedError` if
   the DB row's flags changed, or if `job.status != "running"` (meaning a
   sibling pod's recovery pass re-queued this same job — the second
   in-flight thread yields instead of double-processing).
5. **The dispatch itself — two patterns coexist, and one silently shadows
   the other.** This is the single most important thing to understand about
   this file, and it's worse than "legacy code still around" — **most of the
   `JOB_HANDLERS` registry is unreachable dead code, and at least one
   documented behavior change never actually shipped because of it.**

   **Pattern A (inline, lines 397–1294):** a flat
   `if/elif job.job_type == "...":` chain, one branch per type — `re_geocode`,
   `recalculate_scores`, `bulk`, `batch`, `initial`, `detail`,
   `tfidf_kmeans_cluster`, `claude_classify`, `shab_daily`/`shab_backfill`,
   `csv_export`, `sogc_preprocess`, `extract_sogc_persons`,
   `noga_v2_explain`, and ~15 more.

   **Pattern B (registry, line 1296 — `elif job.job_type in _JOB_HANDLERS`):**
   looks up the type in `JOB_HANDLERS` (`app/services/job_handlers/__init__.py`),
   builds a `JobContext`, and calls the handler.

   **The problem:** `JOB_HANDLERS` registers 46 job types — including
   `bulk`, `batch`, `initial`, `detail`, `re_geocode`, `recalculate_scores`,
   `claude_classify`, `tfidf_kmeans_cluster`, `csv_export`,
   `sogc_preprocess`, `shab_daily`/`shab_backfill`, `noga_v2_explain`, and
   roughly a dozen more — **every one of which also has an earlier, exact-string
   match `elif` in Pattern A.** Python's `if/elif` takes the first branch
   that matches; since Pattern A's branches come first in source order, the
   corresponding `JOB_HANDLERS` entries for those ~27 types are **registered
   but never called.** Confirmed by grepping for callers of e.g.
   `zefix_jobs.handle_bulk`, `claude.handle_claude_classify`,
   `export.handle_csv_export` outside the registry dict itself — there are
   none. Only the job types that *aren't* in the Pattern A chain at all
   (`uid_import`, `uid_detail`, `enrich_web_purpose_sim`, `simap_*`,
   `shab_archive`, `link_sogc_stubs`, `resolve_bisher_links`,
   `repair_is_current`, and the `web_*`/`directory_crawl` crawler types) are
   genuinely reachable through Pattern B today.

   **This isn't just dead-code hygiene — it hides disagreeing defaults, though
   tracing the actual trigger path shows it isn't user-facing today.**
   Compare `app/services/job_handlers/zefix_jobs.py::handle_bulk` (dead)
   against the inline `elif job.job_type == "bulk":` block (live) in
   `_run_job`:
   ```python
   # dead — job_handlers/zefix_jobs.py:111
   active_only=ctx.params.get("active_only", False),
   # live — job_worker.py inline "bulk" branch
   active_only=params.get("active_only", True),
   ```
   `ARCHITECTURE.md` §1 documents: *"Bulk import — ... now imports both
   ACTIVE and CANCELLED/BEING_CANCELLED companies by default
   (`active_only=False`)."* That change was made in the registry version,
   which is dead code — so on first read this looked like a live,
   currently-shipping bug. **Tracing every real caller (including the
   frontend, per the review that follows) shows the mismatched fallback
   defaults are never actually reached:**
   - The only HTTP route that creates a `"bulk"` job, `POST /collection/bulk`
     → `trigger_bulk` (`app/api/routes/jobs.py:481`), takes `body.active_only`
     from `BulkImportBody` (Pydantic default `False`) and **always writes it
     explicitly** into the enqueued job's `params` — the key is never absent.
   - The frontend's "Bulk import from Zefix" form
     (`collection-client.tsx`) submits `active_only: fd.get("active_only") === "on"`
     unconditionally — always `true` or `false`, never omitted — and its
     checkbox defaults unchecked, i.e. `active_only: false` by default,
     matching the documented intent.
   - `rerun_job` re-enqueues from the original job's stored `params_json`,
     which already has the explicit value baked in.
   - `run_collector.py`'s CLI `bulk` command doesn't go through `_run_job`/
     `JOB_HANDLERS` at all — it calls `bulk_import_zefix` directly with its
     own `active_only=not args.include_inactive` computation.

   So in the current codebase, `params.get("active_only", True)` and
   `ctx.params.get("active_only", False)` are both **unreachable fallbacks**
   — real inconsistency, but not a live bug today. It becomes a real bug the
   moment anything enqueues a `"bulk"` job without going through
   `trigger_bulk` (a script, a future integration, a different admin action)
   — at that point which default applies depends entirely on which of the
   two duplicate implementations happens to still be "live" per the
   shadowing rule above. Every other Pattern A/B pair checked
   (`claude_classify`) was a faithful 1:1 port with no behavioral diff, so
   the `bulk` divergence looks like an isolated slip during the
   handler-extraction refactor rather than a systemic pattern — but it's
   exactly the kind of thing invisible from either file in isolation, and
   the frontend/API trace was necessary to tell "dead code" from "live bug."

   **Action items for the review:**
   - Decide, per job type, which implementation is the intended one, then
     delete the other. For `bulk` specifically: pick one `active_only`
     default and delete the other copy (or, better, finish the migration and
     delete the entire inline `elif` chain so there's only one copy of
     everything).
   - Don't trust `ARCHITECTURE.md`'s job-type descriptions at face value for
     any of the shadowed types without checking which implementation is
     actually reachable for that job type's real trigger path — as this case
     shows, the "intended" behavior can live entirely in the dead copy.
   - **Practical rule going forward:** any PR adding a *new* job type should
     add a handler under `job_handlers/` and register it (Pattern B) — and
     should *not* also add a matching inline `elif`, or it'll suffer the
     same fate.

6. **Success path** — `mark_completed`, emit info/warn events (capped at 10
   warnings / 50 errors so a bad job can't flood `job_run_events`), sync
   in-process state, publish an update (SSE), maybe send a completion email,
   then **conditionally bust the taxonomy/category-stats cache** — but only
   for job types listed in the hardcoded `_TAXONOMY_INVALIDATING` set
   (`claude_classify`, `reclassify_noga`, `recalculate_scores`,
   `recalculate_google_scores`, `reextract_purpose`). This set has to be
   manually kept in sync with reality: **if you add a new job type that
   changes `ai_category`/`noga_code`/scores and forget to add it here, the
   cached taxonomy/category counts silently go stale.** Worth a specific
   check during review whenever a PR adds a job type that writes score or
   category fields.
7. **Exception handling** — four cases: `_JobWaitingExternalSignal` (job
   already transitioned state, no-op), `JobPausedError` (pause or
   preemption-requeue), `JobCancelledError` (refund + mark cancelled),
   generic `Exception` (rollback, refund, mark failed, log full traceback to
   `job_run_events` at `debug` level). All four paths converge on `finally:
   _hb_stop.set()` — the heartbeat thread is always torn down.

## Why `run_batch_collect` and `_progress` also show up as hotspots

`_progress` (189 connections) is a callback closure *defined inside*
`_run_job` and threaded through to whichever service function is running
(e.g. `run_batch_collect`, 192 connections). It's how a long-running,
paginated 700k-row service function reports partial progress back up to the
DB/SSE layer without importing `job_worker` itself. If you're reviewing a
change to any long-running service function's progress-reporting signature,
check both ends — the closure's shape in `_run_job` and every call site.

## What to actually check in a review touching this file

- **New job type added as Pattern A instead of Pattern B** (see above).
- **New job type that writes scores/categories but isn't in
  `_TAXONOMY_INVALIDATING`.**
- **Anything that bypasses `_assert_not_cancelled()` checkpoints** inside a
  loop — the whole pause/cancel/multi-pod-eviction model depends on it being
  called at every "between companies" boundary (per `ARCHITECTURE.md` §6),
  not just at the top of the function.
- **Heartbeat/recovery timing constants changed on only one side** (30s
  stamp interval here vs. the staleness threshold in the startup recovery
  pass).
- No existing tests cover `_run_job` at all — a behavior change here is
  currently only checked by manual/production observation. If you're
  touching this function, adding even one test for the dispatch logic
  (e.g. "unknown job_type raises," or "cancelled-before-start refunds
  credits") is disproportionately valuable given the total lack of coverage.
