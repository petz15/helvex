# Deep Dive — Background Job System (`app/services/jobs/job_worker.py`)

Read this before reviewing any PR that touches job execution. Companion to
[risk-map.md](risk-map.md).

> **This document was rewritten.** The previous version described a 1059-line
> `_run_job` containing an inline `if/elif job.job_type == ...` chain that
> shadowed most of the `JOB_HANDLERS` registry. That migration is complete: the
> inline chain is gone, dispatch is registry-only, and `_run_job` is ~150 lines.
> If you are working from the old description, discard it.

## The execution model

There is **one** mode: every pod runs the same `_job_worker_loop` daemon thread,
which claims rows from `job_runs` and runs them. No Redis, no RQ, no
`app/worker_entrypoint.py` — those were removed and only lingered in docs.

Pods are specialised by *job type*, not by queue:

| Env var | Set on | Effect |
|---|---|---|
| `JOB_TYPE_WHITELIST` | api-worker, ml-worker, crawler-http | claim only these types |
| `JOB_TYPE_BLACKLIST` | web pod | claim everything except these |
| `DISABLE_JOB_WORKER` | web pod (when both workers are on) | claim nothing |
| `JOB_WORKER_CONCURRENCY` | per worker | jobs in flight per pod |

These lists are hand-maintained strings in the Helm templates. **A new job type
that isn't added to the right list is silently never executed** — no error, the
row just sits `queued` forever. This is the top recurring bug in this subsystem;
check it on every PR adding a job type.

## `_job_worker_loop`

A single long-lived loop, one shape at every concurrency level (at 1, the pool
just holds one slot). Per iteration it: drains finished futures → runs the stale-job
recovery sweep if `_STALE_JOB_RECOVERY_INTERVAL` has elapsed → fills every free
slot → waits for a slot to free up (or parks on `_wake_event` when idle).

Three things worth knowing:

- **Recovery is on a timer, not gated on queue depth.** It used to live inside
  the "queue is empty" branch, so it never ran at all on a pod without a
  whitelist, and a sustained backlog starved it on the others.
- **The loop never exits when idle.** It used to, then relied on
  `kick_job_worker` respawning it. `kick_job_worker` now sets `_wake_event` to
  wake the parked loop instead — an enqueue on this pod starts work immediately
  rather than waiting out `JOB_POLL_INTERVAL`.
- **Thread startup is lock-guarded** (`_worker_lock` + `_worker_threads`, keyed
  and checked with `is_alive()`). `kick_job_worker` is called from routes,
  schedulers and `enqueue_job`; two concurrent calls previously both saw
  "not running" and started two competing loops.

## Claiming: one statement, never two

`crud.claim_next_job()`:

```sql
UPDATE job_runs SET status='running', started_at=now(), last_heartbeat_at=now(), …
WHERE id = (SELECT id FROM job_runs
            WHERE status='queued' AND NOT cancel_requested AND <type filter>
            ORDER BY queued_at LIMIT 1 FOR UPDATE SKIP LOCKED)
RETURNING id
```

**Do not split this back into a peek and a claim.** The previous design did
`SELECT … FOR UPDATE SKIP LOCKED` in one session and `UPDATE` in another. Two
costs followed: the row lock was released when the select's session closed, so
`SKIP LOCKED` protected nothing and every losing pod burned a wasted round trip;
and locally a submitted job stayed `queued` until its thread actually started, so
the next poll re-drew the same row, hit the in-flight guard, and aborted slot
filling — **one slot per poll interval, so reaching concurrency N took ~5·N
seconds.**

`NOT cancel_requested` lives in the `WHERE`, not as a `SET cancel_requested=false`.
Clearing it on claim could swallow a cancel that arrived while a job was pausing
for shutdown and was then re-queued by recovery.

`get_next_queued_job()` still exists but only peeks — diagnostics and tests only.

## `_run_job(app, job_id)`

Receives an **already-claimed** job (status is already `running`). It does not
claim; calling it with an unclaimed id lets two pods run the same job.

