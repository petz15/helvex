# Manual Code Review — Risk Map

Generated from the code-review-graph knowledge graph (Leiden community detection,
hub/bridge centrality, untested-hotspot detection) cross-referenced against
`ROADMAP.md`'s known bug list. Use this to prioritize review time — start at
the top, not at file #1 in your editor's tree view.

See also: [job-system-deep-dive.md](job-system-deep-dive.md),
[billing-worldline-deep-dive.md](billing-worldline-deep-dive.md),
[company-filtering-deep-dive.md](company-filtering-deep-dive.md).

## Why these and not others

A function lands here for one or more of: **(a)** very high connectivity (many
things break if its contract changes), **(b)** no test coverage detected,
**(c)** tied to a bug already listed in `ROADMAP.md`, **(d)** large enough
(150+ lines) that a reviewer can't hold the whole thing in their head at once.

## Priority 1 — confirmed live bugs

| Area | File | Why it's here |
|---|---|---|
| **`JOB_HANDLERS` registry: ~27 dead entries, one confirmed-fragile default** | `app/services/jobs/job_worker.py` inline `elif` chain vs. `app/services/jobs/job_handlers/*.py` | `JOB_HANDLERS["bulk"]`, `["claude_classify"]`, `["csv_export"]`, and ~24 others are registered but unreachable — an earlier inline `elif job.job_type == "...":` in `_run_job` always matches first. Traced the one place this looked like a live bug (`handle_bulk`'s dead `active_only=False` default vs. the live inline branch's `active_only=True` default): **checked the frontend and the only HTTP trigger route (`POST /collection/bulk` → `BulkImportBody.active_only: bool = False`) and confirmed every real call site always writes `active_only` explicitly into the job's stored `params`** (the web form's checkbox, `rerun_job`'s reload of stored `params_json`, and the Pydantic default all resolve to an explicit value before `_run_job` ever sees it) — so the mismatched fallback defaults are currently unreachable dead code, not a live behavioral bug. Still worth cleaning up: two disagreeing defaults sitting in dead/near-dead code is a landmine for the next person who adds a new caller that omits the param. See job-system deep-dive for the full reachable/dead job-type list. |
| Worldline subscription webhook | `app/api/routes/billing/webhooks.py::worldline_return` (295 lines, in-route business logic) | 195 connections, **untested**. `ROADMAP.md` already lists "existing subscription then upgrading is not working" — this is the exact codepath. See deep-dive for the specific comparison against the Stripe path. |
| `apply_successful_payment` | `app/services/billing/payment_transactions.py:304` | Sets `org.tier` + grants `upgrade_proration_credits` for Worldline subscriptions — proration logic has no Stripe equivalent (`apply_subscription_update`). If the upgrade bug is server-side, this asymmetry is the first place to look. |

## Priority 2 — highest blast radius, no tests

Ranked by graph connectivity (`get_hub_nodes`); "untested" = flagged by the
knowledge-gaps scan (no test file references it).

| Function | Connections | File | Note |
|---|---|---|---|
| `_run_job` | 394 | `app/services/jobs/job_worker.py:323` | The job dispatcher. 1059-line single function (noqa C901 — complexity check already suppressed). Its inline `elif` chain silently shadows most of the `JOB_HANDLERS` registry — see Priority 1 and the job-system deep-dive. |
| `get_db` | 270 | `app/database.py:34` | FastAPI DB session dependency — expected to be widely referenced, low individual risk, but any change here is a whole-app blast radius. |
| `worldline_return` | 195 | `billing/webhooks.py:93` | See Priority 1. |
| `run_batch_collect` | 192 | `app/services/enrichment/web_enrichment.py` | Core Google-search enrichment loop. |
| `_progress` | 189 | `app/services/jobs/job_worker.py` | Progress-callback closure inside `_run_job` — tightly coupled to the god function above. |
| `_apply_filters` | 180 | `app/crud/company.py:82` | Single 236-line procedural filter builder for the company list/export/alert-sweep queries — every dashboard filter and CSV export goes through this. See company-filtering deep-dive for a specific index-coverage concern at 700k rows. |
| `claude_classify_batch` | 128 | `app/services/scoring/scoring/claude_classify.py` | AI scoring batch job — costs real money per call (~$0.25/1k companies per CLAUDE.md); a bug here has a $ blast radius, not just a correctness one. |
| `search_persons` | 127 | `app/api/routes/persons.py` | |
| `get_current_user` | 104 | `app/auth.py` | Also the #1 bridge/chokepoint node (see below) — auth path. |

## Priority 3 — architectural chokepoints (bridge nodes)

These aren't necessarily complex themselves, but betweenness-centrality
analysis says they sit on the shortest path between many otherwise-unrelated
parts of the app. A bug here doesn't just break one feature — it can sever
connectivity between several.

- `app/auth.py::get_current_user`, `_auth_info_from_request`, `_get_cached_user`, `require_superadmin`
- `app/services/jobs/job_worker.py::enqueue_job`, `_enqueue_job_in_session`
- `app/api/routes/jobs.py::_enqueue_or_http_error`
- `app/api/deps.py::get_current_org`

