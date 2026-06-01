# Helvex — Architecture Reference

> Internal documentation for bug fixing and onboarding.
> **Stack:** FastAPI · PostgreSQL · K3s/Hetzner · Helm · Terraform · Next.js (Redis optional for RQ mode)
> **Repo:** `helvex` (product name: Firmiq)
> **Note:** This document was last updated May 2026 after Phase 1–3 refactoring. Phase 3.2 (Redis removal) remains planned but incomplete; Redis is still used in RQ mode.

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
10. [Configuration & Environment](#10-configuration--environment)
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
1. **Bulk import** — Zefix canton-by-canton, resumable; now imports both ACTIVE and CANCELLED/BEING_CANCELLED companies by default (`active_only=False`)
2. **Detail fetch + geocode** — swisstopo building-level precision
3. **Website enrichment** — Serper.dev Google Search, daily quota-aware
4. **AI scoring** — Claude Haiku via Anthropic API
5. **Dashboard / export** — filter, sort, paginate, CSV export (streaming sync or async unlimited)

---

## 2. Directory Layout

```
zefix_analyzer/
├── app/                        # Python backend (FastAPI)
│   ├── main.py                 # App factory, middleware, lifespan, startup jobs
│   ├── config.py               # Pydantic settings (reads .env)
│   ├── auth.py                 # JWT, session cookies, rate limiting, token helpers
│   ├── database.py             # SQLAlchemy engine + session factory
│   ├── create_admin.py         # CLI: create superadmin user
│   ├── run_collector.py        # CLI: run collection jobs outside HTTP
│   ├── worker_entrypoint.py    # Entrypoint for RQ worker pod (if USE_RQ=true)
│   ├── clients/                # External API wrappers (Zefix, Serper, Geocoding, SHAB)
│   │   ├── zefix_client.py     # Zefix REST API client
│   │   ├── google_search_client.py  # Serper.dev wrapper
│   │   ├── geocoding_client.py # Offline geocoder (swisstopo + GeoNames fallback)
│   │   ├── shab_client.py      # SHAB HR publications feed (Zefix SOGC bydate)
│   │   └── shab_archive_client.py  # shab.ch archive API + PDF extraction (pypdf)
│   ├── api/
│   │   ├── routes/
│   │   │   ├── auth.py         # /api/v1/auth/*
│   │   │   ├── admin.py        # /api/v1/admin/* (superadmin only)
│   │   │   ├── companies/      # /api/v1/companies/* (split into: list, detail, bulk, analytics, search, zefix)
│   │   │   ├── billing/        # /api/v1/billing/* (subscription, checkout, webhooks, info)
│   │   │   ├── workspace/      # /api/v1/orgs/{org_id}/* (org settings, members, company state)
│   │   │   ├── jobs.py         # /api/v1/jobs/*
│   │   │   ├── map.py          # /api/v1/map/*
│   │   │   ├── notes.py        # /api/v1/notes/*
│   │   │   ├── ops_settings.py # /api/v1/settings/* (org-scoped settings)
│   │   │   ├── orgs.py         # /api/v1/orgs/* (organization CRUD)
│   │   │   ├── clusters.py     # /api/v1/clusters/* (TF-IDF cluster browse)
│   │   │   ├── views.py        # /api/v1/views/* (saved search views)
│   │   │   ├── invites.py      # /api/v1/invites/* (organization invitations)
│   │   │   └── deps.py         # Shared FastAPI dependencies
│   │   └── [deprecated shims]  # Backward-compat re-exports from app/clients/
│   ├── models/                 # SQLAlchemy ORM (70+ migrations, Phase 4 cleanups deferred)
│   │   ├── user.py, organization.py, company.py, job_run.py, note.py, etc.
│   ├── schemas/                # Pydantic request/response DTOs
│   ├── crud/                   # DB access functions (no business logic)
│   └── services/               # Business logic (split into 20+ focused modules)
│       ├── collection.py       # Facade re-exporting from split modules
│       ├── zefix_import.py     # Zefix API fetch, bulk import, detail collect
│       ├── web_enrichment.py   # Google search enrichment, batch collect
│       ├── geocoding_pipeline.py    # Geocoding, flex-score recalc
│       ├── noga_pipeline.py    # NOGA industry classification, embeddings
│       ├── claude_classify.py  # Claude Haiku batch classification + resume
│       ├── language_detection.py    # Purpose language detection (multilingual)
│       ├── cluster_pipeline.py # TF-IDF K-Means, HDBSCAN, semantic clustering
│       ├── scoring.py          # Score computation (flex, web, AI, combined)
│       ├── shab_import.py      # SHAB daily + backfill import (Zefix SOGC bydate)
│       ├── shab_archive_import.py  # SHAB archive import from shab.ch (PDF-based)
│       ├── job_worker.py       # Job dispatch (now: handler registry pattern)
│       ├── job_handlers/       # Handler modules for each job type (24 types)
│       ├── rate_limit.py       # Centralized rate limiting (no Redis)
│       ├── noga_lookup.py      # NOGA hierarchy file loader (zero deps)
│       ├── credits.py          # Credit deduction, low-balance alerts
│       ├── activity.py         # Activity logging (user actions)
│       ├── tiers.py            # Org tier feature gates + pricing
│       ├── email.py            # SMTP transactional email
│       ├── s3_client.py        # boto3 S3 wrapper
│       ├── payment_transactions.py  # Credit grant, subscription apply
│       └── [other services]    # email, boilerplate_analysis, incremental_classify, etc.
│
├── alembic/                    # Database migrations (Alembic)
│   ├── env.py
│   ├── versions/               # 70+ numbered migration files (Phase 4 schema cleanups deferred)
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
| GET  | `/api/v1/companies/{id}/noga-explain` | Full NOGA classification trace (superadmin only) |
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
| POST | `/api/v1/scoring/repair-is-current` | Enqueue `repair_is_current` job — recomputes `is_current` on all existing `sogc_person_appearances` in-place |

#### Views — `app/api/routes/views.py`

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/api/v1/views` | Member | List saved views for current org |
| POST | `/api/v1/views` | Member | Save current filter set as a named view |
| DELETE | `/api/v1/views/{id}` | Member (owner) | Delete a saved view |
| PATCH | `/api/v1/views/{id}/alert` | Member (owner) | Enable/disable daily new-match alert for a saved view |

#### Organizations (CRUD) — `app/api/routes/orgs.py`

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/api/v1/orgs/me` | User | List all orgs the current user is a member of |
| GET | `/api/v1/orgs/{org_id}` | Member/Admin | Get organization details (name, slug, tier, user's role) |
| POST | `/api/v1/orgs` | User | Create new organization |
| POST | `/api/v1/orgs/switch/{org_id}` | Member | Set active org for session |
| DELETE | `/api/v1/orgs/{org_id}` | Owner | Delete organization (cascades members) |
| POST | `/api/v1/orgs/{org_id}/leave` | Member | Leave organization (prevents leaving if sole owner) |
| POST | `/api/v1/orgs/{org_id}/request-verification` | Admin/Owner | Request verified-business status (auto-verify if linked company web_score ≥ 70) |

#### Workspace / Orgs — `app/api/routes/workspace.py`

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/api/v1/orgs/{id}/notifications` | Member | Get notification preferences (`email_notifications`) |
| PATCH | `/api/v1/orgs/{id}/notifications` | Admin/Owner | Update notification preferences |
| GET | `/api/v1/orgs/{id}/members` | Member | List organization members |
| POST | `/api/v1/orgs/{id}/members` | Admin/Owner | Add member to organization |
| PATCH | `/api/v1/orgs/{id}/members/{user_id}` | Admin/Owner | Update member role |
| DELETE | `/api/v1/orgs/{id}/members/{user_id}` | Admin/Owner | Remove member from organization |
| GET | `/api/v1/orgs/{id}/settings` | Member | Get org-scoped settings |
| PUT | `/api/v1/orgs/{id}/settings` | Admin/Owner | Update org-scoped settings |
| … | (other org-scoped routes) | | |

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

#### Map Geocoding — `app/api/routes/map.py`

| Method | Path | Description |
|---|---|---|
| POST | `/api/v1/map/geocode-address` | Geocode a user-supplied address string → lat/lon |
| GET | `/api/v1/map/bounds` | Leaflet map data within viewport bounds (clustered or detailed) |

**Geocoding flow** (`app/api/geocoding_client.py`):

The `geocode_address(address)` function attempts three fallback strategies in order:

1. **Building-level lookup** (swisstopo Amtliches Gebäudeadressverzeichnis)
   - Requires comma-separated address with PLZ segment (e.g. `"Bahnhofstrasse 1, 3011 Bern"`)
   - Returns sub-meter precision (~5m)
   - Normalized string matching (whitespace + case-insensitive)

2. **PLZ centroid fallback** (`_plz_fallback`)
   - Extracts any 4-digit Swiss postal code from the address
   - Returns GeoNames centroid for that PLZ (~2 km precision)
   - Supports minimal input: `"3000"` or `"3000 Bern"`

3. **City name fallback** (`_city_fallback`) — **NEW**
   - Builds city-name → centroid index from GeoNames col 2 (place names) at startup
   - Tries each comma-separated segment as a city name (right-to-left, most specific first)
   - Falls back to whole address if no comma-separated parts match
   - Supports: `"Zürich"`, `"Bern"`, `"3000 Bern"` (city name extracted after PLZ)
   - Normalization: whitespace stripped, diacritics removed, lowercased

**Data sources:**
- `swisstopo_addresses.db` — SQLite index built at Docker build time from zipped shapefile
- `geonames.txt` — tab-delimited GeoNames CH subset (PLZ + place name + lat/lon)
- Both downloaded and compiled into memory at first request (lazy loading)

**City index structure:**
```python
_city_table: dict[str, tuple[float, float]] = {
    "zurich": (47.3769, 8.5472),
    "bern": (46.9479, 7.4474),
    "basel": (47.5596, 7.5886),
    ...
}
```

**Normalization function** (`_norm`):
- Remove accents (é → e, ü → u)
- Convert to lowercase
- Strip whitespace
- Skip if empty

#### Other routes

| Route module | Path prefix | Summary |
|---|---|---|
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
| `lat`, `lon` | Float | Geocoordinates (swisstopo / GeoNames) |
| `flex_score` | Integer 0-100 | Zefix-data-only priority score (no external API) |
| `flex_score_breakdown` | JSON | Per-component flex score detail |
| `website_url` | String | Top Google result URL (global master; see dual-write note) |
| `web_score` | Integer 0-100 | Google name/location match quality |
| `google_search_results_raw` | Text | Raw top-5 Serper results as JSON |
| `website_checked_at` | DateTime | Last Google enrichment timestamp |
| `social_media_only` | Bool | True if only social media URLs were found |
| `ai_score` | Integer 0-100 | Claude Haiku classification score |
| `ai_category` | String | Claude-assigned category label |
| `combined_score` | Float | Stored weighted score (ai×0.60 + noga_conf×0.25 + keyword_density×0.15) |
| `noga_code` | String | Official Swiss NOGA 2025 industry code |
| `noga_confidence` | Float 0-1 | Classifier confidence |
| `purpose_language` | String | Detected language of purpose text |
| `tfidf_cluster` | Text | Top TF-IDF cluster terms (comma-separated) |
| `purpose_keywords` | Text | Per-company top keywords from purpose text |
| `business_model` | String | b2b / b2c / b2g / mixed |
| `zefix_raw` | Text/JSON | Raw Zefix API response |
| `sogc_pub` | Text/JSON | Raw `sogcPub` array from Zefix detail (source for `sogc_publications`) |

**Dual-write note (Google scoring fields):** `website_url`, `web_score`, `google_search_results_raw`, `website_checked_at`, and `social_media_only` exist on both `Company` (global Serper master) and `OrgCompanyState` (org-specific re-score). Always read from `OrgCompanyState` when `org_id` is available; fall back to `Company` only when no org context exists. See model file comments for details.

**Note:** `review_status`, `contact_status`, `contact_name/email/phone`, and `tags` also exist as legacy columns on `Company`, but the authoritative per-org values live in `OrgCompanyState`. Do not write these fields directly on `Company` for new code.

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
| `org_id` | FK → organizations (nullable; NULL = legacy view shown in all org contexts) |
| `user_id` | FK → users (owner) |
| `name` | Human-readable label |
| `filters_json` | Serialized filter params |
| `alert_enabled` | Bool (default False) — if True, daily sweep emails the owner when new matches appear |
| `alert_last_count` | Count of matching companies at last sweep; NULL until first sweep run |
| `alert_last_checked_at` | Timestamp of last sweep for this view |

Views are scoped per-user per-org. `list_views` returns rows matching `org_id = current_org` OR `org_id IS NULL` (legacy pre-0072 views). New views always set `org_id`.

Migrations: `0052_add_user_view_alert_fields`, `0072_add_org_id_to_user_views`

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
| `SogcPublication` | sogc_publications | One row per SOGC publication entry from `companies.sogc_pub` |
| `SogcChange` | sogc_changes | One row per detected change type within a publication |

#### `SogcPublication` — `app/models/sogc_publication.py`

Exploded from the `companies.sogc_pub` JSON blob (sourced from Zefix `sogcPub` field on the company detail endpoint).

| Column | Notes |
|---|---|
| `sogc_id` | Zefix SOGC ID — unique, used as upsert key |
| `company_uid` | FK → companies.uid (SET NULL on delete) |
| `pub_date` | Publication date "YYYY-MM-DD" |
| `sub_rubric` | HR01 / HR02 / HR03 (new / mutation / deletion) |
| `pub_number` | Publication number if present |
| `text_de / text_fr / text_it / text_en` | Cleaned publication text per language (language inferred from `registryOfCommerceCanton`) |
| `detected_language` | Primary language used for parsing |
| `encoding_fixed` | True if latin-1→utf-8 mojibake fix was applied |
| `raw_json` | Raw entry dict from sogcPub array |
| `preprocessed_at` | When `sogc_changes` were last written for this publication |

#### `SogcChange` — `app/models/sogc_change.py`

| Column | Notes |
|---|---|
| `sogc_publication_id` | FK → sogc_publications.id (CASCADE delete) |
| `change_type` | One of: `address`, `person_added`, `person_removed`, `capital`, `name`, `merger`, `acquisition`, `purpose`, `status` |
| `keywords_matched` | JSON list of keywords that matched |
| `raw_excerpt` | First ~200 chars around the matched keyword |

Migration: `0073_add_sogc_publications_and_changes`

### CRUD Layer (`app/crud/`)

Thin functions over SQLAlchemy — no business logic. Key modules:
- `crud/user.py` — `create_user`, `authenticate`, `mark_email_verified`, `update_password`, `record_verification_sent`
- `crud/company.py` — `get_company`, `list_companies` (with filters), `upsert_company`, `update_company`
- `crud/job_run.py` — `create_job`, `list_jobs`, `get_job`, `update_job_status`, `requeue_interrupted_jobs`

### Migrations (`alembic/versions/`)

73 migration files (covering Zefix import, multi-tenancy migration, job dispatcher, scoring evolution, Phase 4 schema cleanups, SOGC publication tables, etc.). On startup `alembic upgrade head` runs automatically. To create a new migration:

```bash
alembic revision --autogenerate -m "describe change"
alembic upgrade head
```

**Recent Phase 4 cleanups:**
- `0071` — Dropped 2 redundant combined_score functional indexes + 2 stale partial indexes (pre-column-rename names). `ix_companies_combined_score_stored` (stored column B-tree) is the correct index.
- `0072` — Added `org_id` FK to `user_views`; backfilled from `users.org_id`; views are now org-scoped.
- `fe7a997e322a` (prior session) — Dropped 8 never-read JSON blob columns from `companies` (`sogc_pub`, `further_head_offices`, `branch_offices`, `has_taken_over`, `was_taken_over_by`, `audit_companies`, `old_names`, `translations`).
- `0090` — Added `ix_companies_name_trgm` GIN trigram index on `companies.name`. Fixes full-table seq-scan for `ILIKE '%term%'` name searches (B-tree cannot serve leading-wildcard patterns). `pg_trgm` extension already enabled from `0022`.

**Request logging (`app/main.py` `api_request_logger`):**
- Logs safe query params (`q`, `canton`, filter params) in every API request line via `qs=` field.
- Emits `logger.warning` instead of `logger.info` for requests exceeding `_SLOW_REQUEST_MS` (500ms). Allows grep/alerting on `http.request` with `WARNING` level to surface slow searches.

**Deferred Phase 4 items:**
- `4.4` — Legacy per-user billing columns (`payment_customer_id`, `payment_subscription_id`, `subscription_status`) are still on `users`; confirmed actively used in billing/workspace routes — deferred indefinitely.
- `4.6` — Junction table dual-write (`tfidf_cluster`/`purpose_keywords` text columns vs junction tables via `_sync_junction_tables`) — deferred; requires full audit before removing sync logic.

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

### Rate limiting — `app/auth.py` + `app/services/rate_limit.py`

**Implementation (Thread mode, default):** In-memory sliding window using `defaultdict` of timestamps per IP/key. No external dependencies.

**Note:** Phase 3.2 (Redis removal) is planned but not yet complete. Redis is still available for RQ mode (separate worker process) if needed in production.

| Endpoint | Limit | Window | Keyed by |
|---|---|---|---|
| Login failures | 10 attempts | 15 min | IP address |
| `/register`, `/forgot-password`, etc. | Configurable | Per-action | IP address |
| **Authenticated routes** | Per-route | Per-route | User ID or Org ID |

**Authenticated endpoint rate limits:**

| Route | Limit | Window |
|---|---|---|---|
| `POST /jobs/enqueue/csv-export` | 5 calls | 10 min |
| `GET /companies/export.csv` | 5 calls | 10 min |
| `POST /scoring/claude` | 20 calls | 10 min |
| `GET /companies/{id}/google-search` | 30 calls | 10 min |
| `POST /scoring/claude-preview` | 3 calls | 24 h (calendar day per org) |

Superadmins bypass all authenticated rate limits. The `check_rate_limit()` helper
in `app/services/rate_limit.py` is the centralized function; routes call it directly.

**Note:** Rate limiting is per-pod; in multi-pod deployments, each pod maintains its own sliding window. This is acceptable for a 2-3 pod cluster; large deployments should transition to Redis or a shared state backend.

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

### Session Store

**Decision (Phase 3.3):** Use stateless `itsdangerous` signed cookies. No server-side session store.

**Rationale:**
- **Stateless:** Session data is cryptographically signed and embedded in the cookie. No DB/cache lookup on every request.
- **Scalable:** Multi-pod deployments (session on pod A, request on pod B) work without shared state.
- **Portable:** Cookie survives pod restarts; no lost sessions.
- **Simple:** Single `SECRET_KEY` signs all session tokens. No session ID registry to manage.

**Trade-off:** Cannot revoke a session before it expires (8 h TTL). If immediate logout is needed (e.g., user deactivation), the frontend clears the cookie and the backend rejects any token where `decode_session_cookie()` returns None. Immediate deactivation is rare; 8h is acceptable for most SaaS products.

**If revocation becomes required:** Introduce a small session invalidation table keyed by `(user_id, session_created_at)` with a 8h TTL. Check it on each request before accepting a cookie. Trade-off: adds a DB hit per request, but revocation becomes instant.

---

### Secrets Management

**Current state (Phase 3.4):** Secrets are stored in GitHub Actions encrypted variables and passed to K8s as environment variables during deployment.

**Planned upgrade:** Transition to sealed-secrets or external-secrets-operator for K8s secret rotation without re-deploying:

1. **External Secrets Operator** (recommended)
   - Syncs secrets from a source (HashiCorp Vault, AWS Secrets Manager, etc.) into K8s `Secret` objects
   - `helvex` Helm chart references secrets by name; external-secrets-operator populates them on a schedule
   - Secrets are never committed to Git
   - No manual Helm values edits needed for secret rotation

2. **Sealed Secrets** (simpler alternative)
   - Each K8s cluster has a sealing keypair; secrets encrypted with the public key can only be decrypted in that cluster
   - `SealedSecret` CRDs are committed to Git; the sealed-secrets controller decrypts them to actual `Secret` objects
   - Good for single-cluster deployments; less suitable for multi-cluster federation

**Scope (Phase 3.4):** Document the decision and plan migration steps. Implementation deferred to later phase (requires Vault setup or external-secrets-operator deployment in K8s).

---

## 6. Background Job System

### Implementation

**Thread mode (default, `USE_RQ=false`)**
- Single daemon thread (`app/services/job_worker.py`), polls `job_runs` table for `status=queued`
- Executes jobs sequentially in-process
- No external dependencies; uses in-memory progress tracking
- Set `DISABLE_JOB_WORKER=true` to suppress the thread (e.g., API-only pod)

**Job handler registry pattern** — Replaced the previous 735-line `elif` chain
- Each job type has a dedicated handler in `app/services/job_handlers/{type}.py`
- `_run_job()` dispatches via `JOB_HANDLERS[job_type](ctx)`, passing context (DB, params, progress callback, abort signal)
- Handlers return `(stats_dict, done_message)` or raise typed exceptions (`JobPausedError`, `JobCancelledError`, `JobWaitingExternalSignal`)

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
| `sogc_preprocess` | `mode`, `batch_size`, `uids` | Explode `sogc_pub` blobs into `sogc_publications` + `sogc_changes` rows; see §SOGC Preprocessing | — |
| `repair_is_current` | `batch_size` | Recompute `is_current` flag for all existing `sogc_person_appearances` rows; fixes historical data before the temporal-ordering bug was corrected | — |
| `shab_archive` | `start_page`, `end_page`, `page_size`, `request_delay`, `pdf_delay` | Fetch shab.ch archive, download PDFs, upsert `sogc_publications` + `sogc_changes`; resume via page cursor | ONE_PER_ORG |

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

**Improvements (Feb 2026):**
- **City name fallback:** Added `_city_fallback()` function to match city names (GeoNames col 2) when building lookup fails and no PLZ is present. Enables queries like `"Zürich"`, `"Bern"`, `"Lausanne"` without requiring a PLZ or full address. Normalization (accent removal, lowercase, whitespace trim) ensures matches work across language variants (e.g. "Zurich" → "zürich").
- **Resolution order:** building lookup → PLZ fallback → **city fallback** (new) → return None
- **GeoNames index:** Now built at app startup with both PLZ and city names cached for O(1) lookup

### Claude (Anthropic) — `app/services/collection.py` + `app/crud/app_setting.py`

- **API key**: Resolved per-org via `get_effective_setting(db, "anthropic_api_key", org_id=...)`
  - Falls back to global `ANTHROPIC_API_KEY` env var if no org override
  - Never exposed in APIs (replaced with `anthropic_api_key_set: bool` in frontend)
- **Model**: Claude Haiku (cheapest; ~$0.25 per 1000 companies)
- **Used for**: `claude_classify` batch job, and `claude-preview` dry-run endpoint
- **System prompt**: User-configurable via Settings API; resolved per-org
- **Preflight check** (`_preflight_job`): Validates org has API key before queueing `claude_classify`
- **Dry-run / preview**: `claude_classify_batch(dry_run=True)` scores up to 5 companies without writing to DB. Called by `POST /api/v1/scoring/claude-preview`. Rate-limited to 3 calls/min per org (in-memory sliding window).

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

### SOGC Publication Preprocessing — `app/services/sogc_preprocessor.py`

Explodes the flat `companies.sogc_pub` JSON blob (a list of SOGC publication entries from the Zefix company detail endpoint) into two normalized tables.

**Data source:** Zefix company detail (`/company/uid/{uid}`) returns a `sogcPub` list where each entry has:
```json
{
  "sogcId": 1006604586,
  "sogcDate": "2026-03-24",
  "registryOfCommerceCanton": "ZH",
  "message": "<FT TYPE=\"F\">Acme AG</FT>, in <FT TYPE=\"S\">Zürich</FT>, ...",
  "mutationTypes": [{"key": "MUTATION"}]
}
```
The `message` field contains the full HR publication narrative (single language; `<FT TYPE="...">` HTML tags are stripped, HTML entities unescaped). Language is inferred from `registryOfCommerceCanton` (GE/JU/NE/VD → fr, TI → it, all others → de).

**Encoding fix:** Some entries have UTF-8 bytes stored as latin-1 (mojibake). The fix tries `text.encode("latin-1").decode("utf-8")` and applies it only when it succeeds and changes the string.

**Change detection:** `_detect_changes(texts)` runs multilingual keyword matching against `CHANGE_PATTERNS` (9 types × 4 languages) and returns one `SogcChange` row per matched type. Matching is case-insensitive substring search across all non-null text columns.

**Key functions:**
- `preprocess_company_sogc_pub(db, company)` — idempotent upsert for one company; upserts `sogc_publications` by `sogc_id`, deletes + reinserts `sogc_changes`. Called inline after each SHAB import.
- `run_sogc_preprocess_batch(db, mode, uids, ...)` — batch over companies table with cursor pagination; `mode="missing"` skips already-processed companies, `mode="all"` reprocesses everything.
- `run_sogc_publications_backfill(db, ...)` — iterates existing `sogc_publications` rows, re-applies encoding fix to stored texts, regenerates `sogc_changes`. Used via `mode="publications"` job param to retroactively fix rows written before the encoding/text-extraction fixes.

**SHAB integration (Zefix-backed):** After every HR01/HR02 company upsert in `shab_import.py`, `preprocess_company_sogc_pub` is called fire-and-forget (errors logged, not raised), ensuring new publications are indexed immediately.

### SHAB Archive Import — `app/services/shab_archive_import.py`

Imports historical SHAB publications directly from the `shab.ch` public archive API (`https://www.shab.ch/api/v1/archive/public`). Unlike the Zefix-backed SHAB importer this source is PDF-based and does not touch the `companies` table.

**Workflow:**
1. `fetch_archive_page(page, size)` — paginates the archive list (`includeContent=false`).
2. For each HR01/HR02/HR03 entry: `fetch_pdf_bytes(id)` → `extract_text_from_pdf()` (pypdf).
3. Extract UID via regex (`CHE-xxx.xxx.xxx`), detect language (lingua + canton fallback).
4. Upsert `SogcPublication` with extracted text; detect changes via `_detect_changes` from `sogc_preprocessor.py`.
5. Supports pause/resume: `progress_done` stores the last completed page number.

**sogc_id convention:** `"shab_{archive_id}"` (e.g. `"shab_4447021"`) — never collides with Zefix SOGC IDs (plain numeric strings).

**Job type:** `shab_archive` — registered in `JOB_HANDLERS`, ONE_PER_ORG.

**API endpoint:** `POST /api/v1/collection/shab-archive` (superadmin only).

**Frontend:** Collection page → SHAB / SOGC group → "SHAB Archive Import (shab.ch)" section.

**Dependency:** `pypdf>=4.0.0` added to `requirements.backend.txt`.

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
Computed by `compute_relevance_score()` in `scoring.py`:
- `ai_score × 0.60 + noga_confidence×100 × 0.25 + keyword_density×100 × 0.15`
- Components that haven't run yet are excluded; remaining weights are renormalised
- `flex_score` and `web_score` remain stored as supplementary signals but do not affect the combined score

### Configuring scoring
The keyword taxonomy (target clusters, target keywords, flex score weights) is stored in `app_settings` and editable live via `PATCH /api/v1/settings` or the Settings UI panel. The `combined_score` formula is fixed (not org-configurable).

### Classification Pipelines (Clustering, Keywords, NOGA)

**Files:** `app/services/cluster_pipeline.py`, `app/services/noga.py`, `app/services/collection.py`

---

#### ML Pipeline Overview

Three complementary ML pipelines enrich company data:

| Pipeline | Input | Output | When to run |
|---|---|---|---|
| **Keyword extraction** | `purpose` text | `purpose_keywords` per company | After initial import; incremental on new companies |
| **TF-IDF K-Means clustering** | `purpose` or `purpose_keywords` | `tfidf_cluster` per company | Run after keywords; 50 clusters (default) |
| **NOGA classification** | name + purpose + keywords + cluster | `noga_code`, `noga_path` | After keywords + clustering are done |

**Correct execution order:** Keywords → Clustering → NOGA → (auto) Discover Stopwords. NOGA uses `purpose_keywords` and `tfidf_cluster` as input signals; running it before clustering degrades accuracy.

### NOGA Classification (Industry Taxonomy)

Classifies each company into the official Swiss NOGA 2025 taxonomy (~2000 level-5 categories) using a hybrid approach:

**Setup (one-time):**
1. `build_noga_embeddings` — Embeds all NOGA categories in DE/FR/IT/EN using `paraphrase-multilingual-mpnet-base-v2` (768-dim), stores vectors in PostgreSQL via pgvector.

**Per-company classification:**
1. `detect_language_bulk` — Detects company purpose language (DE/FR/IT/EN) via lingua library; stores in `purpose_language`.
2. `reclassify_noga` — Hybrid classifier: (60%) pgvector cosine similarity to language-matched NOGA embeddings + (40%) token overlap from company name/purpose/keywords. Returns `noga_code`, `noga_confidence` (0–1), and full ancestry path. Controlled by `embed_mode` parameter (see below).
3. `reclassify_low_conf_noga` — Confidence-based refinement: re-runs classifier on companies below threshold (default 0.80), useful after rebuilding embeddings.

**Why language-aware:** Embedding company purpose in French and matching it against French NOGA descriptions yields higher semantic similarity than cross-lingual matching. Confidence scores reflect this — typically 0.75–0.95 for clear industry classifications, 0.40–0.70 for ambiguous cases (candidates for API re-run).

---

### Company Purpose Embeddings (Semantic Search)

Stores 768-dim L2-normalized embeddings per company in `company_embeddings` for free-text semantic search.

**Two embedding types:**
- `purpose_full` — raw `company.purpose` text
- `purpose_clean` — boilerplate-stripped purpose (same patterns as NOGA pipeline)

**Batch jobs (standalone):**
- `embed_purpose_full` — (re)computes `purpose_full` embeddings for all companies with a purpose
- `embed_purpose_clean` — strips boilerplate, then embeds; `only_missing=true` is the default
- Both support pause/resume via `resume_from` (keyset pagination on `company.id`)

**NOGA pipeline integration:** `reclassify_noga` embeds purpose text per-company to do the pgvector similarity search (transient, not stored). At the end of each classified batch it also persists embeddings to `company_embeddings`, sharing model load and boilerplate patterns at no extra cost. Behaviour is controlled by the `embed_mode` job param:

| `embed_mode` | What is stored | Use case |
|---|---|---|
| `"clean"` (default) | `purpose_clean` only | Normal runs — keeps semantic search index up to date |
| `"full_and_clean"` | `purpose_clean` + `purpose_full` | Initial backfill or after bulk import |
| `"none"` | nothing | NOGA-only run, skip embedding overhead |

**Semantic search API:** `GET /api/v1/search/semantic?q=<text>&embedding_type=purpose_clean&limit=50` — embeds the query at request time and returns ranked companies by cosine similarity. Requires embeddings to be pre-computed.

**Storage:** ~2 GB on disk for 700k companies × 2 embedding types (768-dim float32 × 4 bytes, pgvector compression).

**Index:** Two partial IVFFlat indexes (one per `embedding_type`, `lists=1000`) for sub-second ANN search on 700k vectors.

**Key files:**
- `app/models/company_embedding.py` — SQLAlchemy model
- `app/services/company_embedding_pipeline.py` — batch jobs, upsert helpers, semantic search
- `alembic/versions/0083_add_company_embeddings.py` — migration

**Debug / explain endpoint (superadmin only):**
`GET /api/v1/companies/{id}/noga-explain` — re-runs classification for a single company and returns the full intermediate trace:
- Stripped purpose, detected language, extracted tokens, embed text
- Per-level (L1–L5): top-10 candidates with embedding similarity, normalized token score, excludes cosine penalty, final hybrid score, and the winner
- Flags for lookahead tie-breaking and fallback usage at each level

Implemented in `classify_company_noga_explain()` in `app/services/noga.py`. Exposed in the company detail UI as a "NOGA explain" button (violet, header area) visible only to superadmins; opens a modal with the full trace.

---

#### Text Preprocessing (shared by TF-IDF clustering algorithms)

The TF-IDF-based clustering pipelines share the same preprocessing stack:

1. **Boilerplate stripping** — removes generic legal boilerplate sentences (e.g. "Die Gesellschaft bezweckt...") using configurable DB regex patterns. Prevents generic legal terms from dominating cluster labels.
2. **Lemmatization** — spaCy `de_core_news_md` reduces words to their dictionary root. "betreibt" → "betreiben". Skipped when `use_keywords=True`.
3. **Stopword filtering** — DB `tfidf_stopwords` table (populated by `discover_stopwords` job and manual admin input). `cluster_analysis` job identifies candidates.
4. **TF-IDF vectorization** — up to 15,000 features, unigrams + bigrams, `min_df=5`, `max_df=0.4`.
5. **Dimensionality reduction** — TruncatedSVD 50 components + L2 normalisation.

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

#### TF-IDF K-Means (`tfidf_kmeans_cluster`)

The only supported clustering algorithm. All other algorithms (HDBSCAN, BIRCH, semantic K-Means) have been removed.

**How it works:**
- MiniBatchKMeans on the SVD-reduced space (50 dimensions)
- Default `n_clusters=50` — targeted at a clean, non-fragmented cluster set; near-duplicate labels (cosine > 0.88) are merged post-labeling
- Each company gets up to 3 clusters (`max_clusters_per_company=3`) via soft cosine similarity to centroids
- Low-quality clusters (mean IDF of top terms below `min_cluster_specificity=0.3`) are suppressed
- c-TF-IDF labels: top-5 terms per cluster with bigram deduplication
- After the run, `discover_stopwords` is auto-enqueued to mine the fitted vectorizer

**Best practice:**
1. Run `recompute_keywords` first to populate `purpose_keywords`
2. Then run K-Means with `use_keywords=True` — cleaner clusters since boilerplate is already absent
3. `discover_stopwords` runs automatically after — no manual cluster_analysis needed

**Parameters to tune:**
- `n_clusters` (default 50): Too few → over-broad clusters; too many → label fragmentation.
- `min_similarity` (default 0.20): Cosine threshold for cluster assignment. Lower → more assignments but noisier.

---

#### Recommended Workflow

```
1. Initial import (bulk + initial jobs)
       ↓
2. recompute_keywords   ← extracts purpose_keywords from raw purpose text (~20 min)
       ↓
3. tfidf_kmeans_cluster  use_keywords=True, n_clusters=50 (~25 min)
       ↓ (auto-triggers)
   discover_stopwords   ← 4-phase boilerplate/stopword discovery (~5 min)
       ↓
4. reclassify_noga     ← 2-stage classification, confidence ≥ 0.50
       ↓
5. claude_classify     ← AI scoring uses org-configured categories & target description
       ↓
6. recalculate_scores  ← recomputes combined_score (AI×0.60 + NOGA×0.25 + keywords×0.15)
```

For **ongoing imports** (daily SHAB updates): incremental keyword extraction, cluster assignment, and language detection (`purpose_language`) run automatically during the `initial` detail-fetch job using S3-cached model artifacts. Full re-cluster periodically (monthly or when corpus shifts significantly).

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

**Classification method (2-stage):**
1. **Stage 1 — Section vote:** Embed company `purpose_keywords` with `paraphrase-multilingual-MiniLM-L12-v2`, score all ~800 NOGA leaf codes, group top-K candidates by section letter (A–U), pick the section with the most votes.
2. **Stage 2 — Within-section re-rank:** Re-rank only codes belonging to the winning section by embedding cosine similarity. Cuts search space from ~800 to ~20–40 codes.
3. **Confidence gate:** If best score < 0.50, return no classification rather than assign a low-confidence code.
4. **Hierarchy path** — walk `noga_lookup.json` via `parentCode` links to build full ancestry (section → division → group → class → type).
5. **Multilingual labels:** Section labels for the `market-segments` API are derived directly from `noga_lookup.json` using `_collect_multilang_text()` — all four language variants (DE/FR/IT/EN) are available without hardcoding.

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
| `scikit-learn` | backend + ml | TF-IDF, TruncatedSVD, MiniBatchKMeans |
| `scipy` | backend + ml | Sparse matrix ops |
| `numpy` | backend + ml | Centroid math, cosine similarity |
| `spacy` + `de_core_news_md` | ml | German lemmatization (downloaded at build time) |
| `sentence-transformers` | ml | NOGA embedding (`paraphrase-multilingual-MiniLM-L12-v2`) |
| `lingua-language-detector` | backend + ml | Purpose language detection (`purpose_language` column); falls back to `langdetect` |
| `tqdm` | ml | Progress bars in pipeline CLI |

**K8s pod routing (from `job_worker.py`):**

```python
ML_JOB_TYPES = {"tfidf_kmeans_cluster", "discover_stopwords", "reclassify_noga", ...}
# → routed to helvex-ml pod when USE_RQ=true
```

When `USE_RQ=false` (local dev / thread mode), all jobs run in the same process.

**Model artifacts on S3 (`helvex-exports` bucket, `models/` prefix):**

| File | Written by | Read by |
|---|---|---|
| `tfidf_vectorizer.pkl` | all clustering jobs | incremental keyword extraction on new companies |
| `svd_transformer.pkl` | all clustering jobs | incremental cluster assignment on new companies |
| `kmeans_centroids.npy` | `tfidf_kmeans_cluster` | incremental cluster assignment |
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

### Organizations Management UI (frontend)

`/app/organizations` — a dedicated page for multi-org workspace management, accessible from the account dropdown between **General** and **Billing**.

**What it provides:**
- Grid of all organizations the user belongs to, each displayed as a card with name, slug, role badge, and tier
- The currently active workspace is highlighted; a chip confirms the active state
- **Switch workspace** — clicking "Switch to this org" calls `POST /api/v1/orgs/switch/{id}`, revalidates SWR `"me"` + `"my-orgs"`, and refreshes the router
- **Create new organization** — unlimited; the inline form calls `POST /api/v1/orgs`; after creation the new org becomes active and the card grid updates
- **Leave organization** — available for non-owner roles; calls `POST /api/v1/orgs/{id}/leave`
- **Team management** — the `OrgClient` (embedded) for member CRUD is rendered below the org grid for the currently active org

**Account page (`/app/account`) changes:**
- The Organization section now shows the active org with inline workspace-switcher pills (if the user belongs to multiple orgs) and a "Create another organization" inline form
- A "Manage team →" shortcut links to `/app/organizations`
- The former **Team** section (embedded `OrgClient`) has been removed from this page — it now lives exclusively in `/app/organizations`

**Header (`nav-bar.tsx`) changes:**
- The `<select>` org-switcher dropdown that previously appeared in the navbar when a user belonged to multiple orgs has been removed
- The account dropdown now includes **Organizations** between General and Billing (desktop) and in the mobile menu

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
rationale behind each decision. Most of these decisions apply to **RQ mode** (optional separate worker process, production-intended). **Thread mode** (default, `USE_RQ=false`) is simpler: it polls the DB in-process and does not use Redis.

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
| **Redis pub/sub** (RQ mode) | Workers publish to `jobs:{org_id}` channel on status transitions | <1 s for transitions | Separate worker process with Redis |
| **DB poll** (Thread mode) | SSE endpoint polls DB every 1 s | ~1 s | Default; single thread, no Redis |

In RQ mode with Redis, a **2 s periodic DB poll** runs alongside pub/sub to deliver progress-bar updates. Workers do not publish on every progress tick (which would flood Redis with dozens of messages per second for fast jobs); the periodic poll closes this gap with a 2 s lag that is invisible to users.

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
| One active per org | `bulk`, `detail`, `initial`, `recalculate_scores`, `recalculate_google_scores`, `reextract_purpose`, `reclassify_noga`, `re_geocode`, `tfidf_kmeans_cluster`, `discover_stopwords`, `recompute_keywords`, `cluster_analysis` |
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

### Redis connection pool for pub/sub publish (RQ mode only)

**Note:** This optimization applies only to RQ mode. Thread mode does not use Redis.

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

---

## 19. ML Pipeline Improvements (Apr 2026)

This section documents new ML services and the Explorer/scoring redesign shipped in the Apr 2026 sprint.

### New Files

| File | Purpose |
|---|---|
| `app/services/embeddings.py` | Shared multilingual embedding backbone (singleton SentenceTransformer) |
| `app/services/incremental_classify.py` | Classify newly imported companies inline (NOGA + clusters + language detection) |
| `app/services/stopword_discovery.py` | 4-phase automated boilerplate/stopword discovery pipeline |

### Shared Embedding Backbone — `app/services/embeddings.py`

All ML code that needs sentence embeddings shares a single lazy-loaded model instance:

```python
DEFAULT_MODEL = "paraphrase-multilingual-mpnet-base-v2"

embed_texts(texts, *, model_name, batch_size=256)  → np.ndarray  # (N, D) float32
embed_single(text, *, model_name)                  → np.ndarray  # (D,) float32
build_company_text(company)                        → str          # purpose + keywords
nearest_neighbours(query_vec, index_matrix, top_k) → list[(idx, cosine)]
```

`_get_model()` is `lru_cache(maxsize=1)` — the model loads once and stays in memory.

### Incremental Classification — `app/services/incremental_classify.py`

When new companies arrive via Zefix import they need inline classification. This service runs synchronously during `enrich_company()`.

**Entry point:**
```python
classify_new_companies_inline(db, company_ids, *, run_noga=True, run_clusters=True)
→ {"total": N, "noga_classified": X, "cluster_assigned": Y, "lang_detected": Z, "errors": [...]}
```

**Steps:**
1. `_run_noga_batch` — calls `apply_noga_classification()` per company (confidence ≥ 0.50 required).
2. `_run_cluster_batch` — `assign_new_companies_to_clusters()` using cached S3 artifacts.
3. `_run_language_detection_batch` — detects `purpose_language` (DE/FR/IT/EN/RM) via `lingua` (falls back to `langdetect`). Always runs; instant.

**Integration:**
```python
if clusters and company.tfidf_cluster is None and company.purpose:
    classify_new_companies_inline(db, [company.id], run_noga=False, run_clusters=True)
```

NOGA is skipped inline because it depends on `tfidf_cluster` — use the batch `reclassify_noga` job.

**Backfill:**
```python
backfill_unclassified(db, *, batch_size=500, run_noga, run_clusters, limit)
```

### Automated Stopword Discovery — `app/services/stopword_discovery.py`

`discover_stopwords` job auto-triggers after every `tfidf_kmeans_cluster` run. Four phases:

| Phase | Method | Output |
|---|---|---|
| 1 — IDF analysis | Terms with IDF < 0.92 (>40% doc frequency) from fitted vectorizer | Staged in `tfidf_stopwords` with `enabled=False` |
| 2 — Sentence dedup | Sentences appearing in >300 companies (MD5 hash across all languages) | Staged in `boilerplate_patterns` with `enabled=False` |
| 3 — Cross-cluster staging | Terms in labels of >60% of clusters | Staged in `tfidf_stopwords` with `enabled=False` |
| 4 — Claude review (opt-in) | Single Haiku call on top-50 candidates, grouped by language | `always_boilerplate` → `enabled=True` immediately |

Patterns approved (`enabled=True`) take effect on the next pipeline run via `_strip_purpose_boilerplate()`.

### New Relevance Score Formula

`combined_score` is now computed by `compute_relevance_score()` in `scoring.py`:

```
relevance_score =
    ai_score × 0.60
  + noga_confidence×100 × 0.25
  + keyword_density×100 × 0.15

keyword_density = min(keyword_count, 10) / 10   # normalized: 10+ keywords = 1.0

If ai_score is None → renormalize remaining weights (0.25 → 0.625, 0.15 → 0.375)
If all inputs None → return None
```

`flex_score` and `web_score` remain in the DB as supplementary data quality signals but no longer affect `combined_score`.

### New API Endpoint: Market Segments

`GET /api/v1/companies/market-segments`

Returns NOGA section statistics for the Explorer MarketMap component. Response:

```json
[
  {
    "section": "J",
    "labels": {"de": "Information und Kommunikation", "fr": "...", "it": "...", "en": "..."},
    "company_count": 2847,
    "avg_relevance": 74.2,
    "top_keywords": ["software", "saas", "cloud", "api", "data"],
    "canton_top": "ZH",
    "canton_pct": 42,
    "growth_recent": 312
  }
]
```

- Section labels are derived from `noga_lookup.json` via `_collect_multilang_text()` — no hardcoded map.
- Cached with 1-hour TTL (module-level cache, not Redis).
- `growth_recent` = companies with `first_sogc_date` in the last 18 months.

### Purpose Language Detection

`purpose_language` column (`de`/`fr`/`it`/`en`/`rm`) on the `companies` table. Populated by `_run_language_detection_batch()` in `incremental_classify.py`. Filterable via `?purpose_language=fr` on the company list endpoint.

### Explorer Rewrite (frontend)

`frontend/src/app/[locale]/app/explorer/explorer-client.tsx` fully replaced with a single-page Market Intelligence Hub layout:

| Component | Purpose |
|---|---|
| `MarketMap` | 21 NOGA section tiles, colour-coded by `avg_relevance`; click sets section filter |
| `SubSegmentStrip` | Division chips from `noga-hierarchy` endpoint; appears when section selected |
| `FilterSidebar` | Collapsible left panel: score range, canton, language chips, legal form, review/contact status, cluster autocomplete, keyword autocomplete, date range |
| `ScoringWizard` | 4-step modal: Clusters (autocomplete), Keywords (autocomplete), NOGA targets, Save |
| `ExplorerPage` | Semantic search header + stats strip + MarketMap + FilterSidebar/CompanyPanel split. Reads optional `?noga_code=<code>` URL param to pre-set the NOGA filter on mount (used by NOGA browser page). |

### NOGA Browser Page (frontend)

`frontend/src/app/[locale]/app/noga/noga-client.tsx` — full-hierarchy NOGA taxonomy browser.

- Fetches `GET /api/v1/companies/noga-hierarchy` (same cached endpoint used by Explorer).
- Renders 19 L1 sections as expandable rows with colour-coded letter badges (A–S).  
  Clicking expands to show L2 divisions → L3 groups → L4 classes → L5 types, each indented with connecting lines.
- Company count shown on every node (aggregated from descendants).
- Locale-aware labels: backend now returns `labels: {de, fr, it, en}` alongside the default `label` (de); frontend picks the right language via `getLabel(node, locale)`.
- Search bar filters the tree in real time (matches code prefix or translated label).
- "Browse" / arrow button on any node navigates to `/app/explorer?noga_code=<code>`.
- Nav entry: `ListTree` icon, label `t.nav.noga`, route `/[locale]/app/noga`.

**Backend change:** `app/crud/company.py` `get_noga_hierarchy()` now includes a `labels` dict (`{de, fr, it, en}`) in each node alongside the existing `label` (German default). Backward-compatible — old cache entries fall back to `label`.

Removed: 3-layer CategoryGrid → CategoryDetail → BrowseView navigation, business model filter, static cluster list in ScoringWizard, scoring weight step.

### Semantic Search Endpoint

`GET /api/v1/companies/semantic-search?q=<query>&top_k=8`

Embeds the query with the shared multilingual model, scores all taxonomy entries (clusters, keywords, NOGA codes) by cosine similarity, returns grouped results. Results with `similarity < 0.20` are filtered. Used by the Explorer search header with 400 ms debounce.

## 20. SOGC Person & Auditor Graph (May 2026)

Extracts structured person and auditor data from `sogc_changes` raw excerpts into a graph-ready schema.

### Tables

| Table | Purpose |
|---|---|
| `sogc_person_entities` | Canonical person node — one row per distinct natural person (identified by `normalized_key = lastname\|firstname\|hometown`) |
| `sogc_person_appearances` | Appearance edge — one row per SOGC change event (person_added/removed/changed). Links entity → company. |
| `sogc_auditors` | Legal entity auditors — structurally separate from natural persons (has UID, legal form, location) |
| `sogc_corporate_roles` | Corporate entities (companies) appearing as shareholders/officers — detected via CHE number in non-auditor person excerpts; model: `app/models/sogc_corporate_role.py` |
| `sogc_person_flags` | User-reported identity issues (`should_merge` / `should_split`) for manual disambiguation |

### Identity model

- `normalized_key = NFKD-lowercase(lastname)|NFKD-lowercase(firstname)|NFKD-lowercase(hometown)` — primary dedup key at insertion time
- Swiss Heimatort (`von X`) is the civil registry origin — stable for life, gives the key its distinctiveness
- `confidence_level`: `high` = entity has at least one bisher hard link OR has hometown + single residence; `medium` = has hometown but residence varies; `low` = foreign national (no Heimatort, name-only matching)
- False merge fix: `appearance.entity_override_id` re-assigns one appearance to a different entity
- False split fix: `entity.merged_into_id` points old entity → canonical; all queries filter `merged_into_id IS NULL`

**Bisher structured fields** — `sogc_person_appearances` carries five parsed bisher columns (`bisher_residence_municipality`, `bisher_lastname`, `bisher_firstname`, `bisher_is_foreign`, `bisher_nationality`) extracted from the `[bisher: ...]` annotation in each SOGC mutation. These encode the person's prior state at that company and are used by the bisher resolver to hard-link appearances across name changes.

### Pipeline (full reindex)

```
SOGC Preprocess  →  extract_sogc_persons (mode=all)  →  resolve_bisher_links
       │                       │                                  │
       │            Inserts appearances with                Union-find on entity
       │            bisher structured fields               IDs via bisher matches;
       │            Key-based entity dedup                 merges entities that
       │            (lastname|firstname|hometown)          differ only by name change
       ▼
SHAB daily → preprocess_company_sogc_pub() → extract_persons_for_publication()
                                                  ├── person_added/removed/changed (no CHE) → sogc_person_appearances
                                                  ├── person_added/removed/changed (CHE present, not auditor) → sogc_corporate_roles
                                                  └── auditor_change              → sogc_auditors
```

**Recommended run order for full re-import:**
1. `sogc_preprocess` (mode=all) — rebuild publications + changes
2. `extract_sogc_persons` (mode=all) — rebuild appearances + entities
3. `resolve_bisher_links` — merge name-change entities via hard links

### Known bugs fixed (May 2026)

**Bug 1 — Auditors appearing in the people list:**
The SOGC preprocessor creates a mirrored `person_added` row for each auditor entry found within a person section of a publication. The extractor was processing this mirror row, creating bogus `SogcPersonEntity` rows for auditor firms (e.g. "PricewaterhouseCoopers AG").

Fix: Added `_AUDITOR_EXCERPT_RE` in `sogc_person_extractor.py` — a multilingual regex matching Revisionsstelle/organe de révision/ufficio di revisione patterns. Applied as a `continue` guard before `_parse_person` in both extraction paths (`extract_persons_for_publication` and `run_extract_sogc_persons_batch`). Matching excerpts are skipped entirely on the person path (they are already handled by the auditor extractor).

**Bug 2 — Both `is_current = True` for same person at same company:**
`_parse_person()` naively set `is_current = True` for any non-`person_removed` change, regardless of temporal ordering. Two `person_added` rows for the same entity at the same company (years apart) both ended up with `is_current = True`.

Fix: Added `_recompute_is_current_for_entities(db, entity_ids)` in `sogc_person_extractor.py`. Groups appearances by `(entity_id, company_uid)`, sorts by `pub_date` descending, then: sets `is_current = False` for any `person_removed` top row, `is_current = True` for the first non-removed row, `is_current = False` for all older rows. Called after every batch in both extraction functions and also after entity merges in `run_resolve_bisher_links` (`sogc_entity_resolver.py`).

**Existing data repair:** `run_repair_is_current(db, batch_size=2000)` in `sogc_person_extractor.py` iterates all non-merged entity IDs in batches and calls `_recompute_is_current_for_entities`. Exposed as the `repair_is_current` job type via `POST /api/v1/scoring/repair-is-current`. Run once after deploying the fix.

### Person Network API

`GET /api/v1/sogc/persons/{entity_id}/network?include_past=true&co_director_limit=8`

Returns a 1-hop ego-graph for a person entity. No graph DB required — implemented with 2 SQL joins on existing tables.

**Response schema (`PersonNetworkOut`):**
```
entity: PersonEntityOut          # full entity record
mandates: MandateItem[]          # one per company
  company_uid, company_id, company_name
  role, role_category, signature_type
  date_from, date_to (YYYY-MM-DD | null if current)
  is_current
  co_directors: CoDirectorOut[]  # up to co_director_limit per company
    entity_id, lastname, firstname
    role, role_category, is_current, active_company_count
```

**Logic:**
1. Load all `sogc_person_appearances` for the entity (filtered by `include_past`); group by `company_uid` to produce one mandate with `date_from` (earliest pub), `date_to` (latest non-current pub), `is_current` (any current appearance).
2. Batch-fetch `company_id`/`company_name` from `companies` table.
3. For each company, fetch co-directors: join `sogc_person_appearances` → `sogc_person_entities` filtered to same company, excluding the central entity. Aggregate `active_company_count` per co-director.

### Jobs

| Job type | Endpoint | Worker | Description |
|---|---|---|---|
| `extract_sogc_persons` | `POST /api/v1/scoring/extract-sogc-persons` | api-worker | Parse sogc_changes → appearances + entities. Params: `mode` (missing\|all), `batch_size` |
| `resolve_bisher_links` | `POST /api/v1/scoring/resolve-bisher-links` | api-worker | Merge entities linked by bisher annotations (name changes). Params: `batch_size`. Run after extract_sogc_persons. |
| `repair_is_current` | `POST /api/v1/scoring/repair-is-current` | api-worker | Recompute `is_current` on all existing `sogc_person_appearances` in-place. One-time fix for historical data. |

### People page frontend (`people-client.tsx`)

The People page (`/app/people`) has been fully rewritten to add inline detail panels.

**Selection model:** Clicking anywhere on a `PersonEntityCard` or `AuditorCard` toggles selection (one person or auditor at a time). The detail panel renders inline below the selected card; clicking again deselects.

**Person detail panel** — `PersonDetailPanel` component. Opens inline, fetches `PersonNetworkData` via SWR keyed on `person-network-{id}-{includePast}`. Contains:

| View | Component | Description |
|---|---|---|
| Timeline (default) | `TenureTimeline` | Gantt chart: one row per company mandate. CSS grid layout (`192px` label + `1fr` bar track). Year axis with tick marks. Bars coloured by `role_category` (director=#dc2626, officer=#2563eb, other=#d97706). Muted opacity for past. Departure end-marker line. Dashed "now" line. Company name is a Link to `/app/companies/{id}`. |
| Network | `NetworkGraph` | Pure SVG (viewBox `0 0 820 560`, no D3). Person at centre (dark circle). Company mandates as rect nodes arranged radially at R=195. Co-director circles (radius ∝ `active_company_count`, clamped 8–18 px) clustered near their company at R=85. Solid edges for current, dashed for past. Red badge on co-directors with `active_company_count > 1`. Legend box top-left, stats box top-right. |

Controls: "Past mandates" checkbox (rekeys SWR), segmented Timeline | Network toggle, close button.

**Auditor detail panel** — `AuditorDetailPanel` / `AuditorClientsTimeline`. Simplified Gantt only: one bar per client company at the `pub_date` point (1-month width), coloured by `is_current`. No network view for auditors.

**Gantt math:**
- `minYear` = floor of earliest `date_from` across all mandates
- `maxDecimal` = `dateToDecimal(today) + 0.4`
- `pos(dateStr) = clamp(((year + month/12 - minYear) / span) * 100, 0, 100)`
- Co-director radial layout: `angle_i = (i/N) × 2π − π/2`, `cx = 410 + 195×cos(angle)`, co-director offset `coAngle = angle + (j − (K−1)/2) × 0.35`, `coX = cx + 85×cos(coAngle)`

### Key files

| File | Purpose |
|---|---|
| `app/services/sogc_person_extractor.py` | Regex-based DE/FR/IT parser, bisher field parsing, entity upsert, confidence recomputation, batch job; `_AUDITOR_EXCERPT_RE` auditor skip guard; `_recompute_is_current_for_entities`; `run_repair_is_current` |
| `app/services/sogc_entity_resolver.py` | Bisher-first entity resolution: union-find, bisher match lookup, entity merge; calls `_recompute_is_current_for_entities` after merges |
| `app/services/job_handlers/sogc_persons.py` | Job handler for extract_sogc_persons |
| `app/services/job_handlers/sogc_entity_resolution.py` | Job handler for resolve_bisher_links |
| `app/services/job_handlers/sogc_repair.py` | Job handler for repair_is_current |
| `app/api/routes/persons.py` | Person/auditor search, company-scoped endpoints, flag reporting; `GET /sogc/persons/{id}/network` ego-graph endpoint |
| `frontend/src/lib/types.ts` | `CoDirector`, `MandateItem`, `PersonNetworkData` types |
| `frontend/src/lib/api.ts` | `fetchPersonNetwork(entityId, params?)` |
| `frontend/src/components/board-panel.tsx` | Company detail "Board & Officers" panel |
| `frontend/src/app/[locale]/app/people/page.tsx` + `people-client.tsx` | People search list (Persons + Auditors tabs). Person cards navigate to the detail page. Auditor cards expand inline with `AuditorDetailPanel` / `AuditorClientsTimeline`. |
| `frontend/src/app/[locale]/app/people/[id]/page.tsx` | Server component — fetches `SogcPersonEntity` via `GET /sogc/persons/{id}`, passes to `PersonDetailClient`. |
| `frontend/src/app/[locale]/app/people/[id]/person-detail-client.tsx` | Full-page person profile: dark slate-900 hero header (name, hometown, confidence, verified, identity notes, LinkedIn), role-breakdown stats strip, `TenureTimeline` (full-width Gantt), `NetworkGraph` (1100×620 SVG, wider radial layout), Timeline/Network toggle + Past checkbox. |
