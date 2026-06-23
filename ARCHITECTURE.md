# Helvex — Architecture Reference

> Internal documentation for bug fixing and onboarding.
> **Stack:** FastAPI · PostgreSQL · K3s/Hetzner · Helm · Terraform · Next.js
> **Repo:** `helvex` (product name: Firmiq)
> **Note:** Last updated June 2026. Redis and RQ mode have been fully removed; the job worker is thread-only.

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
16. [Web Crawler Pipeline](#16-web-crawler-pipeline)
17. [Activity Log](#17-activity-log)
18. [Common Bug-Fixing Cheatsheet](#18-common-bug-fixing-cheatsheet)
19. [Background Job System — Design Evolution](#19-background-job-system--design-evolution)
22. [Security Hardening Pass (Jun 2026)](#22-security-hardening-pass-jun-2026)

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
│   ├── clients/                # External API wrappers (Zefix, Serper, Geocoding, SHAB, UID)
│   │   ├── zefix_client.py     # Zefix REST API client
│   │   ├── uid_client.py       # UID register SOAP client (zeep); formats UIDs, parses registration_type
│   │   ├── google_search_client.py      # Serper.dev wrapper
│   │   ├── scrapingdog_search_client.py # ScrapingDog wrapper (google.ch, per-company location/language)
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
│       ├── uid_import.py       # UID register import (two-phase: active + cancelled)
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
├── docker-compose.yml          # Local dev (app + postgres + nginx)
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

**Migration serialization across pods:** `entrypoint.sh` runs `alembic upgrade head` before exec'ing the app, and step 1 above runs it again in-process — and every one of the 5 deployments (app, frontend, api-worker, ml-worker, crawler-http ×2) does both, independently, on every pod start/restart. A plain `op.add_column` isn't idempotent, so concurrent sessions racing for the same migration's `ACCESS EXCLUSIVE` table lock would previously queue and get killed by the engine-wide 30s `statement_timeout` (`app/database.py`) before any of them finished — observed in production as a `0098 → 0099` migration repeatedly failing across unrelated deploys (`crawler-http` uses a `Recreate` strategy, so a normal deploy alone produces several concurrent migration attempts). Fixed in [alembic/env.py](alembic/env.py): `run_migrations_online()` now takes a session-level Postgres advisory lock (key `727001`) for the duration of the migration connection, with `statement_timeout` disabled on that connection only — every other pod simply waits its turn instead of contending for the table lock.

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
| GET | `/api/v1/admin/crawler/stats` | Superadmin | Crawler health: status/tier/candidate counts, page storage, extraction coverage |
| GET | `/api/v1/admin/crawler/failures` | Superadmin | Paginated terminal crawl failures with company name, URL, error detail |
| POST | `/api/v1/admin/jobs/crawler/reset-http` | Superadmin | Reset HTTP-tier terminal failures back to pending |
| POST | `/api/v1/admin/jobs/crawler/reset-playwright` | Superadmin | Reset Playwright-tier terminal failures back to pending |
| POST | `/api/v1/admin/jobs/crawler/populate-urls` | Superadmin | Enqueue `web_url_populate` backfill job |
| POST | `/api/v1/admin/jobs/crawler/extract` | Superadmin | Enqueue `web_extract` job (also triggered automatically after each crawl batch) |
| POST | `/api/v1/admin/jobs/crawler/reextract` | Superadmin | Flag all crawled S3 HTML for re-extraction + run `web_extract` (no re-crawl) |
| GET | `/api/v1/companies/{id}/web-extract` | Authenticated | Best web extract + per-page crawl coverage for the company detail "Website" tab |
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
| `google_search_results_raw` | Text | Scored organic results as JSON [{title, link, snippet, score}] |
| `google_search_full_raw` | Text | Complete provider JSON (ScrapingDog only; null for Serper) |
| `google_search_params` | JSON | Exact params sent to search API: provider, q, gl, hl, location, municipality, address_zip, purpose_language_raw — diagnose wrong-language/location results |
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
| `registration_type` | String(16) | `hr` \| `mwst` \| `both` \| `uid_only` \| NULL — populated by UID import job |
| `uid_raw` | Text/JSON | Raw UID web service response for this entity (audit trail) |

**`registration_type` values:**
- `hr` — registered with Handelsregister only (classic Zefix companies not in MWST)
- `mwst` — VAT-registered only (sole proprietors, freelancers below HR threshold)
- `both` — registered in both HR and MWST registers
- `uid_only` — in UID register via another authority (AHV employer, SUVA, statistical register, etc.)
- `NULL` — legacy row, type not yet resolved by the UID import job

**`source` values** (extended):
- `zefix` — imported from Zefix commercial register
- `shab_stub` — minimal stub created from a SHAB publication before full Zefix data arrived
- `uid` — imported exclusively from the UID register (no Zefix entry exists)

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

#### `CompanyError` — `app/models/company_error.py`

Per-company pipeline failure log. Written by services during batch jobs; reviewed in the admin Error Center.

| Column | Notes |
|---|---|
| `company_id` | FK → companies.id (nullable — job-level errors have no company) |
| `error_source` | `web_enrichment` \| `zefix_import` \| `geocoding` \| `noga` |
| `error_type` | `enrich_failed` \| `import_failed` \| `geocode_failed` \| `extract_failed` |
| `message` | Human-readable error message |
| `detail_json` | JSON blob with extra context (URL, HTTP status, etc.) |
| `job_run_id` | FK → job_runs.id (optional context) |
| `resolved_at` / `resolved_by` | Cleared when admin applies a data correction |
| `ignored` | True if admin dismissed without formal fix |

Instrumented in: `app/services/web_enrichment.py` (per-company Google search failures), `app/services/zefix_import.py` (per-UID import failures).

CRUD: `app/crud/company_error.py` — `log_error()` deduplicates active errors per (company, source).
Admin endpoints: `GET /admin/errors`, `POST /admin/errors/{id}/resolve`, `POST /admin/errors/{id}/ignore`, `PATCH /admin/companies/{id}/correct`.
Frontend: `frontend/src/app/[locale]/app/admin/errors/` — Error Center page with data quality dashboard + tabbed error list + inline correction panel.

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

**Note:** Redis and RQ mode have been fully removed. Rate limiting is in-process only.

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

**Thread mode (only mode — Redis/RQ removed)**
- Daemon thread pool (`app/services/job_worker.py`), polls `job_runs` table for `status=queued`
- Executes jobs concurrently via `ThreadPoolExecutor` (configurable `max_workers`)
- No external dependencies; uses in-memory progress tracking
- Set `DISABLE_JOB_WORKER=true` to suppress the thread (e.g., API-only pod)

**Job handler registry pattern** — Replaced the previous 735-line `elif` chain
- Each job type has a dedicated handler in `app/services/job_handlers/{type}.py`
- `_run_job()` dispatches via `JOB_HANDLERS[job_type](ctx)`, passing context (DB, params, progress callback, abort signal)
- Handlers return `(stats_dict, done_message)` or raise typed exceptions (`JobPausedError`, `JobCancelledError`, `JobWaitingExternalSignal`)

**Atomic job claiming (multi-pod safety)**
- `crud.atomic_claim_job(db, job_id)` issues a single `UPDATE job_runs SET status='running' WHERE id=? AND status IN ('queued','paused')` and checks `rowcount == 1`
- Only one pod wins the race even if both call `_run_job` simultaneously — the second UPDATE finds the row already `status='running'` and returns `rowcount=0`, causing an immediate return
- `get_next_queued_job(..., skip_locked=True)` uses `SELECT ... FOR UPDATE SKIP LOCKED` so that when both idle pods poll simultaneously, each naturally claims a different queued job rather than both racing for the same one

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
| `link_sogc_stubs` | `batch_size` | Back-fill `company_id` on existing `sogc_publications` + `sogc_person_appearances` rows; creates `shab_stub` Company rows for unknown UIDs — no API calls | — |

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

### Google Search — `app/clients/google_search_client.py` (Serper) + `app/clients/scrapingdog_search_client.py` (ScrapingDog)

Two pluggable providers. Active provider is controlled by the `google_search_provider` setting (`serper` | `scrapingdog`, default `serper`), switchable in the superadmin Settings → LLM tab (or Admin tab) without a restart.

**Serper.dev** (`app/clients/google_search_client.py`):
- API key: `serper_api_key` DB setting (takes precedence) or `SERPER_API_KEY` env var
- POST to `google.serper.dev/search`; `gl=ch`
- Per-company context: `location="{municipality}, Switzerland"`, `hl` from `purpose_language` (fallback `de`)

**ScrapingDog** (`app/clients/scrapingdog_search_client.py`):
- API key: `scrapingdog_api_key` DB setting (takes precedence) or `SCRAPINGDOG_API_KEY` env var
- GET to `api.scrapingdog.com/google/`; `domain=google.ch`, `country=ch`
- Per-company context: `location="{address_zip} {municipality}, Switzerland"`, `language` from `purpose_language` (fallback `de`)
- Returns full provider JSON (includes `local_results`, `organic_results`, `search_information`, pagination)
- Full response stored in `google_search_full_raw` on `Company`

**Common:**
- Daily quota: `GOOGLE_DAILY_QUOTA` (default 100; free tier ~83)
- Quota tracked in `app_settings` table; resets daily
- `web_enrichment.py` dispatches to the active provider; scoring pipeline is identical for both

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
- **Model**: Configurable via `claude_model` key in `app_settings` (default: `claude-haiku-4-5-20251001`). Read at runtime by `get_claude_default_model()` in `app/services/claude.py`. Changeable in Settings → LLM tab without a restart. Valid values: `claude-haiku-4-5-20251001`, `claude-sonnet-4-6`, `claude-opus-4-6`.
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

### UID Register Import — `app/services/uid_import.py`

Imports all entities from the Swiss UID (Unternehmens-Identifikationsnummer) register via the V5.0 PublicServices SOAP API.

**API endpoint:** `https://www.uid-wse.admin.ch/V5.0/PublicServices.svc` (correct as of BFS spec v5.0, Oktober 2018; **not** `uid.admin.ch/uid-wse/` which is a web portal, not the service endpoint).

**WSDL:** `https://www.uid-wse.admin.ch/V5.0/PublicServices.svc?wsdl`

**V5.0 PublicServices constraints:**
- Operation: `Search` (not `SearchByCriteria` from old V3.0)
- `searchMode=Normal` does a SQL **contains** search — not prefix/starts-with
- Max **30 results per call** — no pagination token exists
- Single-character `organisationName` returns 0 results (API minimum ~2 chars)
- Rate limit: `Request_limit_exceeded` raised on excessive call frequency; client retries with exponential backoff

**New data this adds:**
- MWST/VAT-only companies (sole proprietors below CHF 100k HR threshold, freelancers)
- AHV employer registrations without HR entry
- Entities in statistical register (BFS), SUVA, etc.
- Dissolved / historical UIDs not in current Zefix

**2-char pair sweep strategy (guarantees completeness):**
- Iterate all 1,296 two-character pairs from `_SWEEP_CHARS` ("AA", "AB", ..., "Z9", "ZZ")
- "Contains" semantics ensure every company is found by at least one pair (all company names have a 2-char alphabetic/numeric substring in the sweep set)
- When a pair returns exactly 30 (the cap), recursively expand to 3-char, 4-char sub-prefixes until each bucket returns < 30
- `seen_uids` set deduplicates across overlapping prefix buckets
- Resume via `resume_from` pair index (0–1295) stored in job progress

**Upsert behaviour:**
- Existing rows: only `registration_type` and `uid_raw` updated; purpose/scores/geocodes preserved.
- New rows: `source=uid`; no purpose → skipped by NOGA, scoring, and Claude classification.

**`registration_type` derivation:** from V5.0 response — `commercialRegisterStatus=2` → `hr`; `vatRegisterInformation.vatStatus=2` → `mwst`; both → `both`; neither → `uid_only`.

**`legalForm`:** stored as eCH-0097 code (e.g. `"0106"` for GmbH, `"0109"` for AG) — text descriptions require eCH-0097 code table lookup.

**Client:** `app/clients/uid_client.py` — lazy zeep WSDL init; `_search_page(prefix)`, `iter_entities_by_prefix(prefix)`, `get_by_uid(uid_str)`, `detail_to_update(entity)`.

**Scoring note:** UID-only companies (no `purpose`) excluded at DB query level from NOGA, keyword extraction, Claude classification.

**Job type:** `uid_import` | **Endpoint:** `POST /api/v1/jobs/collection/uid-import`
**Detail job:** `uid_detail` | **Endpoint:** `POST /api/v1/jobs/collection/uid-detail`

### SHAB Archive Import — `app/services/shab_archive_import.py`

Imports historical SHAB publications from `shab.ch`. Operates in two modes depending on the date range, dispatched automatically by `handle_shab_archive` in `app/services/job_handlers/shab_archive.py` (cutoff: `2012-12-01`):

#### Mode A — Pre-2012 bulk PDF (`import_shab_old_pdfs`)

Pre-December 2012, SHAB published one PDF per day covering all cantons + all publication types. Endpoint: `GET /api/v1/archive/issue-of-today?date=YYYY-MM-DD&language=de&tenant=shab` (requires browser User-Agent; 404 on weekends/holidays).

**PDF format eras** — three distinct delimiter structures; auto-detected by `_find_entry_delimiters`:
| Era | Delimiter format | Pub-number digits | Entry bullet |
|---|---|---|---|
| 2002–mid-2008 | `Tagebuch Nr. NNNN vom DD.MM.YYYY\n(NNNNNN / CH-…)` | 6 | `I ` (Roman I) |
| mid-2008 | `Tagesregister-Nr. NNNN vom DD.MM.YYYY\n(NNNNNNNN / CH-…)` | 8 | `■` |
| 2009–2012 | `Tagesregister-Nr. NNNN vom DD.MM.YYYY / CH-… / NNNNNNNN` (single line) | 8 | `■` |

The `■` glyph is encoded differently by PyMuPDF depending on PDF year: `\x84` (U+0084, 2008–2011) or a Unicode PUA codepoint like U+F06E (2012+).

**Workflow:** iterates weekdays Mon–Fri; skips 404s; calls `check_bulk_pdf_structure` → `parse_bulk_hr_entries`; upserts `SogcPublication` rows with `sogc_id = "shab_old_{YYYYMMDD}_{pub_number}"` and `company_uid = company_id = None` (old CH-xxx numbers don't map to CHE UIDs).

**Structural validation:** `check_bulk_pdf_structure` logs critical format changes to the Error Center (`company_errors` table, `source="shab_old_pdf"`) rather than silently skipping. Days with critical issues (HR end marker missing, zero delimiters) are counted in `days_skipped`.

#### Mode B — Post-2012 per-publication API (`import_shab_archive`)

Paginates the `shab.ch` public archive API (`https://www.shab.ch/api/v1/archive/public`). PDF-based; links publications to the `companies` table and creates stub entries for cancelled companies absent from Zefix.

**Workflow:**
1. `fetch_archive_page(page, size)` — paginates the archive list (`includeContent=false`).
2. For each HR01/HR02/HR03 entry: `fetch_pdf_bytes(id)` → `extract_text_from_pdf()` (pypdf).
3. Extract UID via regex (`CHE-xxx.xxx.xxx`), detect language (lingua + canton fallback).
4. `_resolve_company_for_shab(db, uid, title, canton, pub_date, uid_map, stats)` — looks up the company by UID (batch cache → DB). Three outcomes:
   - **Found, same name** — link publication to company; no change.
   - **Found, different name** — merge SHAB title as a historical name into `company.old_names` (`{"name": ..., "source": "shab_archive", "date": ...}`), then link.
   - **Not found** — create a `Company` stub with `source="shab_stub"`, `status="CANCELLED"`, `uid`, `name` (from API title), `canton`, `first_sogc_date`; add to batch `uid_map` to avoid duplicates within the same page.
5. Upsert `SogcPublication` with extracted text; detect changes via `_detect_changes` from `sogc_preprocessor.py`.
6. Supports pause/resume: `progress_done` stores the last completed page number.

**Company source field:** `companies.source` distinguishes import origin: `"zefix"` (bulk/detail import), `"shab_stub"` (stub created by SHAB archive). Legacy rows have `NULL`. If a stub later appears in a Zefix import, `source` is updated to `"zefix"` automatically (included in `REEXTRACTABLE_FIELDS`). Stubs have `status="CANCELLED"` so they are excluded from the lead dashboard by the existing `_DELETED_STATUSES` filter.

**sogc_id conventions:**
- Post-2012 API: `"shab_{archive_id}"` (e.g. `"shab_4447021"`)
- Pre-2012 PDF: `"shab_old_{YYYYMMDD}_{pub_number}"` (e.g. `"shab_old_20100315_05541344"`, max 27 chars, within String(32))

**Job type:** `shab_archive` — registered in `JOB_HANDLERS`, ONE_PER_ORG. Params: `date_start`, `date_end`, `mode` (`auto`|`old_pdf`|`api`). Auto-mode routes dates < 2012-12-01 to Mode A and dates >= 2012-12-01 to Mode B.

**API endpoint:** `POST /api/v1/collection/shab-archive` (superadmin only).

**Frontend:** Collection page → SHAB / SOGC group → "SHAB Archive Import (shab.ch)" section.

**Dependencies:** `pypdf>=4.0.0`, `PyMuPDF (fitz)>=1.23.0` added to `requirements.backend.txt`.

### Link SOGC Stubs — `run_link_sogc_stubs` in `app/services/shab_archive_import.py`

Back-fills `company_id` on `sogc_publications` and `sogc_person_appearances` rows that already have a `company_uid` but no `company_id`. Works entirely from already-imported DB data — no API calls or PDF downloads.

**Algorithm (keyset-paginated, batch_size UIDs per commit):**
1. Find distinct `company_uid` WHERE `company_uid IS NOT NULL AND company_id IS NULL` in `sogc_publications`, ordered alphabetically for cursor pagination.
2. For each batch: bulk-fetch matching `Company` rows.
3. For UIDs without a Company row: read `raw_json["title"]` (SHAB archive format) or `raw_json["meta"]["title"]["de"]` from one publication to get a display name; create a `shab_stub` Company (same savepoint + IntegrityError pattern as `_resolve_company_for_shab`).
4. Bulk-update `sogc_publications.company_id` and `sogc_person_appearances.company_id` via SQLAlchemy `update()` statements.

**Job type:** `link_sogc_stubs` — registered in `JOB_HANDLERS`.

**API endpoint:** `POST /api/v1/collection/link-sogc-stubs` (superadmin only).

**Frontend:** Collection page → "Link SOGC Stubs" section (after SHAB Archive Import).

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
2. `reclassify_noga` — Hybrid classifier: (60%) pgvector cosine similarity to language-matched NOGA embeddings + (40%) token overlap from company name/purpose/keywords. Returns `noga_code`, `noga_confidence` (0–1), and full ancestry path. Controlled by `embed_mode` parameter (see below). **Important:** uses `query.with_entities(func.count(Company.id)).scalar()` (not `query.count()`) to count the working set — avoids PostgreSQL wrapping all 700k company columns in a subquery, which hit the 30 s statement timeout under peak nightly load.
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

**Progress-count performance (`include_stale`):** [app/services/noga_pipeline.py](app/services/noga_pipeline.py) `reclassify_noga`'s `include_stale` mode ORs `noga_code IS NULL` with a cross-column comparison (`noga_classified_at < updated_at - interval`), which can't be served by a btree index and forces a full table scan — this timed out in production (job 692, 2026-06-16) even after bumping `statement_timeout` to 120s. The exact `COUNT(*)` for this path was replaced with a cheap planner estimate (`pg_class.reltuples`) since it's only used for progress display, not iteration logic. The plain `only_missing_noga` (non-stale) path keeps an exact, index-backed count via the `ix_companies_no_noga_code` partial index added in migration 0102.

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

1. kubectl / helm / helmfile checked with `command -v` — only installed if missing (cached between runs); each download is checksum-verified (`sha256sum -c`) rather than piped directly to a shell/`tar`
2. Ensure K8s secrets exist (`helvex-env`, `ghcr-pull-secret`, `arc-github-app`)
3. Bootstrap CRDs (`[deploy-prod]` only): cert-manager + CloudNativePG
4. Resolve PostgreSQL backup server names from S3 pointer file
5. Helmfile apply (full/app modes) or `kubectl set image` (component-only modes)
6. Rollout wait scoped to the deployed component(s)
7. Bump minor semver tag (`[deploy-prod]` / `[deploy-app]`)

Each of the four image build/push steps (backend, ml-base, ml, frontend) is followed by an `aquasecurity/trivy-action` scan (`severity: CRITICAL,HIGH`, `exit-code: "0"` — report-only, not a hard gate yet; see [roadmap.md](roadmap.md)). Trivy scanning is in `deploy-prod.yml` only, not `deploy-dev.yml`.

Backend image is signed with Cosign after push.

### `cleanup.yml` — weekly cron (Sun 02:00 UTC)

- Delete untagged GHCR images
- Retain last 5 tagged versions

### Action pinning & dependency updates

All third-party `uses:` references across `ci.yml`, `deploy-dev.yml`, `deploy-prod.yml`, and `cleanup.yml` are pinned to immutable 40-character commit SHAs (`uses: <action>@<sha> # vX.Y.Z`), not mutable tags. `renovate.json` (repo root) keeps these pins current — see [runbook.md §25](runbook.md#25-keeping-k3s-and-the-servers-up-to-date) for the full update policy (patch/digest auto-merges; minor/major and K3s/Postgres/Helm/Helmfile pins require manual review).

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
| `ingress.yaml` | Ingress + Middleware | Traefik routing; TLS via cert-manager; rate-limit Middleware (`ingress.rateLimit`); `/docs` path gated by `ingress.exposeDocs` (off in prod) |
| `clusterissuer.yaml` | ClusterIssuer | Let's Encrypt |
| `networkpolicy.yaml` | NetworkPolicy | Isolates helvex namespace |
| `postgres-networkpolicy.yaml` | NetworkPolicy | Postgres-only ingress allowlist: app-tier pods, `cnpg-system` operator, same-cluster replicas, node-subnet `ipBlock` (kubelet probes bypass pod selectors) — see [roadmap.md](roadmap.md) for scoping caveats |
| `servicemonitor.yaml` | ServiceMonitor | Prometheus scrapes `/metrics` |

**Pod security (all pods):**
```yaml
securityContext:
  runAsNonRoot: true
  runAsUser: 1000
  allowPrivilegeEscalation: false
  capabilities: { drop: [ALL] }
  readOnlyRootFilesystem: true   # all 5 deployments (app, frontend, api-worker, ml-worker, crawler-http)
automountServiceAccountToken: false
```
Each deployment mounts an `emptyDir` for `/tmp` (and, for the frontend, an additional `emptyDir` at `/app/.next/cache` for Next.js Image Optimization) to provide writable scratch space under the read-only root filesystem.

**Networking values (`values.yaml`, `network:` key)** — single source of truth referenced by `postgres-cluster.yaml` (`pg_hba`, now scoped to `network.podCidr` instead of `0.0.0.0/0`) and `postgres-networkpolicy.yaml` (`network.nodeSubnetCidr`):
```yaml
network:
  podCidr: "10.244.0.0/16"        # K3s --cluster-cidr
  nodeSubnetCidr: "10.0.1.0/24"   # Hetzner private node subnet
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
| `firewall` | Inbound: SSH (22), HTTP (80), HTTPS (443) from configured admin CIDRs; outbound: all except ICMP (restricted) |
| `servers` | Control plane `cx23` (static IPv4, K3s init via cloud-init); worker/DB node `cx33` (taint: `helvex.io/role=database:NoSchedule`) |
| `loadbalancer` | Hetzner LB (`lb11`); targets all non-DB nodes; health check on `GET /health` |

### Server access hardening (`control-plane.yaml.tpl` cloud-init)

- **K3s/Helm/Helmfile installs are checksum-verified** during cloud-init (not bare `curl \| sh`), and `k3s_version` is pinned via the Terraform variable rather than tracking `latest`. Changing `k3s_version` only affects newly-provisioned/replaced nodes — see [runbook.md §25](runbook.md#25-keeping-k3s-and-the-servers-up-to-date) for why this can't patch already-running nodes (`hcloud_server.user_data` forces replacement, and `db1` has no separate Hetzner Volume — `db_volume_size_gb = 0` — so a recreate is destructive without a verified backup).
- **`ubuntu` user passwordless sudo is scoped**, not blanket: `/etc/sudoers.d/ubuntu` only allows `systemctl {restart,start,stop,status} k3s`, `journalctl -u k3s [-f]`, `apt-get update`, `apt-get upgrade -y`, `reboot`. Anything broader (manual K3s binary upgrades, `dist-upgrade`, root-owned config edits) requires direct root SSH.
- **PAM (`pam_access`) restricts SSH/sudo to `admin_cidrs`** (`/etc/security/access.conf`, wired into `/etc/pam.d/sshd`) — connections from outside the configured admin IP range(s) are rejected at the PAM layer before any password/key prompt, independent of the Hetzner Cloud Firewall rules. Update `admin_cidrs` in `infra/terraform/envs/prod/variables.tf` if the admin IP changes.

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

## 16. Web Crawler Pipeline

### Overview

Three-phase pipeline: crawl ~210k Swiss company websites → store raw HTML in S3 → extract structured data (contacts, UID, socials, keywords) into `company_web_extract`.

### Pod topology

| Pod | Image | Job types | Node |
|---|---|---|---|
| `crawler-http` × 2 | `helvex-app` | `web_crawl_http`, `web_url_populate`, `web_select_url`, `web_crawl_single` | main node |
| `api-worker` | `helvex-app` | (many others, no longer `web_extract`) | main node |
| `ml-worker` | `helvex-ml` | `web_crawl_http`, `web_crawl_playwright` (idle-fill), **`web_extract`** + all ML job types | ml node (cax21) |

`web_extract` runs **only on `ml-worker`** — it is the only image with the spaCy NER models (`fr/it/en_core_news_sm`, `de_core_news_md`) bundled (see `Dockerfile.ml-base`). This replaces the earlier "Pod A crawls, Pod B extracts" parallelism on `crawler-http`/`api-worker`: extraction no longer runs concurrently with crawling on the main node, it queues for ml-worker instead. Trade-off accepted to keep spaCy off the main-node app image.

### Workflow

```
batch enrichment job   → Serper/ScrapingDog fills google_search_results_raw
                         auto-populates company_url_candidates (upsert safe on
                         existing crawled candidates) + creates company_crawl_state
                         only if no candidate is selected yet — preserves manual
                         URL selection. web_url_populate job still available as
                         a backfill tool for companies enriched before this change.
       ↓
web_crawl_http         SKIP LOCKED claim → robots/sitemap discovery → httpx fetch
                       (Sec-Ch-Ua client hints + HTTP/2; streaming body read
                        capped at MAX_PAGE_BYTES before decompression fills memory;
                        curl_cffi Chrome-TLS impersonation fallback on bot-block)
  bot_blocked          → cloudflare/js_challenge escalate to js_required (→ Playwright);
                         other types → crawl_status=bot_blocked
  js_required          → crawl_status=js_required, tier=playwright
  http_error/timeout   → backoff retry via next_crawl_at (≤3×), then terminal
  no_content           → crawl_status=no_content
  success              → HTML → S3 (keyed crawl/{company_id}/{url_candidate_id}/{page}.html),
                         company_web_pages rows, crawl_status=crawled
                         Prometheus: crawl_result_total{tier, status} incremented per outcome
                         **auto-enqueues web_extract** (dedup returns existing if already running)
       ↓ (parallel)
web_crawl_playwright   SKIP LOCKED claim tier=playwright|js_required
                       Playwright + stealth; resource-blocking (no img/font/media),
                       wait_until=load, optional real-Chrome channel (PLAYWRIGHT_CHANNEL)
       ↓ (parallel, different pod)
web_extract            distinct companies WHERE company_web_pages.needs_extraction=TRUE
                       S3 HTML → trafilatura + regex/schema.org/phonenumbers
                       → company_web_extract PK (company_id, url_candidate_id)
                         one row per company+URL tried; get_best_web_extract() selects
                         highest-confidence row (then most recent) for downstream use
                       deterministic only; LLM enrichment deferred (see ROADMAP)
                       Runs concurrently with crawling: auto-triggered by _run_crawl_batch
                       when crawled > 0; dedup prevents stacking (one active instance only)
```

Both crawler tiers run robots.txt + sitemap.xml discovery (`crawler_sitemap.py`) before
page selection: sitemap URLs fill subpage slots the homepage nav misses, and the robots
`Crawl-delay` raises the per-domain rate limit.

### Job params — web_crawl_http and web_crawl_playwright

Both share: `batch_size`, `canton`, `max_pages`, `rate_limit_delay`, `order_by`, `limit`.

`order_by` values (applied via `claim_crawl_batch`):
- `company_id_asc` — default, stable ordering
- `last_crawled_asc` — oldest crawled first (useful for refresh runs)
- `flex_score_desc` — highest flex score first (needs JOIN companies)
- `combined_score_desc` — highest combined score first (needs JOIN companies)

`limit` — stop after this many companies total; batch size is clamped to `min(batch_size, limit - done)`.

Playwright-only: `rerun: bool` — calls `crawler_crud.reset_playwright_crawled()` before starting, which resets `crawl_status IN (crawled, bot_blocked, http_error, timeout, no_content)` rows with `tier=playwright` back to `pending`.

### URL selection

- **Automatic (new):** `run_batch_collect` upserts candidates immediately after each Google enrich. `select_best_candidate` only fires if no candidate is selected, so re-enriching a company never demotes a manually-chosen or already-crawled URL.
- **`web_url_populate`:** backfill job for companies enriched before the auto-populate was added; idempotent.
- **`web_select_url`** (params: `company_id`, `url_candidate_id`): switch to a specific candidate, resets crawl state.
- Failure statuses are terminal until manually re-queued, or use `rerun=True` on the playwright job.

### S3 key layout

```
crawl/{company_id}/{url_candidate_id}/{page_type}.html
```

Each crawl session (URL candidate) gets its own prefix, so switching a company to a different URL and re-crawling does not overwrite earlier HTML. The `s3_key_html` stored in `company_web_pages` always points to the exact key for that crawl.

### Large-response / zip-bomb protection

- **httpx:** `_fetch` uses `client.stream()` — decompression is streamed and halted at `MAX_PAGE_BYTES` (5 MB). A gzip bomb never fully expands in memory.
- **Content-Length check:** if `Content-Length` header already exceeds the cap, the body is skipped entirely before downloading.
- **Playwright:** body is captured via `page.content()` (browser-level) and then sliced to `MAX_PAGE_BYTES` in Python. The `company_timeout` (120s) prevents hangs.
- **curl_cffi fallback:** `resp.content[:MAX_PAGE_BYTES]` — curl decompresses internally before the slice; risk is low given it's a rarely-used fallback path.

### Error measurement

Crawl outcomes feed `crawl_result_total{tier, status}` Prometheus counter (in `app/metrics.py`) at every outcome point in `_run_crawl_batch`:

| status | meaning |
|---|---|
| `crawled` | success |
| `js_required` | HTTP→Playwright escalation |
| `bot_blocked` | hard block, not escalated |
| `http_error` | non-bot HTTP failure |
| `timeout` | per-page or total timeout |
| `no_content` | near-empty body |
| `error` | unexpected exception |

Grafana query: `rate(crawl_result_total[5m])` grouped by `tier` and `status`. Per-company detail lives in `company_crawl_state.crawl_status` + `crawl_error_detail` for targeted re-queue or candidate switch.

### Self-preemption (ML worker)

`web_crawl_http` and `web_crawl_playwright` check for queued ML jobs in every progress callback. If an ML job is waiting, `pause_requested` is set and `JobPausedError` is raised — the crawl saves its progress and the ML job runs next.

### Key CRUD functions — `app/crud/crawler.py`

| Function | Purpose |
|---|---|
| `claim_crawl_batch(db, tier, batch_size, canton, order_by)` | SKIP LOCKED claim; order_by controls sort |
| `reset_playwright_crawled(db, canton)` | Reset crawled/failed playwright rows to pending for rerun |
| `_CRAWL_ORDER_BY` | Dict mapping order_by string → SQL ORDER BY clause |
| `release_in_progress_states(db, tier)` | Crash recovery — releases stuck in_progress rows |

### Tables

| Table | Purpose |
|---|---|
| `company_url_candidates` | Serper/ScrapingDog URL candidates; one selected per company |
| `company_crawl_state` | Per-company crawl status, tier, bot flags, re-crawl scheduling |
| `company_web_pages` | Per-page crawl result; S3 key for raw HTML; `needs_extraction` flag |
| `company_web_extract` | PK `(company_id, url_candidate_id)` — one row per company+URL crawled; `get_best_web_extract()` picks highest-confidence row for display/scoring. Includes `uid_matches_zefix` (verification flag), `name_address_verified` (migration `0100`, fallback verification when no UID found), and `persons` (impressum management names + spaCy NER names) |

### Key files

| File | Purpose |
|---|---|
| `app/services/crawler_common.py` | Shared utilities: browser profiles + client hints, bot/JS detection, nav + sitemap subpage discovery, media counting |
| `app/services/crawler_sitemap.py` | robots.txt + sitemap.xml discovery (URLs + crawl-delay); best-effort |
| `app/services/crawler_http.py` | httpx crawler (HTTP/2, client hints, curl_cffi impersonation fallback) |
| `app/services/crawler_playwright.py` | Playwright crawler (lazy import; resource-blocking, optional Chrome channel) |
| `app/services/crawler_extract.py` | Deterministic structured-data extractor (trafilatura + regex/schema.org/phonenumbers) |
| `app/services/job_handlers/web_crawl.py` | Job handlers: `_run_crawl_batch` shared loop, `handle_web_extract`, per-type handlers |
| `app/crud/crawler.py` | CRUD: upsert candidates, SKIP LOCKED claiming, save pages, retry backoff, extraction claim/upsert, `get_best_web_extract` |
| `infra/charts/helvex/templates/crawler-http-deployment.yaml` | K8s deployment for HTTP crawler pods |
| `frontend/src/components/website-panel.tsx` | Company-detail "Website" tab: extracted contacts/socials/content + per-page crawl-coverage debug table (POC) |

### S3

Raw HTML stored in `s3_bucket_crawl` under key `crawl/{company_id}/{page_type}.html`. The
`web_extract` job reads these via `s3_client.download_crawl_html`.

### web_extract job

Deduplicated (one active per org). Claims distinct companies with `needs_extraction=TRUE`
pages, downloads each page's HTML from S3, runs `crawler_extract.resolve_company_extract`
(aggregates + dedups signals across pages), upserts one `company_web_extract` row keyed by
`(company_id, url_candidate_id)`, and flips `needs_extraction=FALSE`. When a company is
later crawled via a different URL candidate a second row is written; `get_best_web_extract()`
picks the highest-confidence one for downstream use. Trigger: `POST /api/v1/crawler/extract`
(superadmin) or auto-triggered after each crawl batch. No API cost.
The optional Claude Haiku enrichment layer and multi-candidate comparison UI are deferred — see ROADMAP.

### Extraction logic (`crawler_extract.py`)

`resolve_company_extract(pages, *, company_name, zefix_uid, site_url, company_zip=None, company_city=None)`
aggregates per-page signals into one verification-aware record. All optional third-party
libraries below are imported lazily inside the relevant function with a `try/except ImportError`
fallback to the prior regex-only behaviour — required because this module is imported eagerly
in every process image (app and ml), so it must stay importable where a given package isn't
installed.

- **UID verification** — UID candidates are regex-matched, then validated with `stdnum.ch.uid.is_valid()`
  (checksum) and canonically formatted with `stdnum.ch.uid.format()`; checksum failures are
  dropped instead of accepted as false positives. The validated UID is compared to the Zefix
  UID. Match → near-1.0 confidence (proof the crawl hit the right site); mismatch → penalised
  (≤0.4, likely a wrong Google result); no UID → confidence leans on a **name-match ratio**
  (distinctive company-name tokens present in title/URL/body, after stripping legal-entity
  suffixes via `cleanco.basename()`).
- **Name+address fallback verification** — when no UID was found (`uid_matches is None`) but
  the name-match ratio is effectively exact (`>= 0.999`) and the extracted address contains
  both the company's Zefix zip and city (`_address_matches_company`), `name_address_verified`
  is set True. This feeds its own confidence branch (`deterministic+name_address_verified`,
  confidence up to 0.95) and is a weaker but still solid alternative to UID verification for
  sites that don't publish their UID.
- **Address** — schema.org JSON-LD first, then Microdata/RDFa (via `extruct`, `uniform=True`,
  normalized into the same `{"@type", ...}` shape and merged through `_walk_jsonld` — catches
  older Swiss SME sites that predate JSON-LD), else a Swiss postal-pattern parser over impressum
  text (street + `PLZ City`).
- **Persons** — two passes, merged and deduped: (1) management/contact names following role
  labels (Geschäftsführer / Directeur / Amministratore / CEO …) parsed via regex from
  impressum/about text; (2) spaCy NER (`_extract_persons_ner`, PER/PERSON entities) over the
  same text in the company's detected language, catching names not adjacent to a role label
  (e.g. team-page bios). NER models are loaded once per language and cached
  (`_spacy_ner_cache`); pipeline is trimmed to `{ner, tok2vec, transformer}` only. Runs only on
  `ml-worker` (see Pod topology) since that's the only image with the models bundled.
- **Emails** — ranked best-first: same-domain role mailboxes > same-domain > role > rest;
  free-webmail demoted; each candidate is syntax-validated with
  `email_validator.validate_email(check_deliverability=False)` before ranking. `emails[0]` is
  the primary contact.
- **Keywords** — bigram-preferring miner (multi-word phrases beat single tokens) over
  homepage/about/services main text, expanded multilingual stopwords.
- **Description** — meta/OG first, else first paragraph of trafilatura main text.

Confidence and `extraction_method` (`deterministic` / `+uid_verified` / `+uid_mismatch` /
`+name_address_verified`) feed `get_best_web_extract()` so the best candidate wins automatically.

New dependencies (in `requirements.backend.txt`, shared by `helvex-app` and `helvex-ml` since
`requirements.ml.txt` includes the base file): `extruct`, `cleanco`, `python-stdnum`,
`email-validator` (all pure-Python, safe on both images). spaCy NER models
(`fr_core_news_sm`, `it_core_news_sm`, `en_core_web_sm`, plus the pre-existing
`de_core_news_md`) are downloaded only in `Dockerfile.ml-base` — this is why `web_extract`
job routing was moved to ml-worker only (see Pod topology above).

### web_score adjustment from extraction signals

`scoring.adjust_web_score_for_extraction(base_web_score, *, uid_matches_zefix, name_address_verified)`
(in `app/services/scoring.py`) nudges the Serper-based `web_score` using on-site verification
found during extraction:
- UID found and matches Zefix → **+40** (capped at 100)
- UID found but does not match Zefix → **−50** (floored at 0)
- No UID, but `name_address_verified` → **+20**

`base_web_score` must always be the **raw, un-adjusted** score
(`google_search_results_raw[0]["score"]`), never the currently-stored `company.web_score` —
this keeps repeated re-extraction (e.g. via the `reextract` admin action, which re-runs
extraction without re-crawling) idempotent instead of compounding the adjustment each run.

Wired into `handle_web_extract` (`app/services/job_handlers/web_crawl.py`): after each
successful per-company upsert, `get_best_web_extract()` re-selects the best extract across
all of that company's URL candidates (not necessarily the one just processed), recomputes
`web_score` from the raw Serper score + the best extract's verification signals, and — only
if the value actually changed — updates `companies.web_score` and recomputes
`combined_score` via `Company.compute_combined_score`. Tracked via a `stats["rescored"]`
counter on the job result.

Note: this only adjusts `web_score`, not the `combined_score` formula itself. As of this
change, `Company.compute_combined_score` does **not** use `web_score` in its weighting
(it currently only combines AI score, NOGA confidence, and purpose keywords) — this is a
known drift from the 0.70/0.20/0.10 AI/Web/Flex formula documented elsewhere in this file.
Flagged but intentionally left unchanged pending a separate decision on the broader formula.

### Re-extract without re-crawl

`POST /api/v1/admin/jobs/crawler/reextract` (superadmin) flags every crawled page with stored
S3 HTML (`reset_extraction_flags`) and enqueues `web_extract`. Reprocesses all ~200k sites at
zero crawl cost — the iterate loop for improving the extractor. Button on the crawler admin page.

### Company-detail "Website" tab (POC)

`GET /api/v1/companies/{id}/web-extract` returns the best extract (`get_best_web_extract`)
plus per-page crawl coverage (`company_web_pages`) and the candidate count. Rendered by
`website-panel.tsx` in a new "Website" sub-tab: source strip (URL/confidence/method/date),
contact card (ranked emails with "primary" tag, phones, address, UID with Zefix-match badge),
socials, content card (description/languages/keywords/people), and a crawl-coverage table.
Empty fields render as muted "—" so extraction gaps are visible. The UID field shows a
"name + address verified" badge when no UID was found but `name_address_verified` is true.

The crawler admin page (`/app/admin/crawler`) adds a **field-coverage** card: per-field fill
rates (% of extracted companies with email/phone/uid/address/description/keywords/persons/
socials), UID verified vs mismatch counts, and average confidence — the at-a-glance "what is
the extractor missing at scale" view.

### combined_score formula (web_score wired in)

`compute_relevance_score(company)` in `app/services/scoring.py` now reads `company.web_score` when
available and uses a 4-component formula: `ai×0.50 + web_score×0.20 + noga_confidence×100×0.20 +
keyword_density×100×0.10`. When `web_score` is absent (no Serper result yet) the original
3-component formula `ai×0.60 + noga×100×0.25 + kw×100×0.15` is used. Absent components always
renormalise proportionally so partial data still produces a meaningful score.

`Company.compute_combined_score(ai, noga, kw, web_score=None)` passes `web_score` through to the
above formula. All call sites in `enrich_company_website`, `rescore_from_stored_results`,
`handle_web_extract`, `claude_classify`, and `geocoding_pipeline` now pass `web_score`.

### UID-mismatch candidate auto-quarantine

When `handle_web_extract` finds that the best extract has `uid_matches_zefix = False`, it calls
`reject_url_candidate(db, url_candidate_id)` in `app/crud/crawler.py` to mark the wrong-site
candidate as `rejected`. It then unconditionally triggers a fallback crawl of the next untried
candidate (previously only triggered for low-confidence / no-UID cases). The `quarantined` counter
in job stats tracks how many candidates were auto-rejected per run.

### Review flags UI

`GET /api/v1/admin/crawler/review-flags` returns paginated extract rows where `review_flag IS NOT
NULL`. The crawler admin page shows a "Review flags" table when any flags exist, with links to the
company and the crawled URL so the superadmin can navigate to the Website tab and promote/discard
candidates directly. The KPI row includes a "Flagged for review" card from the `review_flag_count`
field in `/admin/crawler/stats`.

### Multi-candidate extract comparison (Website panel)

`GET /api/v1/companies/{id}/web-extracts` returns all extract rows (one per URL candidate),
ordered by confidence desc. `POST .../promote` switches the selected URL candidate and clears
the `review_flag`. `DELETE .../discard` deletes the extract row and rejects the candidate.

The `WebsitePanel` (`frontend/src/components/website-panel.tsx`) now shows an **"All URL
candidates"** card when more than one extract row exists. Each row shows the URL, confidence,
UID match status, candidate status, review flag (flag icon), and promote/discard action buttons.
The currently-best row is highlighted in blue.

### NOGA fix for Zweigniederlassungen

`reclassify_noga` in `app/services/noga_pipeline.py` now bypasses the `only_missing_noga` guard
for detected branch offices (`is_branch_office(company) == True`). Previously, a branch with a
stale/wrong NOGA code was skipped when `only_missing_noga=True` because it already had a code.
Now branches always re-run `apply_noga_classification`, which inherits the parent's NOGA if
available or clears it if the parent can't be resolved. The `branches_handled` counter tracks
this separately from normal classifications.

### Domain blocklist auto-detection

`get_high_frequency_candidate_domains(db, min_companies, limit)` in `app/crud/crawler.py` runs a
SQL GROUP BY over `company_url_candidates` to find hostnames (www-stripped) appearing for many
distinct companies. These are surfaced in the crawler admin page under **"High-frequency candidate
domains"** with a configurable threshold (default 30). Each row shows the company count, whether
the domain is already in the blocklist (`google_directory_domains`), and a one-click **Block**
button that calls `POST /api/v1/settings/google-directory-domains`.

Admin API: `GET /api/v1/admin/crawler/candidate-domain-stats?min_companies=30&limit=100` —
returns `[{domain, company_count, already_blocked}]`.

### Cross-UID attribution (mismatched UID cross-reference)

During `web_extract`, if `uid_matches_zefix = False` (a UID was found on a crawled page but it
belongs to a different company), `handle_web_extract` looks up `companies WHERE uid = found_uid`.
If the company that owns the UID has no extract or a lower-confidence extract than the current
page, `add_cross_attributed_url_candidate(db, other.id, url)` adds the current page URL as a
`pending` URL candidate for that company (tagged "[cross-UID attribution]"). The current
extract's `review_flag` is set to `"uid_mismatch_cross_ref"` so it is visible during review.

Migration `0103` adds `review_flag TEXT NULL` to `company_web_extract`.

### Branch office skip (Zweigniederlassung)

Zweigniederlassungen (branch offices) don't have independent websites. Two places now exclude them:

1. **`handle_web_url_populate`** — SQL WHERE filters out `legal_form_uid IN ('0108', '0111')` and
   name containing "zweigniederlassung", "succursale", "filiale di". This stops branch offices from
   ever getting URL candidates.

2. **`run_batch_collect`** (`web_enrichment.py`) — ORM filter on the same criteria prevents branch
   offices from being selected for Google/Serper enrichment, saving API quota.

`legal_form_uid` `0108` = branch of Swiss company; `0111` = branch of foreign company.

---

## 17. Activity Log

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

## 19. Company Detail Page (Frontend)

### Overview
Full redesign of `frontend/src/app/[locale]/app/companies/[id]/company-detail-client.tsx` (server component: `page.tsx` fetches company + `is_superadmin` from `/api/v1/auth/me`).

### Layout
- **Sub-tab bar** — Overview (active) · Timeline · People · Documents · Financials; non-Overview tabs disabled until data grows. Sticky at top.
- **Header card** — 56px avatar, H1 name + StatusPill, identity row (UID+copy, legal form, seat, language), action buttons (Share, Registry link, Website link, web-search trigger), **Admin dropdown** (superadmin only, purple), NOGA chip; below a divider: AI summary panel (1.55fr, gradient bg, sparkle eyebrow, `ai_freeform`, source chips) + key facts strip (1fr: Founded/Share capital/Size/Auditor).
- **Body grid** — `1.55fr / 1fr`, gap 18px.
  - **Main column:** `SogcTimelineDB`, `BoardPanel`, `CorporateShareholdersPanel`, `SignersPanelDB`, Corporate structure (head/branch/M&A/audit relations), Purpose (collapsible at 500 chars), Contact, Notes.
  - **Sidebar:** ScoreRing + ScoreBar cards (combined ring, Web/AI/Flex bars), Location (Leaflet map + address), Website card or empty-state + inline "Find website" trigger, Keywords chips (purpose_keywords + tfidf_cluster), Classification (AI category + language), Source coverage 2-col checklist (Zefix/Purpose/NOGA/AI/Website/Geocoded/SOGC/AI score).

### Superadmin dropdown (Admin menu)
- Shown only when `isSuperadmin === true` (passed from server component).
- Purple "Admin" button in header actions; uses `adminMenuRef` + mousedown outside-click listener to close.
- Current items: **Change website** (opens website picker modal), **NOGA explain** (calls `handleNogaTest()`).
- Extensible: add new `<button>` rows inside the dropdown `div` — no structural changes needed.

### Website picker modal
- Full-screen scrim modal (replaces old inline panel below header).
- Shows all `googleResults` (from `google_search_results_raw`) including the current website.
- Each row: score badge + color bar (≥70 green, 40–69 blue, <40 amber), title + URL + snippet, score explanation text (name + location + purpose match), "Use this" button (skipped on current website row).

### Inline atoms (co-located in same file)
- `StatusPill` — green/amber/red by status string.
- `ScoreRing` — SVG donut, color by threshold (≥75 green `#15803d`, 45–74 blue `#2563eb`, <45 amber `#b45309`).
- `ScoreBar` — label + mono value + filled bar.
- `CoverageItem` — ✓ (green) / — (gray) with label.

### Design tokens used
Page bg `#f7f9fc`, card bg white, card border `#e6e8ec`, inner dividers `#eef0f3`, primary blue `#2563eb`, ink `#1f2733`, ink2 `#3f4854`, ink3 `#6b7480`, ink4 `#9aa2ad`. Body grid `1.55fr / 1fr`. Card radius `rounded-2xl` (16px). AI summary panel gradient `linear-gradient(180deg,#f7faff,#fff)` with `#cdddfb` border on NOGA chip.

---

## 18. Guided Search Wizard (Frontend)

### Overview

The `/app/search` blank state (Landing C) provides a guided entry into company discovery for new users, while keeping the search pill as the power-user express lane.

### Files

| File | Purpose |
|---|---|
| `frontend/src/app/[locale]/app/search/search-landing-client.tsx` | Landing C blank state + wizard state management + completion routing |
| `frontend/src/components/guided-wizard.tsx` | 5-step modal wizard (Direction 1 — in-context card) |

### Landing C layout

Rendered when `!hasSearched`. Components top to bottom:
- Brand lockup (HelvexMark + "Helvex")
- Search pill (760px, `1.5px #2563eb` border) — express lane
- Hint line with `<kbd>Enter</kbd>`
- Divider "or let us guide you"
- "What are you looking for?" heading + 3 entry tiles (Companies / People / Jobs)
- "Skip to advanced filters →" link → `/app/companies?view=list`

Clicking a tile → opens wizard at Step 2 (WHERE), with scope pre-answered from tile selection.

### Wizard (GuidedWizard component)

5-step modal over a `rgba(23,32,46,0.34)` scrim. State is local to the component; keyed by `open + scope + step` in the parent so it remounts with fresh state on each launch.

Steps (3 differs by scope — `stepLabels()`/`stepQuestions()`/`stepSubs()` in `guided-wizard.tsx` branch on `state.scope`; step 4 is skipped entirely for `people` since legal form/size don't apply):
1. **What** — Companies / People / Jobs tiles (single-select)
2. **Where** — "All of Switzerland" chip or canton multi-select
3. **Industry** (companies/jobs) — debounced typeahead + hardcoded popular-category chips (NOGA codes), e.g. "Consulting" → `702`
   **Role & Involvement** (people) — role chips (Director/Officer, multi-select) + "companies involved with" min-count chips (Any/1+/3+/5+)
4. **Refine** (companies/jobs only) — legal form chips (single-select) + size segments (display-only, no backend field)
5. **Review** — read-only summary rows; live result count via `fetchCompanies({page_size:1})` for companies/jobs only (skipped for people — not a cheap count here); "See N results →" button

### Filter assembly on completion

Companies/jobs:
```typescript
{ canton, noga_code, legal_form, sort: "-combined_score", page: 1, page_size: 50 }
```
People:
```typescript
{ canton, role_category, min_total_companies, page: 1, page_size: 50 }
```

Completion routing:
- scope = `companies` / `jobs` → `router.push(/${locale}/app/companies?view=list&...)` (fresh page mount reads querystring)
- scope = `people` → stays on `/app/search`; `handleWizardComplete` sets `tab`/`personRoleCategory`/`personMinCompanies`/`hasSearched` local state directly (a `router.push` querystring-only change on the same mounted route does **not** re-sync local `useState`, so state must be set explicitly) and also pushes the querystring for shareable URLs

### People results without a name query

`search-landing-client.tsx`'s people tab originally required a non-empty `submittedQuery` to fetch/show anything — i.e. filter-only searches (role/company-count from the wizard, no name typed) showed nothing. Fixed via `personFiltersActive = !!personRoleCategory || personMinCompanies > 0`; the SWR gate and empty-state copy now treat "query OR active filters" as "has searched".

### NOGA typeahead

Uses the already-cached `filterNogaHierarchy` SWR key (loaded eagerly on blank-state render) + client-side `flattenNoga()` + `matchesNoga()`. No extra API call. Popular chips encode standard NOGA codes (56, 62, 41, 86, 47, 702, 68, 10, 64) — note NOGA codes are undotted digit strings, not dotted decimal notation.

### i18n keys

Added `app.guidedSearch` key to `messages/{en,de,fr,it}.json`. Component uses hardcoded strings for now (same pattern as the rest of `search-landing-client.tsx`).

---

## 19. Common Bug-Fixing Cheatsheet

### NOGA nightly job runs multiple times / fails then retries
`reclassify_noga`'s upfront `COUNT(*)` (used for progress %) ORs an `IS NULL` check with a cross-column comparison (`noga_classified_at < updated_at - interval`), which can't use a btree index and forces a sequential scan. Under load this exceeds the engine-wide 30s `statement_timeout` (`app/database.py`), the job fails, and `_maybe_enqueue_noga_nightly` (`app/main.py`) re-enqueues it within the same 03:00–03:59 window since `has_noga_nightly_run_today` (`app/crud/job_run.py`) deliberately excludes failed/cancelled runs (so a crash doesn't block retry). Fix: `db.execute(text("SET LOCAL statement_timeout = '120000'"))` scoped to just that one COUNT statement in `reclassify_noga()` and `reclassify_low_confidence_noga()` (`app/services/noga_pipeline.py`) — resets to 30s on next commit, doesn't affect interactive API requests.

### Company filter bar: multi-canton / multi-noga-label silently match nothing
`_apply_filters()` in `app/crud/company.py` is the single shared filter builder for `list_companies`/`count_companies`. The guided wizard already comma-joins multi-select values (e.g. `canton="ZH,BE"`), but the canton filter did an exact-equality check against the whole string and noga_label did a single literal `ILIKE`, so any multi-value selection matched zero rows. Fixed by splitting on `,` and using `.in_()` (canton) / `or_(*[...ilike...])` (noga_label). `has_website` was already fully wired end-to-end (route → crud → `lib/api.ts`) — only the filter-bar UI control was missing.

### Header dropdown covered by a page's sticky bar
`NavBar`'s header (`frontend/src/components/nav-bar.tsx`) and a page's own sticky sub-bar (e.g. the company-detail sub-tab bar, `company-detail-client.tsx`) are siblings, not nested — if both use the same `z-*`, they're separate stacking contexts tied at the same level, and CSS resolves ties by DOM order (the later element wins), so a page's sticky bar can paint over a dropdown that visually overflows out of the header above it. Fix: keep in-page sticky bars at a lower z-index than the header (header `z-40` > page sticky bars `z-30`). Also: hover-triggered dropdowns (`group-hover`) must have zero gap between the trigger and the panel (use `pt-N` on the dropdown wrapper, not `mt-N`) — a margin gap is a dead zone where the cursor leaves the hoverable box mid-transit, instantly closing the menu.

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

## 19. Background Job System — Design Evolution

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

**Before:** Clicking "Run" twice created two identical jobs, charged credits twice, and ran redundant work. Nightly schedulers running on multiple pods each independently created jobs, causing duplicate downstream chains.

**After (migration 0093):** Two-layer guarantee:

1. **App layer** — `_compute_dedup_key()` in `job_worker.py` produces a key before inserting. `find_active_by_dedup_key()` returns the existing job if one is active. Credits are not charged again.

2. **DB layer** — partial unique index `ix_job_runs_dedup_active` on `job_runs(dedup_key) WHERE status IN ('queued','running','paused','waiting_external') AND dedup_key IS NOT NULL`. If two pods race past the app-layer check simultaneously, the DB rejects the second INSERT. `_enqueue_job_in_session` catches the `IntegrityError`, rolls back, re-queries the winning pod's row, and returns it.

**Dedup semantics — default is "one active per org per type":**

| Behaviour | Job types |
|---|---|
| **Default** — one active per org | All job types not listed below |
| One active per org + param hash | `claude_classify` (distinct prompt configs may run concurrently) |
| Per-company | `noga_v2_explain` |
| No dedup (parallel-safe) | `batch`, `csv_export`, `web_select_url`, `web_crawl_single` |

New job types are automatically deduplicated without any code change. Add to `NO_DEDUP` only if the type genuinely supports concurrent runs.

**Trade-offs:**
- **Advantage:** Safe to trigger multiple times from UI or multiple pods; no wasted credits or duplicate DB writes.
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

### Person Search API

`GET /api/v1/sogc/persons/search` (`app/api/routes/persons.py`) — filters: `q`, `hometown`, `confidence_level`, `nationality`, `min_active_companies`, `min_total_companies` (distinct companies ever, current+past), `role_category` (comma-separated `director`/`officer`/`other`, matched via `EXISTS` against `sogc_person_appearances`), `is_verified`, `is_current`, `sort_by`.

`PersonEntityOut` includes `total_company_count` (distinct `company_id` count from `sogc_person_appearances`, current+past) and `role_categories` (distinct list). Both are computed per-request in two `GROUP BY person_entity_id` queries scoped to just the current page's `entity_ids` (same batching pattern as the existing `active_count_map`/residence lookups in this endpoint) — no precomputation/preprocessing job, reads straight off the already-imported appearances table, `person_entity_id` is indexed.

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

## 21. SIMAP Public Procurement Import (Jun 2026)

Imports contract award notices from [simap.ch](https://www.simap.ch) — Switzerland's public procurement platform. Enriches company profiles with a high-intent signal: companies that win public contracts are active, financially healthy, and qualified in their domain.

### API

Fully public, no auth. Three endpoints used:

| Endpoint | Purpose |
|---|---|
| `GET /publications/v2/project/project-search` | Paginated award search by date range; multi-value `newestPubTypes` params must be **repeated**, not comma-joined |
| `GET /publications/v1/project/{projectId}/publication-details/{pubId}` | Full award detail: vendors, price, CPV, lot info |
| `GET /vendors/v1/vendor/{vendorId}/public` | Vendor profile → `uidNo` (CHE-xxx.xxx.xxx) for company matching |

**Pagination:** Rolling cursor via `lastItem` (not integer offset). Cursor is stored in `job.stats_json["last_cursor"]` for resume support.

**Language handling:** Text fields are Translation objects with de/fr/it/en slots. Only the `creationLanguage` slot is populated; other slots are null. Company matching is UID-based (not text-based) so this has no impact on matching accuracy.

**Coverage:** ~300–900 award records/month. Swiss vendors have 100% CHE UID coverage; foreign vendors (~24%) are stored with `company_id = NULL`.

### Tables

| Table | Key |
|---|---|
| `simap_awards` | One row per award project (UNIQUE on `simap_project_id`) |
| `simap_award_vendors` | One row per vendor per award; UNIQUE on `(award_id, simap_vendor_id)` |

`SimapAward` stores: project/publication IDs, dates, pub_type, process/project type, multilingual title/authority/description (de/fr/it), CPV code, number of submissions, lot number + title, total price selection + range (for direct awards using a price range rather than per-vendor price).

`SimapAwardVendor` stores: `company_id` FK (nullable), CHE UID, vendor name/country/city, price + currency, rank.

Migration: `alembic/versions/0096_add_simap_awards.py`

### Jobs

| Job type | Params | Description |
|---|---|---|
| `simap_daily` | `date` (optional, default=yesterday), `request_delay` | Import one day of awards; runs nightly at 04:00 Zurich |
| `simap_backfill` | `from_date` (required), `to_date` (optional), `request_delay` | Import full date range; resume support via `last_cursor` |
| `simap_archive` | `from_date` (optional, default=2007-01-01), `to_date` (optional, default=2023-12-31), `request_delay` | Import pre-2024 archive from archiv.simap.ch; see §below |

**CRUD guard:** `has_simap_daily_run_today(db)` prevents double-import.

**Auto-scheduler:** Added `_maybe_enqueue_simap_daily(app)` to the existing nightly scheduler thread in `app/main.py`. Runs at Zurich hour == 4 (04:00–04:59), after SHAB (02:xx) and NOGA (03:xx).

**Dedup key:** `simap_daily:{org_id}` / `simap_backfill:{org_id}` — one active import at a time per org.

### Files

| File | Purpose |
|---|---|
| `app/clients/simap_client.py` | HTTP client for all three SIMAP endpoints; `best_title()` helper (DE→FR→IT→EN fallback) |
| `app/models/simap_award.py` | `SimapAward` ORM model; `best_title()`, `best_proc_office()` methods |
| `app/models/simap_award_vendor.py` | `SimapAwardVendor` ORM model |
| `app/services/simap_import.py` | Core import function: paginates, fetches details + vendor profiles, upserts awards + vendors, matches CHE UIDs; returns stats |
| `app/services/job_handlers/simap.py` | Job handler for `simap_daily`, `simap_backfill`, and `simap_archive`; resume via `last_cursor` in `stats_json` |
| `app/crud/job_run.py` | `has_simap_daily_run_today()` guard (mirrors `has_shab_daily_run_today`) |
| `frontend/src/components/simap-panel.tsx` | Company detail panel: useSWR on `GET /api/v1/companies/{id}/simap-awards`; renders award cards with price, authority, CPV; null-returns if no awards; show-more collapse after 3 |
| `app/clients/simap_archive_client.py` | HTTP client for archiv.simap.ch: `search_archive_awards()` (POST /api/search with `type_cd_ob`, date params) and `get_archive_detail()` (GET /api/detail?meldungsnummer={id}) |
| `app/services/simap_archive_import.py` | Import service for pre-2024 archive: paginates 116k OB02 records, de-dupes by projectid (DE>FR>IT), fuzzy-matches contractor name+zip to companies via pg_trgm; IDs prefixed "arch-" |

## 22. Security Hardening Pass (Jun 2026)

Implemented findings from a security audit. All changes pending user review — see [roadmap.md](roadmap.md).

- **Control-plane access:** scoped `ubuntu` passwordless sudo to a short command allowlist (§14); added `pam_access` restricting SSH/sudo to `admin_cidrs`.
- **Server provisioning:** K3s/Helm/Helmfile installs in cloud-init are now checksum-verified and version-pinned (not `latest`); Hetzner firewall restricts ICMP + egress (§14).
- **Pod hardening:** `readOnlyRootFilesystem: true` + `/tmp` (and frontend `/app/.next/cache`) `emptyDir` mounts on all 5 deployments (§13).
- **Database network exposure:** `pg_hba` restricted from `0.0.0.0/0` to `network.podCidr`; new Postgres-only `NetworkPolicy` (§13) allowlisting app-tier pods, the `cnpg-system` operator, same-cluster replicas, and the node subnet (for kubelet probes).
- **Ingress:** `/docs` (Swagger UI) gated behind `ingress.exposeDocs` (off in prod); Traefik rate-limit `Middleware` added (§13).
- **CI/CD supply chain:** all third-party GitHub Actions pinned to commit SHAs; `aquasecurity/trivy-action` image scanning added to `deploy-prod.yml` (report-only); pipe-to-bash tool installs in `deploy-prod.yml` replaced with checksum-verified downloads (§12).
- **Dependency updates:** `renovate.json` added — patch/digest auto-merge, minor/major and K3s/Postgres/Helm/Helmfile pins require manual review (§12, [runbook.md §25](runbook.md#25-keeping-k3s-and-the-servers-up-to-date)).
- **Not implemented:** Postgres HA (`instances: 2`) — cost/architecture decision requiring explicit sign-off, intentionally left to the user.

Full operational procedures (K3s upgrades, OS patching, Renovate review cadence): [runbook.md §25](runbook.md#25-keeping-k3s-and-the-servers-up-to-date).
| `frontend/src/app/[locale]/app/collection/collection-client.tsx` | Admin trigger sections: "SIMAP Daily Import" + "SIMAP Historical Backfill" |

### API Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/companies/{id}/simap-awards` | Awards where this company was a winning vendor (joined with vendor row for price) |
| `POST` | `/api/v1/jobs/collection/simap-daily` | Trigger daily import (superadmin only) |
| `POST` | `/api/v1/jobs/collection/simap-backfill` | Trigger backfill import with date range (superadmin only) |
| `POST` | `/api/v1/jobs/collection/simap-archive` | Trigger pre-2024 archive import from archiv.simap.ch (superadmin only) |

### Pre-2024 Archive (archiv.simap.ch)

The pre-2024 SIMAP archive at `archiv.simap.ch` uses a different REST API (Vite SPA with `/api` backend) and has **no CHE UIDs** — only contractor name + address. Key differences from the current API:

| Property | Post-2024 (simap.ch) | Pre-2024 (archiv.simap.ch) |
|---|---|---|
| Vendor ID | UUID (`simap_vendor_id`) | None — synthetic `"arch-{pub_id}"` |
| Company matching | Exact CHE UID | Fuzzy: `pg_trgm similarity ≥ 0.50` + exact zip |
| ID type | UUID strings | Integers — prefixed `"arch-{id}"` |
| Volume | ~300–900/month | 116,971 OB02 total (2007–2023) |
| Multi-language | Per-field (title_de/fr/it) | One language per publication — de-dup by projectid (DE>FR>IT) |

**Archive search DTO fields** (discovered from JS bundle `getSearchDTO()`):
- `type_cd_ob`: publication type (OB02 = award notice)
- `stat_tm_1` / `stat_tm_2`: date range (YYYY-MM-DD)
- Page params: `pageNo` (1-based), `recordsPerPage` (max 1000)