1. **Heartbeat daemon** — stamps `last_heartbeat_at` every `_HEARTBEAT_INTERVAL`
   (30 s) on its own session. This is what lets `requeue_interrupted_jobs()` tell
   "pod died, re-queue" from "alive on another pod, leave alone".
   `_HEARTBEAT_INTERVAL` and the recovery-side `stale_after_seconds=300` are a
   **pair** — never change one alone.
2. **`_assert_not_cancelled()`** — the checkpoint every handler calls between
   units of work. Shutdown is a local flag so it trips instantly; the DB flags
   are polled at most every `_FLAG_POLL_INTERVAL` (2 s), on a **short-lived
   session**. It previously did `db.refresh(job)` on the handler's own session:
   one full-row SELECT per company on a 700k-row job, and an autoflush of the
   handler's pending state at an arbitrary point mid-batch.
3. **Dispatch** — `JOB_HANDLERS[job_type](ctx)`, registry only. An unknown type
   raises and the job is marked failed.
4. **Terminal paths** — completed / `waiting_external` / paused / cancelled /
   failed. All converge on `finally:` which stops the heartbeat and records
   `record_job_duration` (previously only the cancelled-before-start branch did,
   so the metric observed nothing for real runs).

## Pause has a reason, and it matters

`job_runs.pause_reason` is `'user' | 'shutdown' | 'preempt'` (migration 0128).
`resume_all_paused_jobs()` auto-resumes only the last two. Before this column,
that sweep — which runs at boot and every 180 s — re-queued *every* paused job,
so **a job a user paused in the UI restarted itself within ~3 minutes** and there
was no way to keep one stopped. NULL (pre-migration rows) is treated as
auto-resumable.

## Shutdown actually waits

`request_shutdown()` sets `_shutdown_event`, wakes the parked poller, and then
**blocks on `_jobs_drained`** until in-flight jobs reach a checkpoint and persist
themselves as `paused`. It used to set a flag and return, so uvicorn tore the
process down mid-batch and the jobs sat `running` until the sweep noticed the
dead heartbeat minutes later. Worker deployments set
`terminationGracePeriodSeconds: 60` so the drain fits before SIGKILL.

`_shutdown_event` is an `Event`, not a bool, specifically so it can be **cleared**
(`reset_shutdown()`, called from lifespan startup). As a write-once global, one
lifespan shutdown poisoned the whole process and every subsequent job paused at
its first checkpoint.

## Concurrency knobs multiply

In-flight work per pod is `JOB_WORKER_CONCURRENCY × the job's own fan-out`:

| Job type | Internal fan-out | Note |
|---|---|---|
| `web_crawl_http` | `crawl_concurrency`, default **10** | async httpx |
| `web_crawl_playwright` | `crawl_concurrency`, default **2** | one Chromium **each** |

So crawl throughput is tuned with `crawl_concurrency`, *not* by raising the
worker's concurrency. Raise `JOB_WORKER_CONCURRENCY` to stop a long job blocking
short ones on the same pod — crawler-http also carries `web_crawl_single`, the
interactive one — and re-check the pod memory limit against the product.

## What to check in a review touching this file

- **New job type**: registered in `JOB_HANDLERS` *and* added to the correct
  Helm whitelist/blacklist. Both, or it never runs.
- **Anything bypassing `assert_not_cancelled()`** inside a loop — pause, cancel
  and multi-pod eviction all depend on it being called at every unit boundary.
- **Heartbeat/recovery constants changed on one side only.**
- **A peek-then-claim reintroduced** anywhere in the claim path.
- **`mark_paused` called without a `reason`** — it defaults to `'shutdown'`, so a
  user-initiated pause that forgets it will silently auto-resume.

## Test coverage

`tests/test_job_dispatch.py` covers `_run_job` (dispatch, unknown type, pause
reasons, preemption, cancel, shutdown, and the `JobContext` progress surface).
`tests/test_job_recovery.py` covers claiming, pause semantics and crash recovery.
Both were written because this code previously had **zero** coverage — which is
how a broken `csv_export` progress callback (a positional arg into a
keyword-only lambda) shipped and failed every export on its first batch.
