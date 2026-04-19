# Helvex — Architecture Reference

> Internal documentation for bug fixing and onboarding.
> **Stack:** FastAPI · PostgreSQL · Redis · K3s/Hetzner · Helm · Terraform · Next.js
> **Repo:** `helvex` (product name: Helvex)

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Directory Layout](#2-directory-layout)
3. [Application Layer (FastAPI)](#3-application-layer-fastapi)
4. [Database Layer](#4-database-layer)
5. [Authentication & Security](#5-authentication--security)
6. [Background Job System](#6-background-job-system)
7. [External Integrations](#7-external-integrations)
8. [Scoring & Classification Logic](#8-scoring-logic)
   - [Scoring Logic](#scoring-logic)
   - [Classification Pipelines](#classification-pipelines-clustering-keywords-noga)
9. [Frontend](#9-frontend)
10. [Configuration & Secrets](#10-configuration--secrets)
11. [Docker Build](#11-docker-build) — image types, ml-base split, Dockerfiles
12. [CI/CD Pipelines](#12-cicd-pipelines) — parallel build graph, deploy modes
13. [Kubernetes / Helm](#13-kubernetes--helm)
14. [Terraform / Hetzner](#14-terraform--hetzner)
15. [Local Development](#15-local-development)
16. [Activity Log](#16-activity-log)
17. [Common Bug-Fixing Cheatsheet](#17-common-bug-fixing-cheatsheet)
18. [Background Job System — Design Evolution](#18-background-job-system--design-evolution)

---

## 1. Project Overview

Helvex is a B2B company intelligence platform. It bulk-imports the entire Swiss commercial register (~700 k companies via the [Zefix](https://www.zefix.admin.ch) public REST API), enriches them with Google Search results, offline geocoding, TF-IDF clustering, and Claude AI scoring, and exposes them through a filterable dashboard.

**Key workflows:**
1. **Bulk import** — Zefix canton-by-canton, resumable
2. **Detail fetch + geocode** — swisstopo building-level precision
3. **Website enrichment** — Serper.dev Google Search, daily quota-aware
4. **AI scoring** — Claude Haiku via Anthropic API
5. **Dashboard / export** — filter, sort, paginate, CSV export (streaming sync or async unlimited)

---

## 2. Directory Layout

```
zefix_analyzer/
├── app/                        # Python backend (FastAPI)
│   ├── main.py                 # App factory, middleware, HTML auth routes
│   ├── config.py               # Pydantic settings (reads .env)
│   ├── auth.py                 # JWT, session cookies, rate limiting, token helpers
│   ├── database.py             # SQLAlchemy engine + session factory
│   ├── create_admin.py         # CLI: create superadmin user
│   ├── run_collector.py        # CLI: run collection jobs outside HTTP
│   ├── worker_entrypoint.py    # Entrypoint for RQ worker pod
│   ├── api/
│   │   ├── routes/
│   │   │   ├── auth.py         # /api/v1/auth/*
│   │   │   ├── companies.py    # /api/v1/companies/*
│   │   │   ├── jobs.py         # /api/v1/jobs/*
│   │   │   ├── map.py          # /api/v1/map/*
│   │   │   ├── notes.py        # /api/v1/notes/*
│   │   │   └── ops_settings.py # /api/v1/settings/*
│   │   ├── zefix_client.py     # Zefix REST API client
│   │   ├── google_search_client.py  # Serper.dev wrapper
│   │   └── geocoding_client.py # Offline geocoder (swisstopo + GeoNames)
│   ├── models/                 # SQLAlchemy ORM models
│   │   ├── user.py
│   │   ├── company.py
│   │   ├── job_run.py
│   │   ├── job_run_event.py
│   │   ├── note.py
│   │   ├── app_setting.py
│   │   ├── audit_log.py
│   │   ├── organization.py
│   │   └── boilerplate.py
│   ├── schemas/                # Pydantic request/response DTOs
│   │   ├── user.py
│   │   ├── company.py
│   │   └── note.py
│   ├── crud/                   # DB access functions (no business logic)
│   │   ├── user.py
│   │   ├── company.py
│   │   ├── job_run.py
│   │   ├── note.py
│   │   ├── app_setting.py
│   │   ├── audit_log.py
│   │   └── boilerplate.py
│   └── services/               # Business logic
│       ├── collection.py       # All data-collection pipeline steps
│       ├── scoring.py          # Zefix + Google + Claude score computation
│       ├── job_worker.py       # Job orchestration (thread + RQ modes)
│       ├── email.py            # SMTP transactional email + templates
│       ├── cluster_pipeline.py # TF-IDF K-Means clustering
│       ├── csv_export.py       # Async unlimited CSV export job logic
│       └── s3_client.py        # boto3 wrapper for helvex-exports S3 bucket
│
├── alembic/                    # Database migrations
│   ├── env.py
│   ├── versions/               # ~26 numbered migration files
│   └── ...
├── alembic.ini
│
├── frontend/                   # Next.js TypeScript frontend
│
├── tests/                      # pytest
│   ├── conftest.py
│   └── test_routes.py
│
├── infra/
│   ├── helmfile.yaml           # Helmfile — orchestrates all K8s releases
│   ├── charts/
│   │   ├── helvex/             # Main application Helm chart
│   │   ├── arc-rbac/           # GitHub ARC runner RBAC
│   │   └── monitoring/         # Prometheus + Grafana
│   ├── environments/           # Per-environment Helm values (dev, prod)
│   ├── terraform/
│   │   ├── envs/prod/          # Production TF root (main.tf, terraform.tfvars)
│   │   └── modules/            # network, servers, loadbalancer, firewall
│   └── registry/
│
├── .github/workflows/
│   ├── ci.yml                  # Path-aware lint/test (backend/frontend/ml)
│   ├── deploy-dev.yml          # Path-aware build + deploy to dev on [deploy-dev]
│   ├── deploy-prod.yml         # Selective prod deploy ([deploy-prod]/[deploy-app]/[deploy-frontend]/[deploy-backend]/[deploy-ml])
│   └── cleanup.yml             # Weekly GHCR image cleanup
│
├── Dockerfile                  # Multi-stage Python 3.12 image
├── docker-compose.yml          # Local dev (app + postgres + redis + nginx)
├── entrypoint.sh               # Docker entrypoint: runs alembic upgrade then uvicorn
├── requirements.txt
├── pyproject.toml              # pytest config
└── .env.example
```

---

## 3. Application Layer (FastAPI)

### Entry Point: `app/main.py`

**Startup sequence (lifespan handler):**
1. Alembic `upgrade head` (or `create_all` + `stamp head` on empty DB)
2. Seed default `app_settings` rows
3. Recover interrupted background jobs → kick worker thread
4. Auto-enqueue one-time re-geocode job if not already done

**Middleware stack (applied top-to-bottom):**

| Middleware | File:line | Purpose |
|---|---|---|
| `startup_gate` | `main.py:418` | Returns loading/error HTML while app initialises |
| `auth_gate` | `main.py:437` | Auth enforcement; public paths bypass it |
| `security_headers` | `main.py:463` | CSP, X-Frame-Options, HSTS, Referrer-Policy |
| Global exception handler | `main.py:236` | Returns JSON with traceback on unhandled exceptions |

**Public paths (bypass `auth_gate`):**

```python
# main.py:50-51
_PUBLIC_PREFIXES = ("/static", "/login", "/health", "/api/v1/auth")
_PUBLIC_EXACT = {"/login", "/logout", "/health", "/verify-email"}
```

**Routers mounted:**
```python
app.include_router(auth_router,      prefix="/api/v1")   # /api/v1/auth/*
app.include_router(companies_router, prefix="/api/v1")   # /api/v1/companies/*
app.include_router(notes_router,     prefix="/api/v1")   # /api/v1/notes/*
app.include_router(jobs_router,      prefix="/api/v1")   # /api/v1/jobs/*
app.include_router(map_router,       prefix="/api/v1")   # /api/v1/map/*
app.include_router(settings_router,  prefix="/api/v1")   # /api/v1/settings/*
```

---

### API Routes

#### Auth — `app/api/routes/auth.py`

| Method | Path | Auth required | Description |
|---|---|---|---|
| POST | `/api/v1/auth/token` | No | JWT login (form: username, password) |
| POST | `/api/v1/auth/register` | No | Register new user, sends verification email |
| POST | `/api/v1/auth/resend-verification` | Yes | Re-send verification email (60 s cooldown) |
| GET  | `/api/v1/auth/verify-email?token=` | No | Verify email via signed token → JSON |
| POST | `/api/v1/auth/change-password` | Yes | Change password |
| POST | `/api/v1/auth/forgot-password` | No | Request password reset email |
| POST | `/api/v1/auth/reset-password` | No | Set new password using reset token |
| GET  | `/api/v1/auth/me` | Yes | Current user info |

HTML routes (browser, in `main.py`):

| Method | Path | Description |
|---|---|---|
| GET  | `/login` | Login form |
| POST | `/login` | Process login, set session cookie |
| GET  | `/logout` | Clear cookie, redirect to /login |
| GET  | `/verify-email?token=` | Verify email, show HTML result page |
| GET  | `/register` | (Link from login page — served by frontend) |

#### Companies — `app/api/routes/companies.py`

| Method | Path | Description |
|---|---|---|
| GET  | `/api/v1/companies/zefix/search` | Live Zefix name search (not DB) |
| GET  | `/api/v1/companies/zefix/{uid}` | Raw Zefix company record |
| POST | `/api/v1/companies/zefix/import/{uid}` | Import/refresh from Zefix into DB |
| GET  | `/api/v1/companies/{id}/google-search` | Trigger Google Search for one company |
| GET  | `/api/v1/companies/stats` | Aggregate counts (review/proposal statuses) |
| GET  | `/api/v1/companies/cantons` | Distinct cantons list |
| GET  | `/api/v1/companies/taxonomy` | Scoring taxonomy config |
| GET  | `/api/v1/companies` | Paginated, filtered company list |
| GET  | `/api/v1/companies/{id}` | Single company |
| PATCH| `/api/v1/companies/{id}` | Update company fields |
| DELETE| `/api/v1/companies/{id}` | Delete company |
| GET  | `/api/v1/companies/export.csv` | Streaming CSV export (capped at 10 k rows) |

#### Jobs — `app/api/routes/jobs.py`

| Method | Path | Description |
|---|---|---|
| GET  | `/api/v1/jobs` | List all jobs |
| GET  | `/api/v1/jobs/{id}` | Job detail |
| GET  | `/api/v1/jobs/{id}/events` | Job event log |
| POST | `/api/v1/jobs/{id}/cancel` | Cancel job |
| POST | `/api/v1/jobs/{id}/pause` | Pause job |
| POST | `/api/v1/jobs/{id}/resume` | Resume job |
| GET  | `/api/v1/jobs/stream/active` | SSE stream of active job status |
| POST | `/api/v1/jobs/enqueue/bulk` | Enqueue bulk import |
| POST | `/api/v1/jobs/enqueue/initial` | Enqueue detail fetch + geocode |
| POST | `/api/v1/jobs/enqueue/batch` | Enqueue Google Search enrichment |
| POST | `/api/v1/jobs/enqueue/re-geocode` | Enqueue re-geocode all companies |
| POST | `/api/v1/jobs/enqueue/derive-industry` | Enqueue industry derivation |
| POST | `/api/v1/jobs/enqueue/tfidf-cluster` | Enqueue TF-IDF clustering |
| POST | `/api/v1/jobs/enqueue/claude-classify` | Enqueue Claude classification |
| POST | `/api/v1/jobs/enqueue/csv-export` | Enqueue unlimited async CSV export (max 1 active per user) |
| GET  | `/api/v1/jobs/csv-export/status` | Latest export status + presigned S3 download URL for current user |

#### Views — `app/api/routes/views.py`

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/api/v1/views` | Member | List saved views for current org |
| POST | `/api/v1/views` | Member | Save current filter set as a named view |
| DELETE | `/api/v1/views/{id}` | Member (owner) | Delete a saved view |
| PATCH | `/api/v1/views/{id}/alert` | Member (owner) | Enable/disable daily new-match alert for a saved view |

#### Workspace / Orgs — `app/api/routes/workspace.py`

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/api/v1/orgs/{id}/notifications` | Member | Get notification preferences (`email_notifications`) |
| PATCH | `/api/v1/orgs/{id}/notifications` | Admin/Owner | Update notification preferences |
| … | (other existing org/member routes) | | |

#### Scoring — `app/api/routes/scoring.py`

| Method | Path | Auth | Description |
|---|---|---|---|
| POST | `/api/v1/scoring/claude-preview` | Member | Dry-run Claude scoring on up to 5 companies matching current filters; rate-limited to 3 calls/min per org |

#### Billing — `app/api/routes/billing.py`

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/api/v1/billing/summary` | Member | Credit balance, tier, low-credit alert threshold |
| GET | `/api/v1/billing/credits/usage` | Member | Credit spend/refund by action over N days |
| … | (top-up, history routes) | | |

#### Admin — `app/api/routes/admin.py`

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/api/v1/admin/analytics` | Superadmin | Platform analytics: org counts, MRR estimate, top credit consumers, job volumes |
| POST | `/api/v1/admin/jobs/saved-view-alerts` | Superadmin | Manually trigger saved-view alert sweep (also runs nightly via cron) |
| … | (other existing admin routes) | | |

#### CSV Export status — `app/api/routes/jobs.py`

The `/api/v1/jobs/csv-export/status` response now includes nudge fields:

| Field | Type | Description |
|---|---|---|
| `capped` | bool | True if export was truncated at the tier row limit |
| `tier_limit` | int | The row cap that applied |
| `total_matching` | int | Total companies that matched the filters |
| `upgrade_to` | str | First higher tier that would lift the cap |

#### Other routes

| Route module | Path prefix | Summary |
|---|---|---|
| `map.py` | `/api/v1/map/bounds` | Leaflet map data (clustered, filtered) |
| `notes.py` | `/api/v1/notes` | CRUD notes linked to companies |
| `ops_settings.py` | `/api/v1/settings` | Read/write `app_settings` table |

---

## 4. Database Layer

### Technology
- **ORM:** SQLAlchemy 2.0 (mapped columns, `Session`)
- **Migrations:** Alembic (`alembic upgrade head` on every startup)
- **Driver:** psycopg2-binary
- **Config:** `app/database.py` — constructs `DATABASE_URL` from env vars or uses the `DATABASE_URL` override

```python
# database.py pattern
engine = create_engine(settings.effective_database_url)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()

def get_db() -> Generator[Session, None, None]:   # FastAPI dependency
    ...
```

### ORM Models

#### `Company` — `app/models/company.py`

The core entity. Key columns:

| Column | Type | Notes |
|---|---|---|
| `uid` | String, unique | Zefix company UID |
| `name` | String | Official company name |
| `status` | String | ACTIVE / DELETED / etc. |
| `canton` | String(2) | Two-letter canton code |
| `municipality` | String | |
| `purpose` | Text | Statutory purpose (used for scoring) |
| `address` | String | Full address string |
| `lat`, `lon` | Numeric | Geocoordinates (swisstopo / GeoNames) |
| `zefix_score` | Integer 0-100 | Computed from Zefix data |
| `website_url` | String | Top Google result URL |
| `website_match_score` | Integer 0-100 | Name/location match quality |
| `claude_score` | Integer 0-100 | Claude Haiku classification |
| `claude_category` | String | Claude-assigned category |
| `tfidf_cluster` | String | Top-3 TF-IDF terms |
| `review_status` | String | pending / confirmed / interesting / rejected |
| `proposal_status` | String | not_sent / sent / responded / converted / rejected |
| `contact_name/email/phone` | String | Outreach contact info |
| `tags` | String | Comma-separated tags |
| `zefix_raw` | Text/JSON | Raw API response |
| `zefix_score_breakdown` | JSON | Per-component score detail |

#### `User` — `app/models/user.py`

| Column | Notes |
|---|---|
| `username` | Unique login handle |
| `hashed_password` | bcrypt + SHA-256 pre-hash |
| `email` | Optional, unique |
| `email_verified` | Bool, required to access gated features |
| `tier` | free / pro / team / enterprise |
| `is_superadmin` | Bypasses tier checks |
| `org_id` | FK → organizations (team tiers) |
| `email_verification_sent_at` | Cooldown tracking |

#### `JobRun` — `app/models/job_run.py`

Persistent record of every background job. Org-scoped: each job stores the org_id of the user who triggered it.

| Column | Notes |
|---|---|
| `org_id` | FK → organizations; used to resolve per-org API keys & scoring config |
| `user_id` | FK → users; caller who triggered the job |
| `job_type` | bulk / initial / batch / re_geocode / tfidf_cluster / claude_classify / derive_industry / csv_export |
| `status` | queued → running → paused / completed / cancelled / failed |
| `cancel_requested` / `pause_requested` | Flags polled by the worker at checkpoints |
| `progress_done` / `progress_total` | Resume pointer + UI progress bar |
| `params_json` | Input params as JSON |
| `stats_json` | Output stats as JSON |
| `dedup_key` | Prevents duplicate active jobs — see §18 |
| `last_heartbeat_at` | Updated every 30 s by worker; guards against double-execution on restart — see §18 |

#### `UserView` — `app/models/user_view.py`

Saved filter sets (named dashboard views) that users can recall later.

| Column | Notes |
|---|---|
| `org_id` | FK → organizations |
| `user_id` | FK → users (owner) |
| `name` | Human-readable label |
| `filters_json` | Serialized filter params |
| `alert_enabled` | Bool (default False) — if True, daily sweep emails the owner when new matches appear |
| `alert_last_count` | Count of matching companies at last sweep; NULL until first sweep run |
| `alert_last_checked_at` | Timestamp of last sweep for this view |

Migration: `0052_add_user_view_alert_fields`

#### Other models

| Model | Table | Purpose |
|---|---|---|
| `JobRunEvent` | job_run_events | Per-job structured event log (info/warn/error/debug) |
| `Note` | notes | Free-text notes on a company, by author |
| `AppSetting` | app_settings | Key-value store for dynamic configuration |
| `OrgSetting` | org_settings | Per-org key-value overrides (e.g. `email_notifications`, `low_credit_alert_at`, `anthropic_api_key`) |
| `AuditLog` | audit_log | Field-level change tracking on company records (old/new values) |
| `ActivityLog` | activity_log | User action log — who did what, when (see §Activity Log) |
| `Organization` | organizations | Team seats |

### CRUD Layer (`app/crud/`)

Thin functions over SQLAlchemy — no business logic. Key modules:
- `crud/user.py` — `create_user`, `authenticate`, `mark_email_verified`, `update_password`, `record_verification_sent`
- `crud/company.py` — `get_company`, `list_companies` (with filters), `upsert_company`, `update_company`
- `crud/job_run.py` — `create_job`, `list_jobs`, `get_job`, `update_job_status`, `requeue_interrupted_jobs`

### Migrations (`alembic/versions/`)

~26 migration files. On startup `alembic upgrade head` runs automatically. To create a new migration:

```bash
alembic revision --autogenerate -m "describe change"
alembic upgrade head
```

---

## 5. Authentication & Security

### Two auth mechanisms (both supported simultaneously)

| Mechanism | How it works | Used by |
|---|---|---|
| **Session cookie** | `itsdangerous` URLSafeTimedSerializer, httpOnly, samesite=lax, secure on HTTPS, 8 h | Browser / HTML UI |
| **JWT Bearer token** | PyJWT HS256, same `SECRET_KEY`, 8 h expiry | API clients, frontend SPA |

Both are checked by `_user_id_from_request()` in `app/auth.py:88`.

### Token helpers — `app/auth.py`

| Function | Salt | Expiry | Purpose |
|---|---|---|---|
| `create_verification_token` | `email-verify-v1` | 24 h | Email verification link |
| `decode_verification_token` | `email-verify-v1` | 24 h | |
| `create_password_reset_token` | `password-reset-v1` | 1 h | Password reset link |
| `decode_password_reset_token` | `password-reset-v1` | 1 h | |
| `create_access_token` | (JWT, no salt) | 8 h | API Bearer token |
| `decode_access_token` | | | |

All signed with `settings.secret_key`. In prod this must be a strong 32+ character key set via `SECRET_KEY` env var; dev gets an ephemeral random key on each startup.

### Password hashing — `app/crud/user.py:11-21`

```
SHA-256(plain_text) → base64 → bcrypt(salted)
```

SHA-256 pre-hash avoids the bcrypt 72-byte truncation vulnerability for long passwords.

### Email verification gate — `app/api/deps.py`

`get_current_org` (the base dependency for every org-scoped route) chains through
`require_verified_email` before loading org state. Effect: any route that uses
`get_current_org` or `require_org_role(...)` automatically rejects unverified users
with HTTP 403. This covers all company, job, billing, workspace, and scoring routes.

Routes that only need `get_current_user` (public search, demo, webhook returns) are
**not** gated — by design.

Admin-created users (`POST /orgs/{id}/members`) and invite-accepted users
(`GET /invites/accept`) are set `email_verified = True` by the backend, so they are
never blocked.

### Rate limiting — `app/auth.py:250-324`

- Backend: Redis `INCR` + `EXPIRE` (if `REDIS_URL` is set), otherwise in-memory `defaultdict`
- Login failures: 10 attempts per IP per 15 min → locked out
- Public endpoints (`/register`, `/forgot-password`): separate per-action counters, keyed by IP

**Authenticated endpoint rate limits** (keyed by `user_<id>`, not IP):

| Route | Limit | Window | Action key |
|---|---|---|---|
| `POST /jobs/enqueue/csv-export` | 5 calls | 10 min | `job_rl:csv_export` |
| `GET /companies/export.csv` | 5 calls | 10 min | `job_rl:csv_export` |
| `POST /scoring/claude` | 20 calls | 10 min | `job_rl:claude_classify` |
| `GET /companies/{id}/google-search` | 30 calls | 10 min | `google_search` |
| `POST /scoring/claude-preview` | 3 calls | 24 h | `claude_preview:<org_id>` (pre-existing) |

Superadmins bypass all authenticated rate limits. The `_check_job_rate_limit` helper
in `jobs.py` implements the pattern for job routes; Google search uses
`check_public_rate_limit` directly with a user-keyed bucket.

### Credit deduction — CSV export

`POST /jobs/enqueue/csv-export` and `GET /companies/export.csv` now deduct credits
**before** enqueuing using `bulk_export_basic` (6,000 credits per 10k row unit).
The tier row cap is rounded up to the nearest 10k unit to get a deterministic cost.

| Tier cap | Units charged | Credits charged |
|---|---|---|
| 100 rows (free) | 1 unit | 6,000 |
| 1,000 rows (simple) | 1 unit | 6,000 |
| 5,000 rows (explorer) | 1 unit | 6,000 |
| 20,000 rows (researcher) | 2 units | 12,000 |
| 100,000 rows (strategist) | 10 units | 60,000 |

Superadmin orgs (`credits_unlimited = True`) are never blocked. Insufficient balance
returns HTTP 402 with a message indicating the cost.

### Open redirect protection — `billing.py`

`_safe_redirect_target()` now validates that the redirect URL's `netloc` matches the
`app_base_url` configured in settings. Any `success_url` or `cancel_url` pointing to
a different host is silently replaced with `app_base_url`. This prevents an attacker
who can supply a checkout body from redirecting users to an external phishing site.

### Saved view size cap — `views.py`

`POST /views` rejects payloads where:
- `name` exceeds 120 characters
- serialised `filters` JSON exceeds 64 KiB

Prevents DB bloat from crafted large-payload attacks.

### Security headers (applied to all responses)

```
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
Referrer-Policy: strict-origin-when-cross-origin
Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; ...
Strict-Transport-Security: max-age=31536000 (HTTPS only)
```

---

## 6. Background Job System

### Two modes

**Thread mode (default, `USE_RQ=false`)**
- Single daemon thread, polls `job_runs` table for `status=queued`
- Executes jobs sequentially in-process
- No external dependencies
- Set `DISABLE_JOB_WORKER=true` to suppress the thread (e.g., API-only pod)

**RQ mode (`USE_RQ=true`)**
- Jobs pushed to Redis queue
- `app/worker_entrypoint.py` runs as a separate `rq worker` process
- Deployed as a separate K8s `worker-deployment.yaml` pod
- Requires `REDIS_URL`

### Job lifecycle

```
queued → running → completed
                → failed
                → cancelled   (cancel_requested flag polled at checkpoints)
                → paused      (pause_requested flag polled at checkpoints)
                    → queued  (on resume)
```

The worker checks `cancel_requested` / `pause_requested` **between companies** (not mid-record), so pausing is clean.

### Job types

| Type | Params | What it does | Org-scoped |
|---|---|---|---|
| `bulk` | `canton`, `page_size`, `include_inactive` | Mass-import minimal company records from Zefix canton by canton | — |
| `initial` | `limit`, `run_google` | Fetch Zefix detail + geocode for companies without lat/lon | — |
| `batch` | `limit`, `refresh_zefix` | Google Search enrichment, quota-aware | — |
| `re_geocode` | — | Re-geocode all companies to building-level precision | — |
| `derive_industry` | `limit` | Re-derive industry field from taxonomy keyword mapping | — |
| `tfidf_cluster` | `n_clusters`, `limit` | TF-IDF K-Means on purpose text | ✓ Uses org-effective scoring config |
| `recalculate_scores` | `limit`, `refresh_zefix` | Recompute combined/flex/ai scores for companies | ✓ Uses org-effective config & API key |
| `claude_classify` | `limit`, `system_prompt` | Claude Haiku scoring + categorization | ✓ Validated in preflight; uses org-effective API key |
| `csv_export` | dashboard filter params | Unlimited paginated CSV written to S3 (`helvex-exports/{user_id}/export.csv`); stored 7 days | ✓ Per-user; max 1 active at a time |
| `saved_view_alerts` | — | Sweep all orgs' alert-enabled saved views; email owner if new companies match since last check | ✓ One active per org; `ONE_PER_ORG` set |

#### Org-scoped job execution

- **Job trigger**: Each user-initiated job stores `job.org_id = current_user.org_id`
- **Config resolution**: Jobs fetch their per-org settings inside the worker loop:
  - `recalculate_flex_scores(db, org_id=job.org_id)` → uses org-effective flex scoring config
  - `claude_classify` job → fetches `get_effective_setting(db, "anthropic_api_key", org_id=job.org_id)` per-job; skips jobs without a key
- **Preflight check**: Before queueing a `claude_classify` job, `_preflight_job` validates the org has both an API key and target description
- **API key never stored in job params**: The API key is fetched fresh from the database during execution, never persisted in job JSON

#### Job completion email notifications

After `mark_completed` / `mark_failed`, the worker calls `_maybe_send_job_notification`. It:
1. Checks the org's `email_notifications` setting (default on).
2. Looks up the org admin/owner email.
3. For `csv_export` completion → calls `send_export_ready(...)`.
4. For any job failure → calls `send_job_failed(...)`.

No notification is sent for intermediate states, cancellations, or job types other than export (failures apply to all types).

#### Saved view alert sweep — `app/services/saved_view_alerts.py`

Runs as `saved_view_alerts` job type (nightly via cron, or triggered manually via `POST /admin/jobs/saved-view-alerts`).

For each `UserView` with `alert_enabled=True`:
- Count matching companies using `count_companies(db, **filter_kwargs)`.
- **First run** (baseline): set `alert_last_count`, skip email.
- **Subsequent runs**: if count > `alert_last_count`, send `send_saved_view_alert(...)` to the view owner; update the stored count.
- Skips views whose owner has `email_notifications=false` or is inactive.

---

## 7. External Integrations

### Zefix API — `app/api/zefix_client.py`

- Base URL: `https://www.zefix.admin.ch/ZefixPublicREST/api/v1`
- Auth: Optional HTTP Basic (`ZEFIX_API_USERNAME` / `ZEFIX_API_PASSWORD`). The public API works without credentials but has lower rate limits.
- Key methods: `search_companies()`, `fetch_companies_by_canton()`, `get_company(uid)`

### Google Search (Serper.dev) — `app/api/google_search_client.py`

- API key: `SERPER_API_KEY`
- Daily quota: `GOOGLE_DAILY_QUOTA` (default 100; free tier ~83)
- Quota tracked in `app_settings` table; resets daily

### Geocoding — `app/api/geocoding_client.py`

Two offline data sources (no API key):

| Source | Precision | Coverage |
|---|---|---|
| swisstopo Amtliches Gebäudeadressverzeichnis | Building-level (<10 m) | CH postal addresses |
| GeoNames PLZ centroids | ~2 km | CH postcodes |

Both are downloaded and compiled into SQLite databases **at Docker build time**. No runtime downloads.

### Claude (Anthropic) — `app/services/collection.py` + `app/crud/app_setting.py`

- **API key**: Resolved per-org via `get_effective_setting(db, "anthropic_api_key", org_id=...)`
  - Falls back to global `ANTHROPIC_API_KEY` env var if no org override
  - Never exposed in APIs (replaced with `anthropic_api_key_set: bool` in frontend)
- **Model**: Claude Haiku (cheapest; ~$0.25 per 1000 companies)
- **Used for**: `claude_classify` batch job, and `claude-preview` dry-run endpoint
- **System prompt**: User-configurable via Settings API; resolved per-org
- **Preflight check** (`_preflight_job`): Validates org has API key before queueing `claude_classify`
- **Dry-run / preview**: `claude_classify_batch(dry_run=True)` scores up to 5 companies without writing to DB. Called by `POST /api/v1/scoring/claude-preview`. Rate-limited to 3 calls/min per org (Redis counter).

### Hetzner Object Storage (S3-compatible) — `app/services/s3_client.py`

Two separate buckets in region **nbg1** (`https://nbg1.your-objectstorage.com`):

| Bucket | Purpose | Owner |
|---|---|---|
| `helvex-backups` | CloudNativePG PostgreSQL WAL + base backups, 7-day PITR | Helm chart / CNPG operator |
| `helvex-exports` | Async CSV export files (`{user_id}/export.csv`), 7-day presigned URL TTL | Application (`s3_client.py`) |

Both buckets share the same `S3_ACCESS_KEY` / `S3_SECRET_KEY` credentials.

**Key design choices:**
- One file per user — re-running an export overwrites `{user_id}/export.csv` in the bucket
- Presigned URL generated fresh on each status poll (1 h expiry on the URL, 7 d on the file itself)
- `is_configured()` guard — if env vars are missing the `/enqueue/csv-export` route returns HTTP 503

**Bucket provisioning:** Hetzner Object Storage buckets are not managed by Terraform (the `hcloud` provider has no bucket resource). Create `helvex-exports` manually in the Hetzner Console under Object Storage → `nbg1`, using the same project and the same S3 credentials as `helvex-backups`.

### SMTP — `app/services/email.py`

- Config: `SMTP_HOST`, `SMTP_PORT` (587), `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`
- Protocol: STARTTLS
- In dev: silently skips if SMTP_HOST is not set
- In prod: required (enforced by `config.py` validator)
- Templates: `send_verification_email`, `send_password_reset_email`, `send_welcome_email`
- Transactional templates added for monetization/alerting:
  - `send_low_credit_alert(to, org_name, balance, threshold)` — links to `/app/billing`
  - `send_export_ready(to, row_count, job_id, download_url)` — links to S3 presigned URL
  - `send_job_failed(to, job_type, label, job_id, summary)` — table of job details
  - `send_saved_view_alert(to, view_name, new_count, previous_count, view_url)` — links to saved view
- All transactional emails are gated on the org's `email_notifications` setting (OrgSetting key). Default: on.
- Links use `APP_BASE_URL` (default `https://helvex.dicy.ch`)

---

## 8. Scoring Logic

**File:** `app/services/scoring.py`

### Zefix Score (0–100)
Computed from Zefix data alone (no external calls).
- Legal form weight (AG > GmbH > Einzelunternehmen > etc.)
- Capital declared and size
- Purpose keyword matches (configurable taxonomy weights)
- Distance from origin point (Muri bei Bern by default, configurable)

### Google / Website Match Score (0–100)
Computed when a Google Search result is found.
- Company name token overlap with domain
- Address proximity to domain registrant geolocation
- Legal form mentioned on the page
- Purpose keywords in search snippet
- Penalty for social/directory domains (LinkedIn, Facebook, local.ch, etc.)

### Claude Score (0–100)
Returned directly by Claude API. User provides the scoring rubric via system prompt.

### Combined Score (UI display)
- Weighted average: Claude 70% · Google 20% · Zefix 10%
- Components that haven't run yet are excluded; weights renormalised

### Configuring scoring
All weights and the keyword taxonomy are stored in `app_settings` and editable live via `PATCH /api/v1/settings` or the Settings UI panel.

### Classification Pipelines (Clustering, Keywords, NOGA)

**Files:** `app/services/cluster_pipeline.py`, `app/services/noga.py`, `app/services/collection.py`

---

#### ML Pipeline Overview

Four complementary ML pipelines enrich company data:

| Pipeline | Input | Output | When to run |
|---|---|---|---|
| **Keyword extraction** | `purpose` text | `purpose_keywords` per company | After initial import; incremental on new companies |
| **Semantic clustering** | `purpose_keywords` (sentence embeddings) | `tfidf_cluster` per company | Preferred method — run after keywords |
| **TF-IDF clustering** | `purpose` or `purpose_keywords` | `tfidf_cluster` per company | Faster fallback if semantic clustering is unavailable |
| **NOGA classification** | name + purpose + keywords + cluster | `noga_code`, `noga_path` | After keywords + clustering are done |

**Correct execution order:** Keywords → Clustering → NOGA. NOGA uses `purpose_keywords` and `tfidf_cluster` as input signals; running it before clustering degrades accuracy.

---

#### Text Preprocessing (shared by TF-IDF clustering algorithms)

The TF-IDF-based clustering pipelines share the same preprocessing stack:

1. **Boilerplate stripping** — removes generic legal boilerplate sentences (e.g. "Die Gesellschaft bezweckt...") using configurable DB regex patterns. Prevents generic legal terms from dominating cluster labels.
2. **Lemmatization** — spaCy `de_core_news_md` reduces words to their dictionary root. "betreibt" → "betreiben". Skipped when `use_keywords=True`.
3. **Stopword filtering** — ~30 hardcoded German legal stopwords + DB `tfidf_stopwords` table. `cluster_analysis` job identifies candidates.
4. **TF-IDF vectorization** — up to 15,000 features, unigrams + bigrams, `min_df=5`, `max_df=0.4`.
5. **Dimensionality reduction** — TruncatedSVD 50 components + L2 normalisation. Required for HDBSCAN (density requires distances); improves K-Means stability.

**`use_keywords=True` mode:** Passes pre-stored `purpose_keywords` (comma → space) directly into TF-IDF, skipping spaCy. Faster (~20 min vs ~60 min for 700K), cleaner clusters because boilerplate is already stripped. Recommended for re-clustering after keywords are established. Falls back to raw `purpose` for companies where `purpose_keywords` is NULL.

---

#### `recompute_keywords` vs `reextract_keywords`

Two functions produce `purpose_keywords`; they differ fundamentally in whether they **refit** the TF-IDF model or **reuse** a previously fitted one.

| | `recompute_keywords` | `reextract_keywords` |
|---|---|---|
| **TF-IDF model** | Fits a new model on the current corpus | Loads frozen model from S3 |
| **IDF weights** | Computed from today's full corpus | Frozen from last clustering run |
| **spaCy lemmatization** | Yes — full preprocessing pipeline | No — transforms raw/boilerplate-stripped text |
| **S3 dependency** | None (uploads a new vectorizer afterwards) | Required — aborts if artifacts missing |
| **Speed (700K)** | ~20 min | ~3–5 min |
| **Consistency with existing companies** | May shift (IDF changes when corpus changes) | Guaranteed — same IDF weights as existing companies |
| **When to use** | After large imports that change corpus composition; to reset model | After small batches of new companies; incremental use |

**Why consistency matters (the key advantage of `reextract_keywords`):**

TF-IDF IDF weights are corpus-relative. A term's "importance" is measured against all other documents. If you fit a new model only on 500 new companies, a term rare in those 500 but common in the full 700K would get an artificially high IDF score — making it appear important when it isn't. `reextract_keywords` uses the same IDF weights that were used for the existing 700K, so new companies' keywords are directly comparable and searchable alongside existing ones.

**Why `recompute_keywords` is still needed:**
IDF weights drift over time as the corpus grows. After a large import (50K+ new companies from a new industry), terms that were rare become common, changing what "important" means. Re-fitting recalibrates the model to the current reality. This also uploads a fresh S3 vectorizer, so subsequent `reextract_keywords` calls use up-to-date weights.

**Incremental use (automatic during detail import):**
`extract_keywords_incremental()` — called per-company during the `initial` detail-fetch job — is the single-document version of `reextract_keywords`. It calls `artifacts.vectorizer.transform([text])` (no fit) using the S3 model. This is why running a full clustering job before importing new companies is important: without S3 artifacts, incremental keyword extraction is silently skipped.

---

#### Clustering Algorithm Comparison

Four algorithms are available. All write to the same `tfidf_cluster` column and share the same label pipeline downstream.

| | **Semantic K-Means** (`semantic_kmeans_cluster`) | **TF-IDF K-Means** (`tfidf_kmeans_cluster`) | **HDBSCAN** (`hdbscan_cluster`) | **BIRCH** (`birch_cluster`) |
|---|---|---|---|---|
| **Function** | `run_semantic_pipeline()` | `run_pipeline()` | `run_hdbscan_pipeline()` / `run_batch_merge_hdbscan_pipeline()` | `run_birch_pipeline()` |
| **Similarity measure** | Sentence-transformer embeddings (meaning) | TF-IDF bag-of-words (vocabulary) | TF-IDF bag-of-words | TF-IDF bag-of-words |
| **k required?** | Yes — `n_clusters` (default 150) | Yes — `n_clusters` (default 150) | No — discovers automatically | Yes — `n_clusters` |
| **Memory scaling** | O(n) after embedding | O(n·k) | O(n²) single-pass / O(n) batch-merge | O(n) single-pass CF-tree |
| **Assignment type** | Soft multi-label (1–3 per company) | Soft multi-label (1–3 per company) | Hard single-label or NULL | Hard single-label |
| **Noise handling** | None — every company assigned | None — every company assigned | Outliers → NULL | None — every company assigned |
| **Speed (700K corpus)** | ~35–50 min (embedding bottleneck) | ~25 min | ~40 min batch-merge; OOM single-pass | ~20 min |
| **Cluster coherence** | **Highest** — same meaning, different words | Good — same words, different meaning missed | Best when it works; many noise points on messy text | Slightly below TF-IDF K-Means |
| **Requires** | `purpose_keywords` populated | None (raw purpose is acceptable) | None | None |
| **Artifacts saved to S3** | vectorizer (TF-IDF for labels) + SVD + centroids | vectorizer + SVD + centroids | centroids computed as cluster means | centroids computed as cluster means |

---

#### Semantic K-Means (`semantic_kmeans_cluster`) — **Recommended**

**When to use:** Production pipeline. Produces the most coherent, intuitive clusters. Companies with similar business activities group together regardless of the words they use in their purpose text.

**How it works:**
1. Each company's `purpose_keywords` (comma-separated, pre-extracted) is joined and embedded with `paraphrase-multilingual-MiniLM-L12-v2` (384-dim multilingual sentence-transformer).
2. Embeddings are reduced with TruncatedSVD (50 components) + L2 normalisation.
3. MiniBatchKMeans clusters in the reduced embedding space.
4. Cluster labels are generated via c-TF-IDF on the original keyword text (separate TF-IDF re-vectorization for readability — the clustering itself is in embedding space).
5. Multi-label soft assignment (up to 3 clusters per company, cosine similarity threshold).
6. `purpose_keywords` are preserved unchanged — this pipeline does not overwrite them.

**Requirements:** `purpose_keywords` must be populated. Run `recompute_keywords` first.

**Why it's better than TF-IDF clustering:**
A "software development" company and a "Softwareentwicklung" company land in the same cluster even though neither keyword appears in the other's purpose. TF-IDF treats them as different; embeddings understand they are the same.

---

#### TF-IDF K-Means (`tfidf_kmeans_cluster`) — **Fast fallback**

**When to use:** Full corpus runs when semantic clustering is unavailable; quick iteration when retuning cluster count.

**How it works:**
- MiniBatchKMeans on the SVD-reduced space (50 dimensions)
- Each company gets up to 3 clusters (`max_clusters_per_company=3`) via soft cosine similarity to centroids
- Low-quality clusters (mean IDF of top terms below `min_cluster_specificity=0.3`) are suppressed; companies assigned only to those become NULL
- c-TF-IDF labels: top-5 terms per cluster with bigram deduplication

**Best practice:**
1. Run `recompute_keywords` (keyword-only job) first to populate `purpose_keywords`
2. Then run K-Means with `use_keywords=True` — this gives cleaner, more domain-specific clusters because generic legal boilerplate is already absent from the keyword set
3. After clustering, run `cluster_analysis` to identify stopword candidates from cross-cluster terms

**Parameters to tune:**
- `n_clusters` (default 150): Swiss company corpus has broad industry coverage. 100–200 is the useful range. Too few → over-broad clusters ("IT dienstleistungen" merges software dev with IT support); too many → label fragmentation.
- `min_similarity` (default 0.20): Cosine similarity threshold below which a company gets NULL instead of a cluster assignment. Lower → more assignments but noisier; higher → cleaner but more NULLs.

---

#### HDBSCAN (`hdbscan_cluster`) — **Exploration / subset only**

**When to use:** Discovering the natural cluster structure of a subset; calibrating the `n_clusters` parameter for K-Means; high-quality clusters on a bounded dataset (e.g. one canton, one industry).

**Why HDBSCAN is "messy" on purpose keywords:**
1. **High noise rate** — Purpose text (even after keyword extraction) still contains ambiguous companies. HDBSCAN assigns these to label −1 (NULL), which is correct but leaves many companies unclassified.
2. **Variable cluster count** — With `min_cluster_size=30`, a 700K corpus can produce anywhere from 50 to 1000 clusters depending on density. Unpredictable for production.
3. **O(n²) memory** — Single-pass HDBSCAN on 700K companies requires ~2.3 TB for the distance matrix. The pod has 16 GB. This is why `run_batch_merge_hdbscan_pipeline` exists.
4. **Batch-merge quality loss** — The batch-merge variant (100K batches → hierarchical merge on centroids) avoids OOM but loses the global density estimation that makes HDBSCAN good. Results are similar to K-Means but with more NULLs.

**Recommended usage:**
- Set `limit=30000` to run on a representative sample — this fits in memory and takes ~3 min
- Use results to understand how many natural clusters exist and how dense they are
- Then configure K-Means `n_clusters` accordingly

**Parameters:**
- `min_cluster_size` (default 30): Minimum companies to form a cluster. Too low → many tiny fragmented clusters; too high → large coarse clusters. For 30K companies, 30–100 is sensible.
- `min_samples`: Controls how conservative cluster cores are. Lower → more points assigned (less noise). Setting to 1 behaves like single-linkage and produces very large clusters; leave as None to auto-set.
- `cluster_selection_epsilon` (default 0.0): Merge nearby clusters. Values ~0.1–0.3 reduce fragmentation. Useful if clusters are splitting hairline industry sub-niches.

---

#### BIRCH (`birch_cluster`) — **Full-corpus fast alternative to K-Means**

**When to use:** When you want a quick full-corpus clustering without S3 artifacts available; when K-Means convergence is slow; memory-constrained environments.

**How it works:**
- Builds a Clustering Feature (CF) tree in a single pass over the data — O(n) memory
- `n_clusters` controls the final agglomerative merge of CF-tree leaf nodes
- Assignment is hard single-label only (no soft multi-assignment like K-Means)

**Tradeoffs vs K-Means:**
- Faster for very large datasets (single pass vs multiple K-Means iterations)
- No multi-label assignment — each company gets exactly one cluster
- Cluster quality slightly below K-Means because CF-tree summarisation loses some information
- `threshold` parameter controls CF-tree node splitting; sklearn sets this automatically when `n_clusters` is given

**When NOT to use:**
- When you want multi-label assignment (e.g. a company that does "IT support" and "software development" should be in both clusters — K-Means handles this, BIRCH does not)
- When cluster quality matters more than speed

---

#### Recommended Workflow

```
1. Initial import (bulk + initial jobs)
       ↓
2. recompute_keywords   ← extracts purpose_keywords from raw purpose text
   (keyword-only job, ~20 min for 700K)
       ↓
3. tfidf_kmeans_cluster  use_keywords=True   ← recommended default
   (K-Means, ~25 min, all companies assigned)
       ↓                       ← optional: HDBSCAN on sample to calibrate k
4. reclassify_noga     ← uses purpose_keywords + tfidf_cluster for best accuracy
       ↓
5. claude_classify     ← AI scoring uses org-configured categories & target description
       ↓
6. recalculate_scores  ← recomputes combined_score with latest flex + AI data
```

For **ongoing imports** (daily SHAB updates): incremental keyword extraction and cluster assignment run automatically during the `initial` detail-fetch job using S3-cached model artifacts. Full re-cluster periodically (monthly or when corpus shifts significantly).

---

#### Keyword Quality

Keywords (`purpose_keywords`) are the most important intermediate artifact — they feed into clustering, NOGA classification, and the FLEX keyword scoring. Improving keyword quality cascades to all downstream outputs.

Quality depends on:
1. **Boilerplate stripping** — configure patterns in Admin → Boilerplate Settings
2. **Stopword filtering** — review `cluster_analysis` output and add cross-cluster terms to DB `tfidf_stopwords`
3. **IDF reweighting** — bigram terms penalised 15% (`bigram_penalty=0.85`) so German compound nouns don't dominate
4. **Corpus dependency** — keywords are relative to the full corpus; adding many new companies from a new industry shifts IDF weights. After large imports, re-run step 2 above.

**Monitoring:** After each clustering run, the `cluster_analysis` job writes `static/cluster_analysis.txt` listing terms appearing in 20%+ of top cluster labels — these are stopword candidates.

---

#### NOGA Taxonomy Classification (`reclassify_noga` job)

**Purpose:** Map each company to the official Swiss NOGA industry classification with full hierarchy breadcrumb.

**Data files (repo-resident):**
- `noga_lookup.json` — flat dict: NOGA code → node (name, annotations, parent link)
- `noga_tree.json` — full hierarchy tree: root sections → divisions → groups → classes → types
- NOGA embeddings — uploaded to S3 by `scripts/build_noga_embeddings.py` (one-time setup)

**Classification method:**
1. **Token matching** — company name + purpose + keywords + cluster are split into terms, matched against all NOGA node descriptions
2. **Hybrid re-rank** (if S3 embeddings available):
   - Embed company `purpose_keywords` with `paraphrase-multilingual-MiniLM-L12-v2`
   - Top-50 NOGA candidates from token matching are re-ranked by embedding similarity
   - Final score = 60% embedding cosine + 40% token score
3. **Preference for specificity** — 6-digit codes (types) preferred over shorter codes when scores are close
4. **Hierarchy path** — walk `noga_lookup.json` via `parentCode` links to build full ancestry (section → division → group → class → type)

**Outputs to DB per company:**
- `noga_code` — best-matching code (e.g. `"263001"`)
- `noga_label` — German description
- `noga_level` — level name (`"Art"`, `"Klasse"`, etc.)
- `noga_confidence` — embedding similarity of top result (0–1)
- `noga_path` — pipe-separated codes root→leaf (e.g. `"C|26|263|2630|263001"`)
- `noga_path_labels` — corresponding German labels, pipe-separated

**S3 artifacts** (produced once by `scripts/build_noga_embeddings.py`):
- `models/noga_embeddings.npy` — float32 array, shape (N_codes, 384)
- `models/noga_embedding_ids.json` — list of NOGA codes in embedding row order + shape metadata

**Graceful degradation:**
- If S3 embeddings unavailable → falls back to token-only matching (lower accuracy)
- If `purpose_keywords` empty → uses raw tokens from name + purpose
- If purpose text missing → returns no classification

---

#### ML Dependencies

**Python packages:**

| Package | Image | Purpose |
|---|---|---|
| `scikit-learn` | backend + ml | TF-IDF, TruncatedSVD, MiniBatchKMeans, Birch |
| `scipy` | backend + ml | Sparse matrix ops, hierarchical clustering in batch-merge HDBSCAN |
| `numpy` | backend + ml | Centroid math, cosine similarity |
| `spacy` + `de_core_news_md` | ml | German lemmatization (downloaded at build time) |
| `hdbscan` | **ml only** | HDBSCAN clustering (`requirements.ml.txt`) |
| `sentence-transformers` | ml | NOGA embedding (`paraphrase-multilingual-MiniLM-L12-v2`) |
| `tqdm` | ml | Progress bars in pipeline CLI |

**Important:** `hdbscan` is **not** in `requirements.backend.txt` — only in `requirements.ml.txt`. The `hdbscan_cluster` and `birch_cluster` jobs are routed to the `helvex-ml` K8s pod (`job_worker.py` line 78). Triggering them from the `helvex` (backend-only) pod will raise `ImportError`.

**K8s pod routing (from `job_worker.py`):**

```python
ML_JOB_TYPES = {"hdbscan_cluster", "birch_cluster", "tfidf_kmeans_cluster", ...}
# → routed to helvex-ml pod when USE_RQ=true
```

When `USE_RQ=false` (local dev / thread mode), all jobs run in the same process — ensure `hdbscan` is installed locally.

**Model artifacts on S3 (`helvex-exports` bucket, `models/` prefix):**

| File | Written by | Read by |
|---|---|---|
| `tfidf_vectorizer.pkl` | all clustering jobs | incremental keyword extraction on new companies |
| `svd_transformer.pkl` | all clustering jobs | incremental cluster assignment on new companies |
| `kmeans_centroids.npy` | all clustering jobs (centroids computed as cluster means for HDBSCAN/BIRCH) | incremental cluster assignment |
| `centroid_registry_map.json` | all clustering jobs | incremental cluster assignment (maps centroid index → canonical name) |
| `noga_embeddings.npy` | `build_noga_embeddings.py` (one-time) | `reclassify_noga` job |
| `noga_embedding_ids.json` | `build_noga_embeddings.py` (one-time) | `reclassify_noga` job |

---

#### Cluster Registry (`app/models/cluster_registry.py`, `app/crud/cluster_registry.py`)

The `cluster_registry` table gives each cluster a stable identity across pipeline runs.

**Why it exists:** Every pipeline run generates fresh c-TF-IDF labels (e.g. `"software,entwicklung,cloud"`). Without the registry, a minor corpus shift could change the top terms enough to produce a different string — breaking saved filter views, scoring rules, and any code that hard-references a cluster name.

**How it works:**
1. After labeling, each cluster's top terms are matched against existing active registry entries via Jaccard similarity (threshold 0.5).
2. If a match is found, the existing `canonical_name` is reused (and `top_terms` updated if they've shifted slightly).
3. If no match, a new entry is created with `canonical_name = label` (the raw c-TF-IDF string).
4. Entries not produced in the current run are marked `active = False`.

**Renaming:** `PATCH /api/v1/clusters/registry/{id}` renames the `canonical_name` and rewrites all matching `tfidf_cluster` values in the `companies` table atomically. UI: Superadmin → Clusters.

**Display formatting:** Raw cluster names are comma-separated lemmas (`"software,entwicklung,cloud"`). The frontend formats them for display as `"Software · Entwicklung · Cloud"` via `formatClusterLabel()` in `frontend/src/lib/utils.ts`. The raw string is always used as the filter/storage value — only the display is formatted.

---

## 9. Credit System & Monetization

### Overview

Credits are the unit of account for AI-powered actions (Claude scoring, web search, etc.). Each org has a balance stored in `OrgSetting(key="credit_balance")`. Credits are pre-purchased and deducted at action time.

### Core function — `app/services/credits.py`

```
check_and_deduct(db, org_id, action, count=1) → (ok: bool, balance: int)
```

- Reads the org's current balance.
- Checks the deduction amount for `action` from the `CreditCostConfig` table (or hardcoded defaults).
- Atomically deducts or returns `(False, current_balance)` if insufficient.
- Calls `_maybe_low_credit_alert(db, org_id, balance)` after every deduction.

### Low-credit alert

`_maybe_low_credit_alert` fires when balance drops below the org's `low_credit_alert_at` threshold (OrgSetting, default `None` / disabled):

1. Checks if an alert was already sent today (`OrgSetting("low_credit_alert_sent_at")`).
2. Calls `_send_low_credit_email(db, org_id, balance, threshold)`.
3. The email helper checks `email_notifications` opt-out, looks up the org admin/owner, and calls `send_low_credit_alert(...)`.

### Tier row caps (CSV export)

`csv_export.py` enforces per-tier row limits. After export completes, `stats_json` is extended with:

| Field | Meaning |
|---|---|
| `capped` | True if the export was truncated |
| `tier_limit` | The cap that applied |
| `total_matching` | Total rows that matched the filters |
| `upgrade_to` | First tier with a higher cap |

The frontend reads these on status poll and shows an upgrade nudge banner when `capped=true`.

### Billing UI (frontend)

`/app/billing` shows:
- **Low-credit alert banner** (amber) if `summary.low_credit_alert_at` is set and balance is below threshold
- **Credit Usage** section: days selector (7/30/90), spend/refund/net totals, per-action bar chart
- **Notification preferences** toggle (email_notifications)
- Credit history and payment history tables

### Security

- Credit balance and deduction are performed with a DB-level lock (select-for-update) to prevent double-spend under concurrent requests.
- The `check_and_deduct` result is not disclosed to the user — actions simply fail silently or with a generic "insufficient credits" error.
- `GET /billing/summary` and `GET /billing/credits/usage` are member-scoped (own org only).

---

## 10. Frontend

**Location:** `frontend/`
**Technology:** Next.js (TypeScript, Node.js 22)

Environment variable the frontend needs:
- `FASTAPI_URL=http://helvex:8000` — K8s service name in prod; `http://localhost:8000` locally

Build commands (see `deploy-prod.yml`):
```bash
npm ci
npx tsc --noEmit
npm run lint
npm run build
```

The Next.js image is separate (`helvex-frontend`) and served behind the same Ingress as the backend.

### Internationalization (i18n) — DE / FR / IT / EN

**Architecture:** Custom React context + JSON dictionaries (no third-party library).

**Structure:**
- `frontend/src/i18n/` — locale config + hooks
  - `locales.ts` — list of supported locales: `de`, `fr`, `it`, `en` (default `de`)
  - `context.tsx` — React context provider `I18nProvider` + `useI18n()` hook
  - `request.ts` — server-side `getDictionary(locale)` for server components
- `frontend/messages/` — JSON translation files
  - `de.json`, `fr.json`, `it.json`, `en.json` — organized by namespace
- `frontend/src/app/[locale]/` — all routes under locale-prefixed dynamic segment

**Routing:**
- URL pattern: `/{locale}/path` (e.g. `/de/app/search`, `/fr/login`, `/en/impressum`)
- Default locale (no locale prefix): redirected by `proxy.ts` (Next.js 16 middleware)
- Redirect: `/ → /de/`, `/?lang=fr → /fr/`, etc.

**Usage:**

*Server components:*
```tsx
import { getDictionary } from "@/i18n/request";
export default async function Page({ params }: { params: Promise<{ locale: string }> }) {
  const { locale } = await params;
  const dict = await getDictionary(locale);
  return <h1>{dict.app.search.title}</h1>;
}
```

*Client components:*
```tsx
"use client";
import { useI18n } from "@/i18n/context";
export function MyComponent() {
  const { dict, locale } = useI18n();
  return <button>{dict.app.search.aiPreview}</button>;
}
```

**String interpolation — `sub()` helper:**
For dynamic values in translation strings (e.g. `"Join {orgName}"`):
```tsx
function sub(template: string, vars: Record<string, string>): string {
  return Object.entries(vars).reduce((s, [k, v]) => s.replace(new RegExp(`\\{${k}\\}`, "g"), v), template);
}
const msg = sub(dict.auth.acceptInvite.invitedToJoin, { orgName: "Acme Inc." });
```

**Namespace organization (de.json structure):**
```json
{
  "nav": { "search": "Suche", ... },
  "app": {
    "search": { "title": "Suche", "aiPreview": "KI Vorschau", ... },
    "map": { "goToAddress": "Zu Adresse gehen", ... },
    "account": { "title": "Konto", ... },
    ...
  },
  "auth": {
    "login": { "title": "Anmelden", ... },
    "resetPassword": { ... },
    "acceptInvite": { ... },
    ...
  },
  "cookie": { ... },
  "legal": { ... }
}
```

**Covered pages:**
- Landing page (`/[locale]/page.tsx`)
- Auth flows: login, register, forgot-password, reset-password, verify-email, accept-invite, confirm-email-change
- Dashboard pages: search, map, addresses, jobs, categories, billing, account, collection, pricing
- Legal pages: `/impressum`, `/datenschutz`, `/agb`

**Language switcher:**
Dropdown in `nav-bar.tsx` — navigates to same path with different `locale` prefix, preserving query params.

**SEO notes:**
- `<html lang={locale}>` set in root layout
- `hreflang` alternates links (if needed, can be added to `generateMetadata`)

---

## 10. Configuration & Secrets

### Environment variables — `app/config.py`

All config is loaded from `.env` (or process env) by `pydantic-settings`. The `Settings` class enforces prod requirements via `@model_validator`.

| Variable | Required in prod | Notes |
|---|---|---|
| `APP_ENV` | — | `dev` / `prod` / `staging` |
| `DATABASE_URL` | Yes (or individual PG vars) | Full postgres:// URL |
| `POSTGRES_HOST/PORT/USER/PASSWORD/DB` | Yes | Used if DATABASE_URL is empty |
| `SECRET_KEY` | Yes (≥32 chars) | JWT + session signing |
| `SMTP_HOST` | Yes | |
| `SMTP_FROM` | Yes | Display name + address |
| `SMTP_USER` / `SMTP_PASSWORD` | Yes | SMTP auth |
| `APP_BASE_URL` | Yes | Used in email links |
| `SERPER_API_KEY` | No | Google Search (jobs fail gracefully without it) |
| `ANTHROPIC_API_KEY` | No | Claude classification |
| `REDIS_URL` | No | Required if `USE_RQ=true` |
| `S3_ACCESS_KEY` | No* | Hetzner Object Storage key (shared by backup + export buckets) |
| `S3_SECRET_KEY` | No* | Hetzner Object Storage secret |
| `S3_ENDPOINT_URL` | No* | e.g. `https://nbg1.your-objectstorage.com` |
| `S3_BUCKET_EXPORTS` | No* | `helvex-exports` — async CSV export storage; *required for csv_export job type |
| `USE_RQ` | No | `false` by default |
| `DISABLE_JOB_WORKER` | No | `false` by default |
| `ZEFIX_API_USERNAME/PASSWORD` | No | Optional HTTP Basic for Zefix |

### Secrets in Kubernetes

All env vars are bundled into a single Kubernetes `Secret` named **`helvex-env`** (referenced in `charts/helvex/values.yaml: envSecretName`).

The secret is created by the **deploy pipeline** (`deploy-prod.yml`) from GitHub Actions secrets:

```yaml
# deploy-prod.yml (simplified)
kubectl create secret generic helvex-env \
  --from-literal=APP_ENV=prod \
  --from-literal=DATABASE_URL=${{ secrets.DATABASE_URL }} \
  --from-literal=SECRET_KEY=${{ secrets.SECRET_KEY }} \
  --from-literal=SMTP_HOST=${{ secrets.SMTP_HOST }} \
  ...
  --dry-run=client -o yaml | kubectl apply -f -
```

The `Deployment` mounts it as `envFrom: - secretRef: name: helvex-env`.

GitHub Actions secrets to keep up to date (stored in the repo's Settings → Secrets):

| Secret name | Used for |
|---|---|
| `DATABASE_URL` | Backend DB connection |
| `SECRET_KEY` | JWT / session token signing |
| `SMTP_HOST`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM` | Email |
| `SERPER_API_KEY` | Google Search |
| `ANTHROPIC_API_KEY` | Claude |
| `ZEFIX_API_USERNAME`, `ZEFIX_API_PASSWORD` | Zefix (optional) |
| `S3_ACCESS_KEY`, `S3_SECRET_KEY` | Hetzner Object Storage (backups + CSV exports) |
| `S3_BUCKET_EXPORTS` | `helvex-exports` bucket name |
| `HETZNER_TOKEN` | Terraform / Hetzner Cloud API |
| `GHCR_TOKEN` | GitHub Container Registry push |
| `KUBECONFIG` or `KUBE_CONFIG` | kubectl access for deploy steps |

---

## 11. Docker Build

### Image types

There are four distinct images, each with its own Dockerfile:

| Image | Dockerfile | GHCR tag | Contents |
|---|---|---|---|
| **Backend** | `Dockerfile` | `<repo>:<sha>` | Python 3.12 slim + app deps. spaCy and geocoding DB excluded. ~10 min build. |
| **ML base** | `Dockerfile.ml-base` | `<repo>-ml-base:latest` | Python deps + spaCy `de_core_news_md` + 143 MB geocoding SQLite (swisstopo + GeoNames). Rebuilt only when `requirements.txt` or `geocoding_client.py` change. |
| **ML worker** | `Dockerfile.ml` | `<repo>-ml:<sha>` | `FROM ml-base` + app code only. ~5 min build on cache hit. |
| **Frontend** | `frontend/Dockerfile` | `<repo>-frontend:<sha>` | Node 22 Alpine, Next.js standalone output. ~10 min build. |

### Why ml-base exists

The geocoding DB (`data/geocoding.db`) is downloaded and indexed at build time (~143 MB swisstopo zip → SQLite). QEMU arm64 emulation of this step alone took ~60 min on GitHub-hosted 2-core runners. By isolating it in `Dockerfile.ml-base` and letting GHA layer-cache it, code-only pushes skip the heavy step entirely.

`ml-base` is re-built when any of these change:
- `requirements.txt` (pip layer)
- `app/api/geocoding_client.py` (geocoding build logic)
- `Dockerfile.ml-base`

### Backend (`Dockerfile`)

1. Install system packages (`gcc`, `libpq-dev`)
2. `pip install -r requirements.txt`
3. Copy application source
4. `EXPOSE 8000` / `ENTRYPOINT ["sh", "entrypoint.sh"]`

Build args `INSTALL_SPACY_MODEL=false` and `BUILD_GEOCODING_DB=false` are passed by CI — the conditional blocks exist for local manual builds but are never triggered in the normal pipeline.

### ML base (`Dockerfile.ml-base`)

1. Install system packages
2. `pip install -r requirements.txt`
3. `spacy download de_core_news_md`
4. Copy only `app/__init__.py`, `app/api/__init__.py`, `app/api/geocoding_client.py`
5. Build geocoding DB (`_load_plz_table()` + `build_geocoding_db()`)
6. `chown -R 1000:1000 /app/data`

Only `geocoding_client.py` is copied — it has no internal imports beyond `httpx`.

### ML worker (`Dockerfile.ml`)

```dockerfile
ARG ML_BASE_IMAGE=ghcr.io/.../ml-base:latest
FROM ${ML_BASE_IMAGE}
COPY . .          # app code; data/ is git-ignored so ml-base's data/ layer is preserved
```

### Frontend (`frontend/Dockerfile`)

Multi-stage: `builder` (full `npm ci` + `next build`) → `runner` (standalone output only). The `deps` stage was removed — unused.

**`entrypoint.sh`** (backend + ml-worker only):
```bash
alembic upgrade head
exec "$@"   # uvicorn or rq worker
```

Build context optimization: `.dockerignore` excludes `.venv`, `frontend/node_modules`, `.git`, `__pycache__`, `data/` (geocoding files are git-ignored anyway).

---

## 12. CI/CD Pipelines

**Location:** `.github/workflows/`

### `ci.yml` — runs on every push + PR to main (path-aware)

`ci.yml` starts with a changed-path detector (`dorny/paths-filter`) and then executes only relevant lanes:

1. `test-backend` (Python 3.12: `ruff`, `pytest`, `pip-audit`) when backend paths changed
2. `test-frontend` (Node 22: `tsc`, `eslint`, `npm run build`) when frontend paths changed
3. `test-ml-imports` (ML smoke imports) when ML-specific paths changed

### `deploy-dev.yml` — trigger: `[deploy-dev]` in commit message

1. Detect changed areas (backend / frontend / ml) via `dorny/paths-filter`
2. Run three build jobs in **parallel**, building only changed tracks:
   - `build-backend` → `ghcr.io/<repo>:dev`
   - `build-ml` → `ghcr.io/<repo>-ml:dev` (uses `ml-base:latest` from GHCR — no heavy rebuild in dev)
   - `build-frontend` → `ghcr.io/<repo>-frontend:dev`
3. Deploy via Helmfile with stable dev tags (`image.tag=dev`, `mlImage.tag=dev`, `frontend.image.tag=dev`)

### `deploy-prod.yml` — parallel builds + selective deploy

#### Parallel build job graph

```
push [deploy-*]
       │
       ├── build-backend ──────────────────────────────┐
       │                                               │
       ├── build-ml-base ──► build-ml ─────────────────┤
       │                                               │
       └── build-frontend ─────────────────────────────┘
                                                       │
                                                  deploy (helvex-prod runner)
```

All three tracks run in parallel. `build-ml` depends on `build-ml-base`. Wall-clock time on a code-only push: ~12 min (down from ~90 min).

#### Commit-tag triggers

| Tag | Builds | Deploys |
|---|---|---|
| `[deploy-prod]` | backend + ml-base + ml + frontend | full helmfile apply (infra + app) |
| `[deploy-app]` | backend + ml-base + ml + frontend | helmfile apply `--selector name=helvex` |
| `[deploy-frontend]` | frontend only | `kubectl set image` on frontend deployment |
| `[deploy-backend]` | backend only | `kubectl set image` on backend deployment |
| `[deploy-ml]` | ml-base + ml only | `kubectl set image` on ml-worker deployment |

Workflow-dispatch `deploy_mode` input mirrors the same logic.

#### Deploy steps (on `helvex-prod` self-hosted runner)

1. kubectl / helm / helmfile checked with `command -v` — only installed if missing (cached between runs)
2. Ensure K8s secrets exist (`helvex-env`, `ghcr-pull-secret`, `arc-github-app`)
3. Bootstrap CRDs (`[deploy-prod]` only): cert-manager + CloudNativePG
4. Resolve PostgreSQL backup server names from S3 pointer file
5. Helmfile apply (full/app modes) or `kubectl set image` (component-only modes)
6. Rollout wait scoped to the deployed component(s)
7. Bump minor semver tag (`[deploy-prod]` / `[deploy-app]`)

Backend image is signed with Cosign after push.

### `cleanup.yml` — weekly cron (Sun 02:00 UTC)

- Delete untagged GHCR images
- Retain last 5 tagged versions

---

## 13. Kubernetes / Helm

### Cluster topology

- **K3s** (lightweight K8s) on Hetzner Cloud
- 2 nodes: control plane (cx23) + worker/database (cx33)
- Namespaces: `helvex-dev`, `helvex-prod`, `cert-manager`, `cnpg-system`, `arc-systems`, `monitoring`

### Helmfile — `infra/helmfile.yaml`

Install order (dependencies respected):

```
cert-manager → cloudnative-pg → arc-controller → arc-rbac → arc-runner-set → monitoring → helvex
```

### Helvex Helm chart — `infra/charts/helvex/`

**Key templates:**

| Template | K8s Kind | Notes |
|---|---|---|
| `deployment.yaml` | Deployment | FastAPI app pod |
| `frontend-deployment.yaml` | Deployment | Next.js pod |
| `worker-deployment.yaml` | Deployment | RQ worker (only if `worker.enabled`) |
| `postgres-cluster.yaml` | `postgresql.cnpg.io/v1 Cluster` | CloudNativePG-managed PostgreSQL |
| `postgres-backup-schedule.yaml` | ScheduledBackup | S3 backups, retention via Helm values |
| `redis.yaml` | Deployment + Service | Redis for job queue + rate limiting |
| `service.yaml` | Service | ClusterIP for backend |
| `ingress.yaml` | Ingress | Traefik routing; TLS via cert-manager |
| `clusterissuer.yaml` | ClusterIssuer | Let's Encrypt |
| `networkpolicy.yaml` | NetworkPolicy | Isolates helvex namespace |
| `servicemonitor.yaml` | ServiceMonitor | Prometheus scrapes `/metrics` |

**Pod security (all pods):**
```yaml
securityContext:
  runAsNonRoot: true
  runAsUser: 1000
  allowPrivilegeEscalation: false
  capabilities: { drop: [ALL] }
automountServiceAccountToken: false
```

**Health probes (backend pod):**
- Startup: `GET /health` every 10 s, 30 failures = ~5 min grace period
- Liveness: `GET /health` every 30 s, 3 failures → restart
- Readiness: `GET /health` every 10 s, 3 failures → removed from load balancer

**Environment-specific values:**

| Setting | Dev | Prod |
|---|---|---|
| Image registry | localhost:5000 | ghcr.io/petz15 |
| TLS | Disabled | Let's Encrypt (helvex.dicy.ch) |
| Postgres instances | 1 | 2 (HA) |
| Postgres storage | 10 Gi | 20 Gi |
| DB backups | No | Yes (S3, 7 d retention) |
| RQ worker pod | No | Yes |
| Monitoring | No | Yes |
| imagePullSecrets | No | ghcr-pull-secret |

---

## 14. Terraform / Hetzner

**Location:** `infra/terraform/envs/prod/` + `infra/terraform/modules/`

**Provider:** `hetznercloud/hcloud` v1.60.1
**Region:** `nbg1` (Nuremberg)
**OS:** Ubuntu 24.04

### Modules

| Module | Resources created |
|---|---|
| `network` | Hetzner VPC `10.0.0.0/16`, subnet `10.0.1.0/24` (eu-central zone) — avoids K3s internal ranges `10.42/43.x.x` |
| `firewall` | Inbound: SSH (22), HTTP (80), HTTPS (443) from configured admin CIDRs; outbound: all |
| `servers` | Control plane `cx23` (static IPv4, K3s init via cloud-init); worker/DB node `cx33` (taint: `helvex.io/role=database:NoSchedule`) |
| `loadbalancer` | Hetzner LB (`lb11`); targets all non-DB nodes; health check on `GET /health` |

### Applying changes

```bash
cd infra/terraform/envs/prod
terraform init
terraform plan
terraform apply
```

`terraform.tfvars` holds the Hetzner API token and admin SSH CIDRs — **never commit this file**.

---

## 15. Local Development

**File:** `docker-compose.yml`

Services:
- `app` — FastAPI backend (port 8000)
- `postgres` — PostgreSQL 16
- `redis` — Redis 7
- `nginx` — Reverse proxy (optional)

```bash
cp .env.example .env
# Edit .env — set POSTGRES_PASSWORD, SERPER_API_KEY, etc.
docker compose up --build
# App starts at http://localhost:8000
# API docs at http://localhost:8000/docs
```

To create an admin user:
```bash
docker compose exec app python -m app.create_admin
```

To run tests:
```bash
pytest tests/
```

To create a DB migration after model changes:
```bash
alembic revision --autogenerate -m "add foo column"
alembic upgrade head
```

---

## 16. Activity Log

### Purpose

The activity log is a lightweight audit trail of **user-initiated actions** across the platform. It is distinct from `audit_log` (field-level data diffs on company records).

### Storage

Table: `activity_log` (migration `0053_add_activity_log`)

| Column | Type | Notes |
|---|---|---|
| `id` | Integer PK | |
| `user_id` | FK → users (SET NULL) | Null if user was deleted |
| `org_id` | FK → organizations (SET NULL) | Org context at time of action |
| `action` | String(64), indexed | Action slug — see catalogue below |
| `resource_type` | String(32) | `company`, `view`, `note`, `org`, `user` |
| `resource_id` | Integer | PK of the affected row (no DB-level FK) |
| `meta` | JSONB | Extra context (search query, field list, count, etc.) |
| `ip` | String(45) | Client IP (IPv4 or IPv6) |
| `created_at` | DateTime(tz), indexed | UTC timestamp |

Indexes: `user_id`, `org_id`, `action`, `created_at`.

### Action Catalogue

| Action slug | Triggered from |
|---|---|
| `user_registered` | `POST /auth/register` |
| `user_login` | `POST /auth/login`, `POST /auth/token` |
| `email_verified` | `GET /auth/verify-email` |
| `password_changed` | `POST /auth/change-password` |
| `email_changed` | `POST /auth/confirm-email-change` |
| `company_viewed` | `GET /companies/{id}` |
| `company_updated` | `PATCH /companies/{id}` |
| `company_deleted` | `DELETE /companies/{id}` |
| `company_exported` | `GET /companies/export.csv` |
| `company_bulk_updated` | `POST /companies/bulk-update` |
| `company_bulk_tagged` | `POST /companies/bulk-tag` |
| `company_website_set` | `PATCH /companies/{id}/website` |
| `view_created` | `POST /views` |
| `view_deleted` | `DELETE /views/{id}` |
| `view_alert_toggled` | `PATCH /views/{id}/alert` |
| `note_created` | `POST /companies/{id}/notes` |
| `note_deleted` | `DELETE /companies/{id}/notes/{note_id}` |

### Service

`app/services/activity.py` — `log_activity(db, *, action, user_id, org_id, ...)`.

The helper is **best-effort**: exceptions are caught and logged but never re-raised. A failed write never breaks the user-facing request. It calls `db.flush()` (not `db.commit()`), so the entry participates in the caller's transaction.

### Admin Dashboard

`GET /api/v1/admin/activity-logs` — paginated log with filters: `user_id`, `org_id`, `action`, `resource_type`.

`GET /api/v1/admin/activity-logs/summary` — per-action counts + distinct active users for the last N days.

Frontend: **Superadmin → Activity** tab (`/app/admin/activity`).

### Retention

No automatic pruning yet. At high volume, add a nightly job to delete rows older than 90 days:

```sql
DELETE FROM activity_log WHERE created_at < now() - interval '90 days';
```

---

## 17. Common Bug-Fixing Cheatsheet

### Email verification not working
- Verify `SMTP_HOST`, `SMTP_FROM`, `SMTP_USER`, `SMTP_PASSWORD` are set in prod
- Check `APP_BASE_URL` — email links point to `{APP_BASE_URL}/verify-email?token=...`
- The `/verify-email` route is in `app/main.py` and is public (no login required)
- Token signed with `SECRET_KEY` — if the key rotates, all outstanding tokens become invalid
- The `/api/v1/auth/verify-email` endpoint returns JSON; the browser-facing `/verify-email` returns HTML

### Job stuck in `running` state
- Worker pod crashed mid-job before heartbeat went stale (within 120 s window)
- Wait 2 minutes, then restart the web pod — `requeue_interrupted_jobs()` will now re-queue it
- Or manually: `UPDATE job_runs SET status='queued', started_at=NULL WHERE id=<id>;`
- Check `job_run_events` table for the last log entry before the crash
- Under normal operation (SIGTERM → graceful shutdown), jobs transition to `paused`, not `running`

### Google Search quota exhausted
- Quota tracked in `app_settings` table, key `google_searches_today` (resets daily)
- Current quota: check `GET /api/v1/settings`
- Adjust via `PATCH /api/v1/settings` or increase `GOOGLE_DAILY_QUOTA` env var

### Migration fails on startup
- `entrypoint.sh` runs `alembic upgrade head` before uvicorn starts
- Pod will crash-loop if DB is unreachable — check CloudNativePG cluster status
- Verify `DATABASE_URL` secret is correct: `kubectl get secret helvex-env -o yaml`

### Pod OOMKilled
- Geocoding SQLite databases are loaded on first request, not at import time
- Claude + scikit-learn jobs are memory-intensive — run these in the RQ worker pod, not the API pod

### Auth token rejected after redeploy
- Dev uses ephemeral random `SECRET_KEY` — rotates every restart, invalidating all sessions
- Prod must set `SECRET_KEY` explicitly to a stable value in the `helvex-env` K8s secret

### Frontend can't reach API
- Frontend uses `FASTAPI_URL` env var — must be the K8s service name, e.g., `http://helvex:8000`
- Check `kubectl get svc -n helvex-prod`

### Checking logs

```bash
# Backend
kubectl logs -n helvex-prod deploy/helvex -f

# Worker
kubectl logs -n helvex-prod deploy/helvex-worker -f

# Frontend
kubectl logs -n helvex-prod deploy/helvex-frontend -f

# Database
kubectl logs -n helvex-prod helvex-postgres-1 -f
```

### Connecting to the database directly

```bash
kubectl exec -n helvex-prod -it helvex-postgres-1 -- psql -U zefix -d zefix_analyzer
```

Or via the CloudNativePG pooler if enabled.

### Job stuck in `paused` state after restart

Paused jobs are now **auto-resumed on startup** via `crud.resume_all_paused_jobs()`.
If a job remains paused, it was either cancelled mid-run or there is a preflight
failure (missing API key, insufficient credits) — check the job event log.

---

## 17. Background Job System — Design Evolution

This section records the architectural changes made to the job system and the
rationale behind each decision.

### Overview of changes (migration 0048)

Two columns were added to `job_runs`:

| Column | Type | Purpose |
|---|---|---|
| `dedup_key` | `VARCHAR(64)` nullable | Prevents duplicate active jobs per org/type |
| `last_heartbeat_at` | `TIMESTAMPTZ` nullable | Lets startup skip live worker-pod jobs |

---

### Real-time job status via SSE (replaces 3 s SWR polling)

**Before:** The Jobs page called `GET /api/v1/jobs` every 3 seconds per browser tab using SWR `refreshInterval`. With N users each having the jobs page open, the server received N×20 requests/minute of pure overhead — most returning unchanged data.

**After:** A single persistent `EventSource` connection per browser tab to `GET /api/v1/jobs/stream/active`. The server pushes updates; the client is silent.

**Two backend modes:**

| Mode | Trigger | Latency | Use case |
|---|---|---|---|
| **Redis pub/sub** | Workers publish to `jobs:{org_id}` channel on status transitions | <1 s for transitions | RQ mode (production) |
| **DB poll** | SSE endpoint polls DB every 1 s | ~1 s | Thread mode (dev / no Redis) |

In Redis mode a **2 s periodic DB poll** runs alongside pub/sub to deliver progress-bar updates. Workers do not publish on every progress tick (which would flood Redis with dozens of messages per second for fast jobs); the periodic poll closes this gap with a 2 s lag that is invisible to users.

**Trade-offs:**
- **Advantage:** Near-zero polling overhead; status transitions (complete/fail/pause) reach the browser in <1 s in production.
- **Disadvantage:** Each open SSE connection holds one synchronous uvicorn worker thread (blocking I/O). At current scale (<50 concurrent users) this is fine; at higher scale the endpoint should be rewritten as `async def` with `anyio.sleep` and an async Redis client.

---

### Job deduplication (idempotent enqueue)

**Before:** Clicking "Run" twice created two identical jobs, charged credits twice, and ran redundant work.

**After:** `_compute_dedup_key()` in `job_worker.py` produces a key per (job_type, org_id, relevant params). Before inserting a new job, `find_active_by_dedup_key()` checks for an existing active job with the same key. If found, the existing job is returned and no credits are charged.

**Dedup semantics by job type:**

| Behaviour | Job types |
|---|---|
| One active per org | `bulk`, `detail`, `initial`, `recalculate_scores`, `recalculate_google_scores`, `reextract_purpose`, `reclassify_noga`, `re_geocode`, `hdbscan_cluster`, `recompute_keywords`, `cluster_analysis` |
| One active per org + param hash | `claude_classify` (keyed on category/canton/prompt params) |
| No dedup | `batch`, `csv_export` (cancel-before-enqueue used for csv_export instead) |

**Trade-offs:**
- **Advantage:** Safe to click triggers multiple times; no wasted credits or duplicate DB writes.
- **Disadvantage:** A paused job with a dedup key blocks re-enqueue until it is resumed or cancelled. Users who want a fresh run must cancel first.

---

### Worker heartbeat + safe job recovery

**Problem:** `requeue_interrupted_jobs()` on web-pod startup re-queued ALL `running` jobs. In RQ mode (separate worker pods), this re-queued jobs that were still alive, causing double-execution.

**After:**
1. A per-job **heartbeat daemon thread** updates `job_runs.last_heartbeat_at` every 30 s while the job executes.
2. `requeue_interrupted_jobs()` now skips jobs whose `last_heartbeat_at` is younger than 120 s — these are alive on a worker pod and must not be touched.
3. Only jobs with a stale or absent heartbeat (truly crashed) are re-queued.

**Trade-offs:**
- **Advantage:** Eliminates double-execution after web-pod restarts; safe for rolling updates.
- **Disadvantage:** Adds one extra DB thread per running job. The thread is a daemon, writes are single-row UPDATEs every 30 s — negligible load. A crashed worker leaves a stale heartbeat; the 2-minute stale window means the job will not be recovered until the next startup after that window expires.

---

### Graceful shutdown (pause instead of SIGKILL)

**Before:** SIGKILL from K8s left jobs stuck as `running`. On restart they were re-queued from scratch.

**After:**
1. SIGTERM → `_SafeWorker.handle_warm_shutdown_request()` sets `_shutdown_requested = True`.
2. At the next `_assert_not_cancelled()` call inside the job, a `JobPausedError` is raised.
3. The job is saved as `paused` with `progress_done` checkpointed — no work is lost.
4. On next startup, `resume_all_paused_jobs()` re-queues these jobs automatically.
5. `terminationGracePeriodSeconds: 90` on all worker pods gives the job enough time to reach the next checkpoint before K8s sends SIGKILL.

**Trade-offs:**
- **Advantage:** Zero lost progress across rolling updates; users never see a job reset to 0.
- **Disadvantage:** `terminationGracePeriodSeconds: 90` delays K8s rolling updates by up to 90 s per pod. Jobs with very coarse checkpoints (e.g., one checkpoint per canton in `bulk`) may not save progress if the canton takes >90 s to process.
- **Note:** Auto-resume on startup re-queues all paused jobs, including those paused by user action before a restart. Users who want a job to stay paused across a restart should cancel it instead.

---

### Redis connection pool for pub/sub publish

**Before:** `_publish_job_update()` created a new `Redis(...)` TCP connection on every call — up to dozens of times per second for a fast-progressing job.

**After:** A module-level `ConnectionPool` (max 5 connections) is shared across all `_publish_job_update()` calls in the worker process.

**Trade-offs:**
- **Advantage:** Eliminates per-call TCP handshake overhead; pool connections are reused.
- **Disadvantage:** The pool is process-scoped. In RQ mode each work-horse subprocess is a fork of the worker process. The forked pool state is safe for Redis (connections are closed+reopened on fork by the redis-py library), so there are no cross-process connection leaks.

---

### Job history retention

**Before:** `job_runs` grew indefinitely — every completed, failed, or cancelled job remained in the table forever.

**After:** `delete_old_finished_jobs(keep_days=30)` runs on every startup and deletes terminal jobs older than 30 days. Active jobs are never deleted.

**Trade-offs:**
- **Advantage:** Prevents unbounded table growth; keeps query performance stable.
- **Disadvantage:** Job history older than 30 days is permanently lost. If longer retention is needed, adjust `keep_days` or move old rows to a separate archive table instead of deleting.