Practical implication: if a PR touches any of these, review it more carefully
than the diff size alone would suggest — `git blame`-style "small diff, small
risk" heuristics don't hold here.

## Priority 4 — largest files (decomposition candidates, not necessarily bugs)

Line counts from `find_large_functions` (threshold 150 lines). Size alone
isn't a defect, but it's where reviewers most often miss things on a skim:

| File | Lines |
|---|---|
| `app/api/routes/jobs.py` | 2055 |
| `app/services/jobs/job_worker.py` | 1784 (incl. `_run_job` at 1059) |
| `app/services/ml/cluster_pipeline.py` | 1422 |
| `app/api/routes/admin/__init__.py` | 1407 |
| `app/crud/company.py` | 1317 |
| `app/services/jobs/job_handlers/web_crawl.py` | 1303 |
| `app/services/ingestion/zefix_import.py` | 1257 |
| `app/services/scoring/scoring/scoring.py` | 1256 |
| `app/services/registry/sogc_person_extractor.py` | 1166 |
| `app/services/registry/shab_archive_import.py` | 1128 |
| `app/services/billing/payments/worldline_provider.py` | 760 (`WorldlineProvider` class alone: 521) |

## Documentation gaps found while producing this map

- **`ARCHITECTURE.md` has zero mentions of Worldline/payments** despite it
  being one of the highest-risk, highest-connectivity areas of the codebase.
  `docs/payment-flows.md` covers the Saferpay API contract well, but nothing
  ties that to the route-handler code structure. Partially addressed by the
  new deep-dive doc; a short pointer section was added to `ARCHITECTURE.md`.
- **`ARCHITECTURE.md` §6 claimed** the job dispatcher's "previous 735-line
  `elif` chain" was "replaced" by a handler registry pattern. Understated
  the actual issue — it's not that the migration is "partial," it's that
  ~27 `JOB_HANDLERS` entries are dead code shadowed by the inline chain. See
  Priority 1 above and the job-system deep-dive. Corrected in `ARCHITECTURE.md`.
- **Frontend check (`.code-review-graphignore` didn't exclude `frontend/`, but
  the graph index hadn't been rebuilt since it was added — 0 frontend nodes
  were indexed before this pass; rebuilt with `full_rebuild=True`, now 1006
  frontend nodes across `.ts`/`.tsx`/`.js`).** Checked whether the frontend
  has an equivalent shadowing problem or masks/exposes the `bulk`
  `active_only` default question — it doesn't have the shadowing pattern,
  but tracing it is what downgraded the `bulk` finding above from "live bug"
  to "unreachable dead default": the frontend form and the only API route
  always send `active_only` explicitly, so neither backend default is ever
  actually consulted. One minor, unrelated doc-drift item found in passing:
  `frontend/.../collection-client.tsx`'s `ML_JOB_DOCS` reference panel lists
  a `cluster_drift_check` job type that doesn't exist anywhere in the
  backend (no route, no `JOB_HANDLERS` entry, no inline `elif`) — harmless,
  since that panel is purely descriptive with no trigger button, but it's a
  stale entry (planned-and-never-built, or removed-and-not-cleaned-up).
- **`CLAUDE.md`'s job-system description is stale**: it describes a `USE_RQ`
  env var and separate RQ worker mode. Neither exists in the current
  codebase (`grep` for `USE_RQ`/`worker_entrypoint` returns nothing) —
  `ARCHITECTURE.md` already correctly says thread-only. Worth a one-line fix
  in `CLAUDE.md` next time it's touched (not changed here, since it's your
  instructions file rather than generated documentation).
- **`docker-compose.yml` no longer exists in the repo**, but both
  `CLAUDE.md` ("Docker Compose (full stack)" section:
  `docker compose up --build`) and `ARCHITECTURE.md`'s directory layout
  (`docker-compose.yml # Local dev (app + postgres + nginx)`) still document
  it as the local-dev path. `git log -- docker-compose.yml` shows it existed
  and was removed in a prior, already-committed change — not a work-in-progress
  deletion. If local dev has genuinely moved to a different flow (K8s dev
  cluster only?), both docs need updating; if not, the file needs restoring.
  Not changed here — this is a judgment call about which local-dev story is
  actually current, not something inferable from the code alone.
- **Two stale "Redis" comments fixed in passing** (`app/auth.py`,
  `app/services/platform/providers/gemini.py`) — both described Redis-backed paths
  that don't exist; the actual implementations are in-memory-only and
  Postgres-only respectively. No behavior change.
- **`.github/workflows/deploy-dev.yml` still provisions `REDIS_URL`/
  `REDIS_PASSWORD` into the `helvex-env` k8s secret** from GitHub Actions
  repo secrets (lines ~219–220). Nothing in the app or Helm charts reads
  those keys (`grep -r REDIS_URL infra/ app/` finds only this line) — it's
  dead CI config left over from the RQ-worker era. `deploy-prod.yml` does
  *not* have this, so it's already inconsistent between environments.
  Harmless (unused secret literals) but worth deleting for clarity — flagged
  rather than changed since it's a deploy pipeline file.
