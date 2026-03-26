# Helvex — Architecture Reference

> Internal documentation for bug fixing and onboarding.
> **Stack:** FastAPI · PostgreSQL · Redis · K3s/Hetzner · Helm · Terraform · Next.js
> **Repo:** `zefix_analyzer` (product name: Helvex)

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Directory Layout](#2-directory-layout)
3. [Application Layer (FastAPI)](#3-application-layer-fastapi)
4. [Database Layer](#4-database-layer)
5. [Authentication & Security](#5-authentication--security)
6. [Background Job System](#6-background-job-system)
7. [External Integrations](#7-external-integrations)
8. [Scoring Logic](#8-scoring-logic)
9. [Frontend](#9-frontend)
10. [Configuration & Secrets](#10-configuration--secrets)
11. [Docker Build](#11-docker-build)
12. [CI/CD Pipelines](#12-cicd-pipelines)
13. [Kubernetes / Helm](#13-kubernetes--helm)
14. [Terraform / Hetzner](#14-terraform--hetzner)
15. [Local Development](#15-local-development)
16. [Common Bug-Fixing Cheatsheet](#16-common-bug-fixing-cheatsheet)

---

## 1. Project Overview

Helvex is a B2B company intelligence platform. It bulk-imports the entire Swiss commercial register (~700 k companies via the [Zefix](https://www.zefix.admin.ch) public REST API), enriches them with Google Search results, offline geocoding, TF-IDF clustering, and Claude AI scoring, and exposes them through a filterable dashboard.

**Key workflows:**
1. **Bulk import** — Zefix canton-by-canton, resumable
2. **Detail fetch + geocode** — swisstopo building-level precision
3. **Website enrichment** — Serper.dev Google Search, daily quota-aware
4. **AI scoring** — Claude Haiku via Anthropic API
5. **Dashboard / export** — filter, sort, paginate, CSV export

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
│       └── cluster_pipeline.py # TF-IDF K-Means clustering
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
│   ├── ci.yml                  # Lint + test on every push/PR
│   ├── deploy-dev.yml          # Build + deploy to dev on [deploy-dev]
│   ├── deploy-prod.yml         # Build + deploy to prod on [deploy-prod] or [deploy-app]
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
| GET  | `/api/v1/companies/export/csv` | CSV export (streaming) |

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

Persistent record of every background job.

| Column | Notes |
|---|---|
| `job_type` | bulk / initial / batch / re_geocode / tfidf_cluster / claude_classify / derive_industry |
| `status` | queued → running → paused / completed / cancelled / failed |
| `cancel_requested` / `pause_requested` | Flags polled by the worker at checkpoints |
| `progress_done` / `progress_total` | Resume pointer + UI progress bar |
| `params_json` | Input params as JSON |
| `stats_json` | Output stats as JSON |

#### Other models

| Model | Table | Purpose |
|---|---|---|
| `JobRunEvent` | job_run_events | Per-job structured event log (info/warn/error/debug) |
| `Note` | notes | Free-text notes on a company, by author |
| `AppSetting` | app_settings | Key-value store for dynamic configuration |
| `AuditLog` | audit_logs | User action log |
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

### Rate limiting — `app/auth.py:250-324`

- Backend: Redis `INCR` + `EXPIRE` (if `REDIS_URL` is set), otherwise in-memory `defaultdict`
- Login failures: 10 attempts per IP per 15 min → locked out
- Public endpoints (`/register`, `/forgot-password`): separate per-action counters

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

| Type | Params | What it does |
|---|---|---|
| `bulk` | `canton`, `page_size`, `include_inactive` | Mass-import minimal company records from Zefix canton by canton |
| `initial` | `limit`, `run_google` | Fetch Zefix detail + geocode for companies without lat/lon |
| `batch` | `limit`, `refresh_zefix` | Google Search enrichment, quota-aware |
| `re_geocode` | — | Re-geocode all companies to building-level precision |
| `derive_industry` | `limit` | Re-derive industry field from taxonomy keyword mapping |
| `tfidf_cluster` | `n_clusters`, `limit` | TF-IDF K-Means on purpose text |
| `claude_classify` | `limit`, `system_prompt` | Claude Haiku scoring + categorization |

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

- API key: `ANTHROPIC_API_KEY` (env var) or overridable via `app_settings` key
- Model: Claude Haiku (cheapest; ~$0.25 per 1000 companies)
- Used for: `claude_classify` job type only
- System prompt: user-configurable via Settings API

### SMTP — `app/services/email.py`

- Config: `SMTP_HOST`, `SMTP_PORT` (587), `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`
- Protocol: STARTTLS
- In dev: silently skips if SMTP_HOST is not set
- In prod: required (enforced by `config.py` validator)
- Templates: `send_verification_email`, `send_password_reset_email`, `send_welcome_email`
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

---

## 9. Frontend

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
| `HETZNER_TOKEN` | Terraform / Hetzner Cloud API |
| `GHCR_TOKEN` | GitHub Container Registry push |
| `KUBECONFIG` or `KUBE_CONFIG` | kubectl access for deploy steps |

---

## 11. Docker Build

**File:** `Dockerfile`

Multi-stage Python 3.12 slim build:

1. Install system packages (`gcc`, `libpq-dev`, etc.)
2. `pip install -r requirements.txt`
3. Python 3.12 `ForwardRef._evaluate` compatibility patch (for spaCy/pydantic.v1)
4. Download spaCy German model: `python -m spacy download de_core_news_md`
5. Build geocoding databases:
   - GeoNames PLZ TSV → SQLite
   - swisstopo Amtliches Gebäudeadressverzeichnis zip (~143 MB) → SQLite
6. Copy `app/` source
7. `EXPOSE 8000`
8. `CMD ["sh", "entrypoint.sh"]`

**`entrypoint.sh`** runs:
```bash
alembic upgrade head
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Build args set in CI: `BUILD_DATE`, `BUILD_GIT_SHA` (exposed via `/metadata` endpoint).

---

## 12. CI/CD Pipelines

**Location:** `.github/workflows/`

### `ci.yml` — runs on every push + PR to main

1. Python 3.12 — `ruff` lint, `pytest`, `pip-audit`
2. Node.js 22 — `tsc --noEmit`, `eslint`, `npm run build`

### `deploy-dev.yml` — trigger: `[deploy-dev]` in commit message

1. Build + push backend Docker image to GHCR
2. Build + push frontend Docker image to GHCR
3. SSH to dev K3s cluster via `helvex-dev` self-hosted runner
4. `helmfile apply --environment dev`

### `deploy-prod.yml` — trigger: `[deploy-prod]` or `[deploy-app]` in commit message

1. Build + push Docker images (signed with Cosign)
2. Bootstrap CRDs if `[deploy-prod]` (cert-manager + CloudNativePG CRDs)
3. `kubectl apply` the `helvex-env` secret from GitHub secrets
4. `helmfile apply --environment prod`
5. `kubectl rollout status` (360 s timeout)
6. On failure: dump pod logs, events, describe

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
| `postgres-backup-schedule.yaml` | ScheduledBackup | S3 backups, 7-day retention |
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

## 16. Common Bug-Fixing Cheatsheet

### Email verification not working
- Verify `SMTP_HOST`, `SMTP_FROM`, `SMTP_USER`, `SMTP_PASSWORD` are set in prod
- Check `APP_BASE_URL` — email links point to `{APP_BASE_URL}/verify-email?token=...`
- The `/verify-email` route is in `app/main.py` and is public (no login required)
- Token signed with `SECRET_KEY` — if the key rotates, all outstanding tokens become invalid
- The `/api/v1/auth/verify-email` endpoint returns JSON; the browser-facing `/verify-email` returns HTML

### Job stuck in `running` state
- Indicates the worker pod crashed mid-job (no graceful shutdown)
- `crud.requeue_interrupted_jobs(db)` is called on startup and resets these to `queued`
- Check `job_run_events` table for the last log entry before the crash

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
