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
1. **Bulk import** — Zefix canton-by-canton, resumable. Imports both ACTIVE and CANCELLED/BEING_CANCELLED companies by default (`active_only=False`) — the frontend form and the `POST /collection/bulk` route (`BulkImportBody.active_only: bool = False`) always pass this explicitly, so it's correct end-to-end today. Note: `_run_job`'s dispatcher internally has two disagreeing, currently-unreachable fallback defaults for this field — see §6 job-handler-registry note and `docs/code-review/job-system-deep-dive.md` before relying on either fallback in new code
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
│   └── services/               # Business logic, grouped by domain subpackage
│       ├── ingestion/          # collection, zefix_import, uid_import, incremental_classify
│       ├── registry/           # shab_import, shab_archive_import, simap_*, sogc_* (preprocessor/persons/entity_resolver)
│       ├── enrichment/         # crawler_* (http/playwright/common/sitemap/extract), directory_extract, website_status, web_enrichment, geocoding_pipeline
│       ├── ml/                 # noga, noga_pipeline, noga_lookup, language_detection, embeddings, company_embedding_pipeline, cluster_pipeline, stopword_discovery, boilerplate_analysis, boilerplate_semantic, _pipeline_utils
│       ├── scoring/            # scoring, claude, claude_classify
│       ├── billing/            # credits, tiers, billing_addresses, billing_renewal, payment_transactions, payments/ (worldline provider — transaction + payment-page interfaces)
│       ├── notifications/      # email, saved_view_alerts, activity
│       ├── platform/           # s3_client, csv_export, llm, providers/ (openai/gemini/deepseek/groq)
│       └── jobs/               # job_worker (JOB_HANDLERS dispatch), rate_limit, job_handlers/ (one module per job type)
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
| POST | `/api/v1/jobs/{id}/rerun` | Re-enqueue failed/cancelled job (body: `{mode: "new"\|"continue"}`) |
| GET  | `/api/v1/jobs/stream/active` | SSE stream of active job status |
| POST | `/api/v1/jobs/enqueue/bulk` | Enqueue bulk import |
| POST | `/api/v1/jobs/enqueue/initial` | Enqueue detail fetch + geocode |
| POST | `/api/v1/collection/batch` | Enqueue `web_search_batch` — Serper/ScrapingDog URL search enrichment |
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

**Payment provider:** Worldline (Saferpay) only — Stripe was removed 2026-07.
Worldline's two return-URL webhooks (`worldline_return`, `worldline_card_return`
in `app/api/routes/billing/webhooks.py`) are large — ~300 lines of inline
business logic (token resolution, duplicate-payment guard, VAT computation,
manual transaction upsert, auto-capture) directly in the route handler.
Both are declared as plain `def` (not `async def`) — they do fully synchronous
work (sync `httpx.Client` calls, and `wait_for_alias_registration`'s
`time.sleep`-based retry loop, up to ~10s). An `async def` with no `await`
runs directly on the event loop and blocks *all* concurrent requests
(including `/health`) for its duration; this previously caused a real
incident where a cancelled card-registration return got hit twice back-to-back,
froze the event loop for 45s+, and tripped the liveness/readiness probes into
restarting the pod. `worldline_card_return` also clears the pending alias
token on failure (not just success) so a duplicate/retried return call fails
fast instead of re-running the whole retry storm.

**Entitlement trust model (SECURITY):** the return endpoint is public (payment
redirect), and `decode_worldline_callback_context()` returns `{}` on a bad/absent
signature. Therefore the granted entitlement (tier / credits / kind) is derived
**only** from the pending `PaymentTransaction` created at checkout (server-computed
values, looked up by the Worldline token) or a validly-signed `ctx` — **never** from
the unsigned `order_reference`/`kind` query params. A return with neither a pending
transaction nor a signed `ctx` is refused (no grant). This closes a forge-the-reference
exploit (hold any valid token → forge a higher tier / large credit grant). Regression
tests: `test_worldline_return_ignores_forged_order_reference`,
`test_worldline_return_blocks_grant_without_trusted_context`. **Amount double-check
(defense in depth):** the handler also verifies the Worldline-authorized
`Transaction.Amount` (minor units, CHF) covers the expected price — a material shortfall
or currency mismatch refuses the grant and voids the authorization
(`test_worldline_return_rejects_amount_mismatch`). Payment *status* is verified
server-to-server via the `authorize_transaction` call before any grant.

**Payment methods — two Saferpay interfaces:** the default **Transaction interface**
(`Transaction/Initialize` → `Transaction/Authorize`) only supports cards + direct debit.
Alternative methods (TWINT, PayPal, Apple/Google Pay) require the **Payment Page interface**
(`PaymentPage/Initialize` → `PaymentPage/Assert`), added in `worldline_provider.py`
(`_paymentpage_initialize_request`, `assert_payment_page`). Gated by
`WORLDLINE_PAYMENT_PAGE_ENABLED` (default off) — the only env needed. By default all
terminal-activated methods and all wallets (Apple/Google/Click-to-Pay) are offered; optional
`WORLDLINE_PAYMENT_METHODS` / `WORLDLINE_WALLETS` allowlists can narrow this. When enabled,
`checkout.py` routes **fresh** subscription/top-up payments (no saved alias to charge) through
the Payment Page; saved-alias one-click charges stay on the Transaction interface. Both the
subscription and top-up routes honour an explicit `use_new_card` flag from the client that skips
saved-alias resolution — so choosing "new payment method" in the UI reaches the Payment Page even
when the org already has a card on file (without it, a saved alias would always force the
Transaction interface). The chosen
interface is carried in the signed callback `ctx` (`interface` field); `worldline_return` calls
`assert_payment_page()` vs `authorize_transaction()` accordingly — everything downstream
(alias save, capture, amount check, entitlement grant) is shared because Assert returns the
same result shape. Subscriptions set `Payment.Recurring.Initial=true` in the PP payload so the
resulting `Transaction.Id` feeds the existing `authorize_referenced_transaction` recurring path
(method-agnostic). Note: amount-less card save (`Alias/Insert`) has no Payment Page equivalent
and stays card-only.

**Saved payment methods can be non-card.** Because the Payment Page can register an alias for
TWINT/PayPal/etc. (not just cards), the saved-method record is method-aware:
`_extract_card_info_from_worldline` (`_shared.py`) captures `method_type` (via
`_extract_payment_method_worldline`, with a key-presence fallback for empty marker objects) and
Worldline's `DisplayText` alongside the best-effort card fields (`masked_number`/`brand`/`exp*`,
empty for non-card). These persist in the `card_info_json` blob (no migration) and are surfaced by
`GET /billing/payment-methods`. The frontend renders them via a shared helper
`frontend/src/lib/payment-method-display.tsx` (`paymentMethodIcon`, `paymentMethodLabel`) on both
the billing page (`CardChip`) and the checkout selector — cards show `brand •••• last4`, non-card
methods show `DisplayText` with a generic wallet icon. Legacy rows without `method_type` default to
`"card"` when a masked number is present. Billing copy/i18n is method-neutral ("payment method",
key `paymentMethods.savedMethod` across en/de/fr/it).

**Charging a non-card alias:** Saferpay cannot charge a saved TWINT/PayPal alias through the
Transaction interface — `Transaction/Initialize` with such an alias returns `402
ACTION_NOT_SUPPORTED`. So both checkout routes look up a resolved alias's `method_type`
(`_resolve_alias_method_type`) and, when it is non-card, **drop the alias and route to the
Payment Page** (where the method can be re-selected). If the Payment Page flag is off, checkout
returns `503` with a clear message rather than a provider 402. Amount-less alias *registration*
(`Alias/Insert`, the standalone "add payment method" flow) remains card-only — Saferpay has no
amount-less registration for TWINT/PayPal; those are saved during an actual Payment Page payment
with "save this payment method" checked.

**Invoice issuer details** (`GET /billing/payments/{id}/invoice`) come from `Settings`
(`invoice_brand_name`, `invoice_company_name`, `invoice_company_address`, `invoice_vat_id`,
`invoice_support_email`), overridable via env — not hardcoded. The invoice number is
`INV-{YYYYMMDD}-{sha1(provider_ref)[:6]}` (date-based, non-sequential — can't be used to infer
transaction volume).

For the Saferpay
API request/response contract, see
[`docs/payment-flows.md`](docs/payment-flows.md); for the code-structure risk
picture (including a concrete lead on the "subscription upgrade doesn't
work" bug in the Bug Fixes list below), see
[`docs/code-review/billing-worldline-deep-dive.md`](docs/code-review/billing-worldline-deep-dive.md).

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
| POST | `/api/v1/admin/jobs/crawler/recompute-website-status` | Superadmin | Enqueue `recompute_website_status` — recompute the company website verdict + multi-site count from extracts (no API/crawl cost) |
| POST | `/api/v1/admin/jobs/crawler/directory-crawl` | Superadmin | Enqueue `directory_crawl` — fetch profile pages from business directories (moneyhouse.ch, local.ch, northdata.com, etc.) and store in `company_directory_data` |
| GET | `/api/v1/companies/{id}/web-extract` | Authenticated | Best web extract + per-page crawl coverage for the company detail "Website" tab |
| GET | `/api/v1/companies/{id}/serp-analysis` | Authenticated | SERP snapshot from stored data: organic rank, ads, local pack, competitors above, `seo_visibility_score`. Write-through: persists `seo_visibility_score` + `seo_visibility_computed_at` if value changed — no new API calls |
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
| `website_url` | String | Best **own-domain** match — gated by the website verdict (NULL for social_only/directory_only/none; no longer a forced top result) |
| `web_score` | Integer 0-100 | Google name/location match quality (URL-selection confidence, not visibility) |
| `seo_visibility_score` | Integer 0-100 | Organic search visibility: rank discounted by ads (-12 each) + SERP features (-5 each). NULL until computed. |
| `seo_visibility_computed_at` | DateTime | Timestamp of last `seo_visibility_score` computation |
| `website_status` | String(16) | Company-level website verdict: `verified` / `confirmed` / `likely` / `social_only` / `directory_only` / `none` (NULL = unknown). See §16. |
| `website_count` | Integer | Number of distinct genuine websites detected (≥2 ⇒ company has multiple sites) |
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

**Dual-write note (Google scoring fields):** `website_url`, `web_score`, `google_search_results_raw`, `website_checked_at`, `social_media_only`, `website_status`, and `website_count` exist on both `Company` (global Serper master) and `OrgCompanyState` (org-specific re-score). The `OrgCompanyState` docstring says to "always read from the overlay when `org_id` is available" — but ⚠️ **this is aspirational, not the current reality**: `_overlay()` reads these scores straight off `Company`, and the intended per-org sink `update_org_google_results()` has **no callers** (the `OrgCompanyState` web-score columns are dead shadow columns). Org-scoped enrichment/crawl jobs therefore write scores onto the *global* `Company` row, which every other org then reads — a data-integrity leak. This whole layering is being reworked so that scores become strictly per-scope; see the approved design in [`docs/code-review/scoring-multitenancy-rework.md`](docs/code-review/scoring-multitenancy-rework.md). Until that lands, treat these fields as effectively global.

**Note:** `review_status`, `contact_status`, `contact_name/email/phone`, and `tags` also exist as legacy columns on `Company`, but the authoritative per-org values live in `OrgCompanyState`. Do not write these fields directly on `Company` for new code.

#### Tenancy-overlay pattern (house standard)

When an org or user needs their own version of shared/base data, follow this shape
everywhere (workflow state, scores, AI, per-scope settings) — the `OrgCompanyState`
drift above is what happens when it's *not* followed consistently.

- **Layer by write authority, not by content.** Base/global row is written by one
  system pipeline (ingest/crawl) and read by everyone. Each scope gets a **separate
  overlay table keyed `(scope_id, base_id)`** — never tenant columns on the base table.
- **The invariant:** *a scoped write must never touch the base row.* Most multi-tenant
  data bugs here are violations of this (e.g. an org-scoped crawl writing `Company.web_score`).
- **Facts vs. derived:** extract expensive facts once, globally (shared); make derived
  values (scores, formatting) a pure function of `facts × scope-config`, computed/materialized
  per scope. This is why re-crawling per org is unnecessary.
- **Sparse vs. dense overlay:** sparse (row only when the scope touched the entity) for
  annotations/overrides; **dense** (a row per base entity per scope, materialized by a job)
  only when you must `ORDER BY`/`WHERE` on the scoped value across the full population.
- **Precedence:** stack as `COALESCE(user, org, global)`, but resolve the scope to a single
  key at request time so reads stay one indexed join with one predicate (no per-row COALESCE).
- **Evolving attributes:** JSONB for the churning long tail + promoted real columns only for
  the few fields you index/sort on. Avoid EAV.

Full worked example + the in-flight migration of scores/AI to this model:
[`docs/code-review/scoring-multitenancy-rework.md`](docs/code-review/scoring-multitenancy-rework.md).

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
| `job_type` | bulk / initial / web_search_batch / re_geocode / tfidf_cluster / claude_classify / derive_industry / csv_export |
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

Instrumented in: `app/services/enrichment/web_enrichment.py` (per-company Google search failures), `app/services/ingestion/zefix_import.py` (per-UID import failures).

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
| **Session cookie** | `itsdangerous` URLSafeTimedSerializer, httpOnly, samesite=strict, secure on HTTPS, **7 d idle sliding** | Browser / HTML UI (the Next.js SPA uses this exclusively) |
| **JWT Bearer token** | PyJWT HS256, same `SECRET_KEY`, 8 h expiry | Self-service `POST /auth/token` — **disabled by default** (`enable_password_token_endpoint`, 404 when off); the frontend never used it. Bearer verification in `_auth_info_from_request` stays dormant for the future API-key/OAuth lanes |

Both are checked by `_user_id_from_request()` in `app/auth.py`.

**Sliding session:** the cookie has a 7-day absolute lifetime (`_SESSION_MAX_AGE`) and is re-issued whenever a request arrives while it is older than `_SESSION_RENEW_AFTER` (1 day). Renewal happens in the `auth_gate` middleware via `session_user_needing_refresh()` + `set_session_cookie()` (samesite=lax on renewal so it also covers OAuth-initiated sessions; origin_gate enforces same-origin on API). Active users therefore never re-enter credentials; only ~7 d of true inactivity forces a re-login. Bearer/JWT requests are never slid. Login sites (`/login`, OAuth `_set_session`) share the same `set_session_cookie` helper so cookie max-age lives in one place.

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

### Rate limiting — `app/auth.py` + `app/services/jobs/rate_limit.py`

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
in `app/services/jobs/rate_limit.py` is the centralized function; routes call it directly.

**Coarse per-user/org request ceiling (anti-scraping):** beyond the per-action limits above, `auth_gate` applies a blanket cap on authenticated `/api/*` requests via `check_request_rate()` in `app/auth.py` — 240 req/min per user; the per-org cap **scales with membership** (`members × 150/min`, floored at the per-user cap) so a large team is not throttled by a flat number. Superadmin-exempt; toggle `api_rate_limit_enabled` (disabled in tests). The user's `(is_superadmin, org_id, active-throttle, org_cap)` is resolved through a ~60s metadata cache to keep the hot path DB-free.

**Anomaly detection + auto-throttle:** `note_api_access()` tracks per-user script-like access (missing `Sec-Fetch-Site`) and sustained volume in-memory; on a breach it writes a durable `security_events` row (migration 0115) with a 1 h `throttle_until` and logs an alert. `check_request_rate` then honours the throttle cross-pod (via the metadata cache), dropping the flagged user's per-user cap to 30/min. Policy is **record + alert + auto-throttle — never auto-suspend** (a false positive must not lock out a user). CRUD: `record_security_event` / `get_active_throttle_until` / `list_recent_security_events`.

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
- One long-lived daemon loop (`_job_worker_loop`, `app/services/jobs/job_worker.py`) claims `job_runs` rows and runs them in a `ThreadPoolExecutor` sized by `JOB_WORKER_CONCURRENCY`. Same loop shape at every concurrency level.
- The loop never exits when idle; it parks on `_wake_event`, which `kick_job_worker()` sets so a local enqueue starts work immediately instead of waiting out `JOB_POLL_INTERVAL` (5 s).
- Thread startup is guarded by `_worker_lock` + an `is_alive()` check, so concurrent `kick_job_worker()` calls (routes, schedulers, `enqueue_job`) cannot start two competing loops.
- Progress is persisted to the `job_runs` row only — there is no in-process mirror (the old `app.state.collection_task`) and no pub/sub fan-out. The UI reads progress from the SSE poller in `app/api/routes/jobs.py`.
- Set `DISABLE_JOB_WORKER=true` to suppress the thread (e.g., API-only pod)

**Stale-job recovery**
- `_run_recovery_sweep()` runs on a timer (`_STALE_JOB_RECOVERY_INTERVAL`, 180 s) at the top of every loop iteration — independent of queue depth. It previously sat inside the "queue is empty" branch, so it never ran on pods without a `JOB_TYPE_WHITELIST`, and a sustained backlog starved it on the others.
- `requeue_interrupted_jobs()` re-queues `running` jobs whose heartbeat is older than 300 s. `_HEARTBEAT_INTERVAL` (30 s) and that threshold are a **pair** — change them together.

**Graceful shutdown**
- `request_shutdown()` sets `_shutdown_event`, wakes the parked poller, then **blocks on `_jobs_drained`** until in-flight jobs hit a checkpoint and persist themselves as `paused` (`pause_reason='shutdown'`). It formerly set a flag and returned, so uvicorn killed jobs mid-batch and they stayed `running` until the recovery sweep noticed.
- `_shutdown_event` is an `Event` so `reset_shutdown()` (lifespan startup) can clear it; as a write-once bool, one shutdown poisoned the process and every later job paused immediately.
- Worker deployments set `terminationGracePeriodSeconds: 60` to fit the drain (`JOB_SHUTDOWN_JOIN_TIMEOUT`, default 25 s) before SIGKILL.

**Concurrency knobs multiply**
- In-flight work per pod = `JOB_WORKER_CONCURRENCY × the job's own fan-out`. `web_crawl_http` defaults to `crawl_concurrency=40` (async httpx); `web_crawl_playwright` to `2`, each slot a full Chromium. Page processing is bounded separately and per-pod by `CRAWL_PAGE_WORKERS` (default 32) — see "Off-loop page processing".
- Crawl throughput is therefore tuned via `crawl_concurrency`, **not** by raising the worker's concurrency. Raise `JOB_WORKER_CONCURRENCY` to stop long jobs blocking short ones on the same pod (crawler-http also carries the interactive `web_crawl_single`), and re-check the pod memory limit against the product. Current: `crawlerHttpWorker.concurrency: 2`, `mlWorker.concurrency: 1`, `apiWorker.concurrency: 2`.

**Job handler registry pattern — single dispatch, no inline branches**
- Every job type is a dedicated handler in `app/services/jobs/job_handlers/{type}.py`, registered in `JOB_HANDLERS` (`app/services/jobs/job_handlers/__init__.py`)
- `_run_job()` (`app/services/jobs/job_worker.py`) dispatches solely via `JOB_HANDLERS[job_type](ctx)`, passing a `JobContext` (DB, `job`, `params`, `resume_from`, `app`, and helper methods `assert_not_cancelled`, `progress`/`progress_no_event`, `event`, `status`/`status_with_stats`, `enqueue_job`). Unknown job types raise `RuntimeError`. (`sync()` and `_heartbeat()` were removed — both fed the deleted in-process state mirror and had no-op bodies.)
- Handlers return `(stats_dict, done_message)` or raise typed exceptions (`JobPausedError`, `JobCancelledError`, `JobWaitingExternalSignal`)
- The legacy inline `elif job.job_type == "...":` chain (~29 branches, ~900 lines) was **removed** — its logic already lived, near-verbatim, in the registry handlers. Two gaps closed during removal: (1) the `shab_daily` auto-chain that enqueues `sogc_preprocess`+`extract_sogc_persons` after a productive import was ported into `job_handlers/shab.py::handle_shab` (uses `ctx.enqueue_job`, which routes through the same `_enqueue_job_in_session` so the dedup key still guards against duplicate chained jobs); (2) `embed_purpose_full`/`embed_purpose_clean` gained handlers in `job_handlers/noga.py` and registry entries.
- Shared post-completion logic stays in `_run_job` and is dispatch-agnostic: `mark_completed`, warning/error event fan-out, and `_maybe_send_job_notification`. (There is no longer any taxonomy/category cache to invalidate — those stats are computed live per request; see `get_taxonomy_stats`/`get_category_stats` in `app/crud/company.py`.)

**Atomic job claiming (multi-pod safety)**
- `crud.claim_next_job(db, job_type_whitelist=…, job_type_blacklist=…)` selects **and** claims in one statement:
  ```sql
  UPDATE job_runs SET status='running', started_at=now(), last_heartbeat_at=now(), …
  WHERE id = (SELECT id FROM job_runs
              WHERE status='queued' AND NOT cancel_requested AND <type filter>
              ORDER BY queued_at LIMIT 1 FOR UPDATE SKIP LOCKED)
  RETURNING id
  ```
  A claimed row can never be handed to a second caller, in this pod or another. `_run_job()` receives an already-claimed id and does no claiming of its own.
- **Do not split this back into a peek and a claim.** The former `get_next_queued_job(skip_locked=True)` + `atomic_claim_job()` pair released its row lock when the select's session closed — so `SKIP LOCKED` protected nothing and every losing pod burned a round trip. Locally it was worse: a job submitted to the pool stayed `queued` until its thread started, so the next poll re-drew it, tripped the in-flight guard and aborted slot filling — one slot per 5 s poll, meaning concurrency N took ~5·N seconds to reach.
- `NOT cancel_requested` sits in the `WHERE` rather than being cleared in the `SET`: clearing it unconditionally could swallow a cancel that arrived while a job was pausing for shutdown and was then re-queued by recovery.
- `get_next_queued_job()` remains as a non-claiming peek for diagnostics and tests only.

**Cheap cancel/pause checkpoints**
- `_assert_not_cancelled()` checks the local shutdown flag instantly, and polls `status`/`cancel_requested`/`pause_requested` at most every `_FLAG_POLL_INTERVAL` (2 s) via `crud.get_job_flags()` on a **short-lived session**.
- It previously did `db.refresh(job)` on the handler's own session — one full-row SELECT per company on a 700k-row job, and an autoflush of the handler's pending state at an arbitrary point mid-batch.

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
| `web_search_batch` | `limit`, `refresh_zefix` | Serper/ScrapingDog URL search enrichment (formerly `batch`); dedup'd to one global instance — its query has no per-row claiming, so concurrent runs would double-process companies | — |
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
| `web_crawl_content` | `max_pages`, `max_depth`, `batch_size`, `canton`, `order_by`, `limit`, `crawl_concurrency`, `rerun` | Phase B — breadth-first crawl of the whole website for identity-confirmed companies; claims `crawl_phase='content'`. Auto-enqueued by `web_extract`. See §Two-phase crawling | — |

#### `web_search_batch` concurrency safety (`run_batch_collect`, `web_enrichment.py`)

`concurrency` (parallel Serper/ScrapingDog requests via `ThreadPoolExecutor`) is
clamped server-side to 1–100 (`app/api/routes/jobs.py`'s `BatchCollectBody` UI hint,
enforced in `run_batch_collect` itself) — added after a high-concurrency run against
a ScrapingDog plan that doesn't support that many concurrent connections caused
cascading `HTTP 400` failures that, combined with the two issues below, could starve
the pod (job worker runs in-process by default; see `apiWorker` in `infra/charts/helvex/values.yaml`, disabled by default):

- **Circuit breaker:** tracks the last 20 Google-search outcomes (both the
  concurrent and sequential paths); if ≥15 of the last 20 failed, the batch stops
  early instead of grinding through the rest of the selection at full speed.
  Recorded in `stats["circuit_breaker_tripped"]` + a `stats["warnings"]` entry.
- **Throttled progress writes:** `progress_cb` (→ `update_progress` → full
  `json.dumps(stats)` + `db.commit()` on the `job_runs` row) previously fired once
  per company; now at most once/second (always fires on the final item or when the
  breaker trips), avoiding O(n²) DB writes on a long, fast-failing run.
- **Capped error list:** `stats["errors"]` keeps at most the last 200 entries;
  `stats["error_count"]` tracks the true total separately.

#### Provider backoff after a circuit-breaker trip

The per-run circuit breaker (above) stops a single `run_batch_collect` call early,
but a real prod incident showed that's not enough on its own: `web_search_batch` is
dedup'd to one global instance, so the instant a circuit-breaker-tripped run
completes, a fresh run can start immediately — and did, repeatedly, each burning
through ~650 requests against an already-struggling ScrapingDog before tripping
again. Root-caused to the provider itself intermittently returning `HTTP 503
Service Unavailable` alongside the earlier `400`s — a provider-side outage, not
purely a concurrency-limit issue.

Fix: `_trigger_provider_backoff(db, provider)` (`web_enrichment.py`) is called
from both circuit-breaker-trip sites in `run_batch_collect` and writes an
AppSetting (`google_search_backoff_until_{provider}`, ISO timestamp, default 10
minutes out — `_DEFAULT_BACKOFF_MINUTES`). `_google_search_ready()` — already
called at the top of `run_batch_collect` to gate `run_google` on a configured API
key — now also checks this: if the cooldown hasn't elapsed, Google enrichment is
skipped entirely for the whole run with a `stats["warnings"]` entry naming the
remaining minutes, regardless of what re-triggered the job. This is deliberately
persisted (AppSetting, not in-process state) so it holds across separate job runs
and even pod restarts. Regression test:
`test_run_batch_collect_triggers_provider_backoff_on_circuit_break`.

#### `google_pending_crawl` — distinguishing "no result" from "not crawled yet"

Also found while investigating the same incident: `run_batch_collect`'s
`google_no_result` counter was conflating two very different outcomes —
"the provider genuinely returned nothing" and "the provider found a real
candidate, it just isn't crawl-confirmed yet" (expected for nearly every fresh
search since the phase-3 crawl-only verdict — see "Website verdict" below). This
made a healthy batch (searches succeeding, `company_search_results` genuinely
populated) look like it was failing to find anything.

`enrich_company_website()` now returns a 3-tuple: `(verdict_positive, website_url,
had_results)` — `had_results` is `True` whenever the provider returned actual hits,
independent of the crawl-gated verdict. All three callers that unpack it
(`_enrich_one_concurrent`, `run_batch_collect`'s sequential path, `initial_collect`
in `zefix_import.py`) now bucket into a new `stats["google_pending_crawl"]` when
`had_results` is true but the verdict isn't, instead of lumping it into
`google_no_result`. `detail.py`'s manual single-company route doesn't unpack the
return value, so it's unaffected. Regression test:
`test_run_batch_collect_pending_crawl_distinct_from_no_result`.

#### `run_batch_collect` no longer loads the selection one row at a time

Found while investigating a real stuck prod job (100002 selected, still showing
"Processing 3/100002" after a long run, ScrapingDog dashboard showing healthy
concurrent 200s the whole time — so the provider side was fine). The culprit:
`companies = [db.get(Company, cid) for cid in company_ids]` — one individual
round-trip per selected company. For a `limit` in the tens/hundreds of thousands
(nothing caps it), that's tens of thousands of sequential DB queries *before any
search request goes out*, during which nothing calls `progress_cb` (so no
heartbeat, no progress update, no cancellation checkpoint — `ctx.assert_not_
cancelled()` only runs inside the `_progress` callback). Looks exactly like a
hung job from the outside. Also a straight violation of the project's own
"never load the companies table unbatched" rule.

Fixed: `company_ids` are now processed in fixed-size chunks (`_BATCH_LOAD_CHUNK
= 1000`, module-level so tests can shrink it), each chunk loaded via one batched
`db.query(Company).filter(Company.id.in_(chunk_ids))` instead of N individual
`db.get()` calls — down from up to 100,000 round-trips to ~100 for that job. The
circuit breaker, throttled progress writes, and `stats["error_count"]`/
`stats["errors"]` accounting are unchanged, now just scoped across chunks via a
`done_total` counter instead of per-call `done`/`i`. `abort_cb()` (if provided)
is also now checked once per chunk, on top of the existing throttled-progress
cancellation checkpoint. Regression test:
`test_run_batch_collect_processes_all_companies_across_chunks`
(`tests/test_collection_batch.py`) — shrinks `_BATCH_LOAD_CHUNK` to 3 and proves
all 10 test companies get reached, not just the first chunk.

#### Cancelling `web_search_batch` with `concurrency > 1` used to hang (2026-07-29)

Reported symptom: stopping a Serper/ScrapingDog batch job left it stuck — job
stayed "running", no new progress, never flipped to "cancelled". Root cause in
`run_batch_collect`'s concurrent branch (`use_concurrent = concurrency > 1`):
`futures = {pool.submit(...): c for c in companies}` submits the *entire*
chunk (up to `_BATCH_LOAD_CHUNK = 1000` companies) to the `ThreadPoolExecutor`
up front. Cancellation is detected via the throttled `_maybe_progress` →
`progress_cb` → `ctx.assert_not_cancelled()`, which raises `JobCancelledError`
straight out of the `for future in as_completed(futures)` loop. Exiting the
`with ThreadPoolExecutor(...)` block on that exception still runs the default
`__exit__`, i.e. `pool.shutdown(wait=True)` — which blocks until *every*
already-submitted future finishes, not just the in-flight one(s). With
`concurrency=1` that means every remaining company in the chunk still gets its
Serper call run, one at a time, before the exception can propagate — looking
exactly like a hang from the outside. The circuit-breaker path already avoided
this (`for f in futures: f.cancel()` before breaking); the
cancellation-via-exception path never got the same treatment. Fixed: the
`as_completed` loop is now wrapped in `try/except BaseException` that cancels
every not-yet-started future before re-raising, so shutdown only has to wait
for the currently-running task(s).

#### `enrich_company_website` no longer swallows provider failures

Found while debugging the above: `enrich_company_website` (`web_enrichment.py`) used
to catch *any* exception from the Serper/ScrapingDog client — network errors, HTTP
4xx/5xx, a missing API key — log one `logger.error(...)` line, and return `(False,
None)` as if it were a normal "no result" search. That meant:

- The circuit breaker above never actually tripped in practice — nothing that calls
  `enrich_company_website` (`run_batch_collect`'s concurrent/sequential paths,
  `initial_collect`, the manual `GET /companies/{id}/google-search` route) ever saw
  an exception to react to, so a provider erroring on every request (e.g. a
  ScrapingDog plan rejecting concurrent connections) would burn through the entire
  batch — and the provider's request quota/credits — with zero visibility beyond a
  raw Python log line the job UI never surfaces.
- The manual web-search route's credit-refund logic (`detail.py`'s
  `google_search_for_company` → `_refund_failed_search`) was dead code for this exact
  failure mode: it only refunds on a caught exception, which never arrived.

Now `enrich_company_website` logs a `company_errors` row (source `web_enrichment`,
error_type `search_api_failed`) with the **exact request** (`provider`, `q`, `gl`,
`hl`, `location`, `purpose_language_raw`, `municipality`, `address_zip`) plus
`status_code` and `duration_ms` in `detail_json`, then **re-raises**. This is the way
to inspect what was actually sent to ScrapingDog/Serper for a failing company:
superadmin → `/admin/errors` → filter source "Web enrichment" → expand a row → the
`Detail` panel is the raw request JSON, `Message` has the provider's response.
Callers now correctly see the exception:
- `run_batch_collect`: circuit breaker + `stats["errors"]` actually populate (no
  longer double-logs to `company_errors` itself — would have overwritten the richer
  detail via `log_error`'s per-company-id/source dedup).
- `detail.py`'s manual route: the existing refund-on-failure path now actually fires.
- `initial_collect` (`zefix_import.py`): falls into its existing per-UID
  `except Exception` handler and gets counted in `stats["errors"]` instead of
  `google_no_result`.

`company_search_results` is deliberately left untouched on this path (unlike the
"zero results" case, which does persist an empty row) so a transiently-failed
company is retried on the next batch run rather than marked as permanently searched.
Regression test: `test_run_batch_collect_circuit_breaker_on_provider_failures`
(`tests/test_collection_batch.py`).

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

#### Saved view alert sweep — `app/services/notifications/saved_view_alerts.py`

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

### Claude (Anthropic) — `app/services/ingestion/collection.py` + `app/crud/app_setting.py`

- **API key**: Resolved per-org via `get_effective_setting(db, "anthropic_api_key", org_id=...)`
  - Falls back to global `ANTHROPIC_API_KEY` env var if no org override
  - Never exposed in APIs (replaced with `anthropic_api_key_set: bool` in frontend)
- **Model**: Configurable via `claude_model` key in `app_settings` (default: `claude-haiku-4-5-20251001`). Read at runtime by `get_claude_default_model()` in `app/services/scoring/claude.py`. Changeable in Settings → LLM tab without a restart. Valid values: `claude-haiku-4-5-20251001`, `claude-sonnet-4-6`, `claude-opus-4-6`.
- **Used for**: `claude_classify` batch job, and `claude-preview` dry-run endpoint
- **System prompt**: User-configurable via Settings API; resolved per-org
- **Preflight check** (`_preflight_job`): Validates org has API key before queueing `claude_classify`
- **Dry-run / preview**: `claude_classify_batch(dry_run=True)` scores up to 5 companies without writing to DB. Called by `POST /api/v1/scoring/claude-preview`. Rate-limited to 3 calls/min per org (in-memory sliding window).

### Hetzner Object Storage (S3-compatible) — `app/services/platform/s3_client.py`

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

### SOGC Publication Preprocessing — `app/services/registry/sogc_preprocessor.py`

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
- `run_sogc_preprocess_batch(db, mode, uids, ...)` — batch over companies table with cursor pagination; `mode="missing"` skips already-processed companies, `mode="all"` reprocesses everything. The `mode="missing"` progress-count (informational only, not used by the pagination loop) bumps `statement_timeout` to 120s for its NOT EXISTS anti-join and falls back to a `pg_class.reltuples` estimate on timeout, so a slow count never fails the whole job.
- `run_sogc_publications_backfill(db, ...)` — iterates existing `sogc_publications` rows, re-applies encoding fix to stored texts, regenerates `sogc_changes`. Used via `mode="publications"` job param to retroactively fix rows written before the encoding/text-extraction fixes.

**SHAB integration (Zefix-backed):** After every HR01/HR02 company upsert in `shab_import.py`, `preprocess_company_sogc_pub` is called fire-and-forget (errors logged, not raised), ensuring new publications are indexed immediately.

### UID Register Gap Import — `app/services/ingestion/uid_import.py`

Discovers the companies **not in Zefix** (the "gap": sole traders, MWST/VAT-only, `uid_only`) from the Swiss UID register via the V5.0 PublicServices SOAP API, using a **name/keyword dictionary sweep with geo/status refinement**.

**API endpoint:** `https://www.uid-wse.admin.ch/V5.0/PublicServices.svc` (BFS spec v5.0; **not** `uid.admin.ch/uid-wse/` which is a web portal).
**WSDL:** `https://www.uid-wse.admin.ch/V5.0/PublicServices.svc?wsdl`

**V5.0 Search semantics (empirically established 2026-06; the old "contains" assumption was wrong):**
- `organisationName` is **exact word-token AND** matching (tokenize by spaces, match each word exactly, case-insensitive, accent-folded — e.g. `MULLER` matches `Müller`). Multiple tokens are AND-ed. NOT a SQL `LIKE`/contains. Min token length ~2.
- Max **30 results per call** — no pagination token.
- Server-side filters (all confirmed working): `address.swissZipCode` (PLZ — pass as `{"_value_1": [{"swissZipCode": <int>}]}`), `commercialRegisterInformation.commercialRegisterStatus` (`"3"`=not in HR, `"2"`=in HR), `vatRegisterInformation.vatStatus` (`"2"`=MWST), `legalForm` (list of eCH-0097 codes). `personName` is silently ignored.
- **No filter lifts the 30-cap** — the non-HR gap is dense (>30 per PLZ even in villages), and there is **no searchable date** covering non-HR entities (so no dictionary-free bisection).
- Rate limit: `Request_limit_exceeded`; client retries with exponential backoff (`_RATE_LIMIT_BASE_DELAY=60s`, `_INTER_CALL_DELAY=2s`).

**Sweep strategy (`sweep_uid_gap`):**
- **Discovery is name-driven** — a company is only found if we query a word in its name. So the OUTER LOOP is a dictionary of name tokens/keywords built from all `source<>'uid'` company names (`build_dictionary`, one chunked keyset-paginated pass; also returns a `canton→[postcodes]` map). Freq-sorted; built from non-UID rows so term order is **stable across resumes**.
- For each term: `search_entities(name=term, not_in_hr=True[, mwst_only])` nationwide. When a bucket caps at 30, `_collect_token` refines to break the cap: **canton (26, coarse/cheap) → postcode (within a capped canton) → MWST subset → AND a 2nd top-token** (`max_depth`). Canton-first keeps a common surname from fanning out to all ~4,150 postcodes at once.
- A single `seen` set (whole run) dedups the heavy cross-term overlap (multi-word names match several terms). Resume via `resume_from` = index into the freq-sorted dictionary; upsert is idempotent so re-runs after resume are safe.
- **Completeness** bounded by the dictionary: a company whose name shares no token with any Zefix name is unreachable (most sole traders are "Firstname Surname …" and surnames recur, so coverage is high). Unmeasurable from the API (no count) — validate by external recall sampling. Tune `max_terms` / `min_token_freq`.

**Why not PLZ-outer:** an earlier iteration looped postcodes and refined by names drawn from companies *already in that postcode* — circular for discovery (it re-finds known names). Discovery must be driven by a comprehensive name dictionary; geography is only a cap-breaking refinement.

**Job params:** `batch_size`, `not_in_hr` (default true), `mwst_only`, `refine_mwst` (default true), `max_depth` (default 1), `min_token_freq` (default 2), `max_terms` (default 50000), `second_token_count` (default 200). resume = dictionary index.

**Upsert behaviour (`_upsert_batch`, `on_conflict` param — Zefix is NEVER overwritten in either mode):**
- New rows: inserted `source=uid`; no purpose → skipped by NOGA, scoring, and Claude classification.
- `on_conflict="update"` (default): refresh existing `source='uid'` rows only — `registration_type`/`uid_raw`/`status` always; other fields filled only when NULL via `COALESCE` (so a prior `uid_detail` enrichment is never clobbered by a sparse Search row). Gated by `WHERE companies.source = 'uid'`, so Zefix/other rows are skipped entirely.
- `on_conflict="ignore"`: `ON CONFLICT DO NOTHING` — only brand-new UIDs inserted; no existing row touched.
- Note: unlike the old full-import, this no longer annotates Zefix rows' `registration_type` (the gap sweep is `not_in_hr` so it never matches a Zefix/HR company anyway).

**`registration_type` derivation:** `commercialRegisterStatus=2` → `hr`; `vatStatus=2` → `mwst`; both → `both`; neither → `uid_only`.

**`legalForm`:** stored as eCH-0097 code (e.g. `"0106"`=GmbH, `"0109"`=AG, `"0101"`=Einzelunternehmen).

**Client:** `app/clients/uid_client.py` — lazy zeep WSDL init; `search_entities(name=, plz=, not_in_hr=, in_hr=, mwst_only=, legal_form=, active_only=)` / `build_search_params(...)` / `_run_search(params)`, `get_by_uid(uid_str)`, `detail_to_update(entity)`.

**Experiment scripts:** `scripts/uid_phase0_experiment.py` (multi-token AND semantics), `scripts/uid_phase0_filters.py` (PLZ + HR filter probes + WSDL schema introspection).

**Scoring note:** UID-only companies (no `purpose`) excluded at DB query level from NOGA, keyword extraction, Claude classification.

**Job type:** `uid_import` | **Endpoint:** `POST /api/v1/jobs/collection/uid-import`
**Detail job:** `uid_detail` | **Endpoint:** `POST /api/v1/jobs/collection/uid-detail` — calls `fetch_uid_details()`, which filters `source='uid' AND uid_detail_fetched_at IS NULL` so each company is fetched exactly once. `uid_detail_fetched_at` is set after every GetByUID attempt (success or not), preventing re-processing on subsequent runs even for entities with no address. `detail_to_update()` also captures `old_uids` from `OtherOrganisationId` (category `CH.HR`) whenever present.

### Resolve SHAB Old-UIDs — `resolve_shab_old_uids` in `app/services/registry/shab_archive_import.py`

Pre-2014 companies carry an **old cantonal Handelsregister number** (e.g. `CH-035.3.029.394-7`) that the pre-2014 SHAB archive references instead of the modern CHE-UID. This job links those publications by resolving the old number to a modern entity.

- **Two representations, one canonical form:** the register returns the old number under `OtherOrganisationId` (category `CH.HR`) in **digits-only** form (`CH10030251826`); SHAB PDFs print the **dotted** form (`CH-100.3.025.182-6`). `uid_client.normalize_old_hr_number()` reduces both to the dotted canonical (11 digits, structure 3-1-3-3-1) used everywhere. Category filtering matters: `CH.ESTVID` (VAT) also normalises to 11 digits, so `extract_old_hr_numbers()` keys on `CH.HR`, not digit shape.
- **Reverse lookup exists:** the Search request has a **top-level `otherOrganisationId`** field (a sibling of `uidEntitySearchParameters`, so *not* built by `build_search_params`). `uid_client.search_by_old_uid()` fills it and returns the unique modern entity. `_do_search()` is the shared core; `_run_search()` and `search_by_old_uid()` both delegate to it.
- **Per publication** with `company_uid IS NULL` and an old number in `raw_json` (`extracted_old_uid` for Mode B, `ch_number` for Mode A): (1) try free local `old_uids` overlap; (2) on a miss, check the `shab_old_uid_misses` cache (also free) of numbers a prior run already confirmed the register has no match for; (3) only then `search_by_old_uid()` (one API call per **distinct** number, cached per run) → link the pub, write the number into the company's `old_uids` (so later runs match locally), and create a **modern-UID `shab_stub`** for companies absent from our DB. A confirmed non-match is written to `shab_old_uid_misses` (`old_uid` PK, `failed_at`, `attempts` — migration `0122`); without this, a failed lookup leaves `company_uid` NULL forever, so the same doomed number gets re-queried against the slow, rate-limited API on every future run instead of the run converging.
- **Storage:** `companies.old_uids` — GIN-indexed `TEXT[]` (a company that changed canton can have several), also opportunistically populated by `uid_detail`'s `detail_to_update()`. `ix_companies_old_uids` has `fastupdate = off` (migration `0117`): this job interleaves `old_uids &&` reads with writes to the same column inside one long session, and GIN's default pending-list buffering makes reads scan that list linearly, degrading until they exceed the 30s `statement_timeout`. `_get_company_by_old_uid()` also catches a timeout defensively (rolls back, treats as a local miss, falls through to the API lookup) rather than failing the whole job.
- **Real throughput:** a clean isolated `search_by_old_uid()` call is ~5s (3.5s deliberate `_INTER_CALL_DELAY` + ~1.5s real SOAP round-trip) — close to the documented ~17/min. If the job is running noticeably slower than that in practice, the extra time isn't the SOAP call; the remaining suspect is the local `old_uids &&` overlap check that runs before every API fallback (DB-side latency/contention, not API latency).
- **Job type:** `resolve_shab_old_uids` | **Endpoint:** `POST /api/v1/jobs/collection/resolve-shab-old-uids` (superadmin). Params: `batch_size`, `max_lookups` (caps reverse-Search calls per run; rate ~17/min). Resumable — linked rows drop out of the `company_uid IS NULL` set; refuses to run while `uid_import`/`uid_detail` are active (shared SOAP limit). **Frontend:** Collection page → "Resolve SHAB old-UID publications (reverse UID Search)" section.

### Backfill SHAB Old-UID Extraction — `backfill_shab_old_uid_extraction` in `app/services/registry/shab_archive_import.py`

Targeted, SOAP-free repair pass — **not** a full historical re-import. Some publications were imported by an older/buggier extraction pass (or hit a transient PDF-parse failure) and ended up with neither `extracted_uid` nor `extracted_old_uid`/`ch_number` in `raw_json` at all. Those rows are invisible to `resolve_shab_old_uids` — there's nothing in them to look up — so they sit unresolved regardless of how well the reverse UID Search works.

- **Pass 1 (scan):** Python-side `raw_json` parse over `company_uid IS NULL AND raw_json IS NOT NULL` (same pattern as `resolve_shab_old_uids` — `raw_json` is plain `TEXT`, no JSON index), keyset-paginated by `id`. Rows already stamped `old_uid_backfill_checked_at` (a prior run's confirmed-empty result) are skipped. Candidates are split by `source`: Mode B (`shab_archive`, has `archive_id`) vs Mode A (`shab_old_pdf`, has `pub_date`).
- **Pass 2 (Mode B):** one `fetch_pdf_bytes(archive_id)` per affected row, concurrent (`pdf_workers`, same pattern as `_prefetch_pdfs_for_page`) — no archive pagination needed since `archive_id` is already in `raw_json`.
- **Pass 3 (Mode A):** one `fetch_daily_pdf_bytes(day)` per **distinct** affected `pub_date` (entries on the same day share one PDF — far cheaper than one fetch per row), re-parsed via `parse_bulk_hr_entries()` and matched back to rows by `pub_number`. Falls back to `extract_old_uid_from_text()` on the entry's own text if the delimiter-based `ch_number` still comes back empty.
- **On recovery:** updates `raw_json` with the new identifier(s) and immediately attempts local resolution via `_resolve_company_for_shab()` (free — no SOAP call); a register lookup for numbers still unmatched locally is left to `resolve_shab_old_uids`'s next run. On confirmed still-empty, stamps `old_uid_backfill_checked_at` so a re-run doesn't re-fetch the same dead PDF indefinitely.
- **Job type:** `backfill_shab_old_uid_extraction` | **Endpoint:** `POST /api/v1/jobs/collection/backfill-shab-old-uid-extraction` (superadmin). Params: `batch_size`, `pdf_workers`, `request_delay` (Mode A day-to-day sleep). Not rate-limited by the UID SOAP API — safe to run alongside `uid_import`/`uid_detail`/`resolve_shab_old_uids`. **Frontend:** Collection page → "Backfill SHAB old-UID text extraction" section.
- **No `resume_from`** — this function doesn't accept one, unlike most other jobs in this file. It's self-resuming by construction instead: Pass 1's filters (`old_uid_backfill_checked_at` stamp, already-has-an-identifier check) mean a full rescan from `id=0` naturally skips everything a prior run already resolved or gave up on, so re-running from scratch is cheap and idempotent, not wasteful in itself.
- **Mode B chunk size bug (fixed):** was `max(pdf_workers * 5, batch_size)` — dominated by `batch_size` (500 by default) since that's always ≥ `pdf_workers*5`. Job cancellation/pause is cooperative: `abort_cb()` (→ `ctx.assert_not_cancelled`, which raises `JobPausedError` when a deploy calls `job_worker.request_shutdown()`, or the user cancels) is only checked once per chunk, and each Mode B item is a real network PDF fetch with up to a 60s timeout plus exponential-backoff retries (`shab_archive_client._get_with_retry`). A 500-item chunk (only 8 fetched concurrently by default) on a slow SHAB PDF server can run many minutes between `abort_cb()` checks — long enough that a graceful-shutdown request (issued on every deploy) goes unnoticed past K8s' termination grace period, and the pod gets SIGKILLed instead of pausing cleanly. (Note: the per-job heartbeat itself is a separate 30s daemon thread — see §18 — unaffected by chunk size; it's cancellation responsiveness that chunk size controls.) A hard-killed pod's job can only be recovered later via the 300s stale-heartbeat sweep (`requeue_interrupted_jobs`) — and since this function accepts no `resume_from`, that recovery restarts the whole scan from the top. This is what looked like "never finishes, just keeps restarting with a lower number" during a run of frequent redeploys (the total shrinks each restart as more of the backlog genuinely gets resolved, but each restart re-does Pass 1 and re-fetches whatever Mode B/A work hadn't committed yet). Fixed: chunk size is now `max(pdf_workers * 3, 10)` (24 by default, independent of `batch_size`) so `abort_cb()` is checked often enough to pause cleanly on the next deploy — this does not eliminate the "restart from scratch" behavior (still no `resume_from`), it just gets there via a clean pause instead of a crash+recovery.

### SHAB Archive Import — `app/services/registry/shab_archive_import.py`

Imports historical SHAB publications from `shab.ch`. Operates in two modes depending on the date range, dispatched automatically by `handle_shab_archive` in `app/services/jobs/job_handlers/shab_archive.py` (cutoff: `2012-12-01`):

#### Mode A — Pre-2012 bulk PDF (`import_shab_old_pdfs`)

Pre-December 2012, SHAB published one PDF per day covering all cantons + all publication types. Endpoint: `GET /api/v1/archive/issue-of-today?date=YYYY-MM-DD&language=de&tenant=shab` (requires browser User-Agent; 404 on weekends/holidays).

**PDF format eras** — three distinct delimiter structures; auto-detected by `_find_entry_delimiters`:
| Era | Delimiter format | Pub-number digits | Entry bullet |
|---|---|---|---|
| 2002–mid-2008 | `Tagebuch Nr. NNNN vom DD.MM.YYYY\n(NNNNNN / CH-…)` | 6 | `I ` (Roman I) |
| mid-2008 | `Tagesregister-Nr. NNNN vom DD.MM.YYYY\n(NNNNNNNN / CH-…)` | 8 | `■` |
| 2009–2012 | `Tagesregister-Nr. NNNN vom DD.MM.YYYY / CH-… / NNNNNNNN` (single line) | 8 | `■` |

The `■` glyph is encoded differently by PyMuPDF depending on PDF year: `\x84` (U+0084, 2008–2011) or a Unicode PUA codepoint like U+F06E (2012+).

**Workflow:** iterates weekdays Mon–Fri; skips 404s; calls `check_bulk_pdf_structure` → `parse_bulk_hr_entries`; upserts `SogcPublication` rows with `sogc_id = "shab_old_{YYYYMMDD}_{pub_number}"`. The parser exposes each entry's old cantonal number as `entry["ch_number"]`; it is normalised (`normalize_old_hr_number`) and resolved to a company via `_resolve_company_for_shab(..., old_uid=…)` (array overlap on `companies.old_uids`). Matched publications are linked (`company_uid`/`company_id` set); local misses (and unmatched-but-titled entries → a `shab_stub` keyed by the old number) are later reconciled by the `resolve_shab_old_uids` job via reverse UID Search.

**Structural validation:** `check_bulk_pdf_structure` logs critical format changes to the Error Center (`company_errors` table, `source="shab_old_pdf"`) rather than silently skipping. Days with critical issues (HR end marker missing, zero delimiters) are counted in `days_skipped`.

#### Mode B — Post-2012 per-publication API (`import_shab_archive`)

Paginates the `shab.ch` public archive API (`https://www.shab.ch/api/v1/archive/public`). PDF-based; links publications to the `companies` table and creates stub entries for cancelled companies absent from Zefix.

**Workflow:**
1. `fetch_archive_page(page, size)` — paginates the archive list (`includeContent=false`).
2. For each HR01/HR02/HR03 entry: `fetch_pdf_bytes(id)` → `extract_text_from_pdf()` (pypdf).
3. Extract the modern UID via regex (`CHE-xxx.xxx.xxx`) **and** the old cantonal number (`extract_old_uid_from_text` → `CH-xxx.x.xxx.xxx-x`); detect language (lingua + canton fallback). The batch pre-resolver `_build_shab_uid_map` keys companies by **both** identifiers via two queries (`uid IN (…)` and `old_uids && ARRAY[…]`).
4. `_resolve_company_for_shab(db, uid, title, canton, pub_date, uid_map, stats, old_uid=…)` — resolves by modern CHE UID first, then by old cantonal number (batch cache → DB). The returned `company_uid` is always the company's **canonical modern** uid, even when matched via the old number. Three outcomes:
   - **Found, same name** — link publication to company; no change.
   - **Found, different name** — merge SHAB title as a historical name into `company.old_names` (`{"name": ..., "source": "shab_archive", "date": ...}`), then link.
   - **Not found** — create a `Company` stub with `source="shab_stub"`, `status="CANCELLED"`, keyed by the modern uid if present else the old number (which is also stored in `old_uids`), `name` (from API title), `canton`, `first_sogc_date`; add to batch `uid_map` to avoid duplicates within the same page.
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

### Link SOGC Stubs — `run_link_sogc_stubs` in `app/services/registry/shab_archive_import.py`

Back-fills `company_id` on `sogc_publications` and `sogc_person_appearances` rows that already have a `company_uid` but no `company_id`. Works entirely from already-imported DB data — no API calls or PDF downloads.

**Algorithm (keyset-paginated, batch_size UIDs per commit):**
1. Find distinct `company_uid` WHERE `company_uid IS NOT NULL AND company_id IS NULL` in `sogc_publications`, ordered alphabetically for cursor pagination.
2. For each batch: bulk-fetch matching `Company` rows.
3. For UIDs without a Company row: read `raw_json["title"]` (SHAB archive format) or `raw_json["meta"]["title"]["de"]` from one publication to get a display name; create a `shab_stub` Company (same savepoint + IntegrityError pattern as `_resolve_company_for_shab`).
4. Bulk-update `sogc_publications.company_id` and `sogc_person_appearances.company_id` via SQLAlchemy `update()` statements.

**Job type:** `link_sogc_stubs` — registered in `JOB_HANDLERS`.

**API endpoint:** `POST /api/v1/collection/link-sogc-stubs` (superadmin only).

**Frontend:** Collection page → "Link SOGC Stubs" section (after SHAB Archive Import).

### SMTP — `app/services/notifications/email.py`

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

**File:** `app/services/scoring/scoring.py`

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

**Files:** `app/services/ml/cluster_pipeline.py`, `app/services/ml/noga.py`, `app/services/ingestion/collection.py`

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

### Semantic Boilerplate Stripping (`purpose_clean`, DE/FR)

`purpose_clean` on `Company` (migration `0127_add_purpose_clean.py`) stores the
cleaned purpose text consumed by NOGA classification, Claude classification, and
the `purpose_clean` embedding. Two stripping methods feed it, chosen per
`purpose_language`:

- **DE/FR (`SEMANTIC_LANGS`)** — embedding-similarity method in
  `app/services/ml/boilerplate_semantic.py`. Company purpose texts mix a
  substantive first part with a generic "ancillary powers" tail (branch
  offices, real estate, financing/guarantees), reliably introduced by a modal
  verb (`kann` / `peut`/`peuvent`) starting at sentence 2+. The regex-based
  method below only catches this tail when it recurs as a near-exact sentence
  and is too coarse as a blanket structural rule (destroys real content when
  the ancillary clause is worded differently — utilities, foundations, niche
  trades). Instead: find the modal-verb trigger sentence + next 2, embed them
  (same `paraphrase-multilingual-mpnet-base-v2` model as NOGA), score against a
  handful of known-generic exemplar sentences per language, and cut at the
  first sentence scoring ≥ `SIMILARITY_THRESHOLD` (0.72). Validated on the full
  DE corpus (94.6% coverage on a 3000-company sample vs ~20% for hand-picked
  regex anchors) and against known false positives (utility company service
  descriptions, foundation-specific activities, niche trade descriptions) that
  a bare structural rule would have destroyed.
- **Other languages (IT, etc.)** — falls back to the existing regex-based
  `_strip_purpose_boilerplate()` (see Boilerplate Pattern Analysis below),
  unchanged.

**Entry points:**
- `get_purpose_clean(company, boilerplate_patterns)` — single-company. Returns
  `company.purpose_clean` directly if already populated; otherwise computes it
  live (used as a fallback for companies not yet processed by the backfill
  job). Called from `classify_company_noga`, `classify_company_noga_v2`,
  `claude_classify._build_user_text`, and both `purpose_clean` embedding paths
  (`embed_purpose_clean_batch`, `embed_batch_for_noga`).
- `compute_purpose_clean_batch(companies, boilerplate_patterns)` — batch-efficient
  version: collects every DE/FR company's trigger window across the whole input
  list into one `embed_texts()` call. Used by the backfill job below and
  internally by `get_purpose_clean` (as a batch of one).
- `strip_purpose_semantic_batch()` — keyset-paginated backfill job (`batch_size=500`,
  same shape as `_run_embed_batch`), writes `purpose_clean` +
  `purpose_clean_computed_at`. Job type `strip_purpose_semantic`, triggered via
  `POST /scoring/strip-purpose-semantic` (Collection page → "Strip Purpose
  (Semantic)"). **Run after `detect_language_bulk`, before `reclassify_noga` /
  `embed_purpose_clean` / Claude classification** so those consume the
  precomputed column instead of re-stripping (and re-embedding) on every call.

**Not touched:** TF-IDF clustering (`cluster_pipeline.py::strip_boilerplate`)
still uses the regex method directly — it operates on raw text lists rather
than `Company` objects, so threading `purpose_clean` through would need a
larger refactor. Out of scope for now.

**Standalone validation tool:** `scripts/validate_boilerplate_similarity.py` —
diagnostic script (not part of the app) used to validate the threshold/exemplars
and spot-check specific company IDs before this was wired into production.
Supports `--lang`, `--limit`/`--full` (keyset-paginated to avoid the 700k-row
statement-timeout trap), and `--ids` for targeted re-checks.

---

### Company Purpose Embeddings (Semantic Search)

Stores 768-dim L2-normalized embeddings per company in `company_embeddings` for free-text semantic search.

**Two embedding types:**
- `purpose_full` — raw `company.purpose` text
- `purpose_clean` — boilerplate-stripped purpose; see "Semantic Boilerplate Stripping" above for how it's computed (semantic for DE/FR, regex fallback otherwise)

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
- `app/services/ml/company_embedding_pipeline.py` — batch jobs, upsert helpers, semantic search
- `alembic/versions/0083_add_company_embeddings.py` — migration

**Debug / explain endpoint (superadmin only):**
`GET /api/v1/companies/{id}/noga-explain` — re-runs classification for a single company and returns the full intermediate trace:
- Stripped purpose, detected language, extracted tokens, embed text
- Per-level (L1–L5): top-10 candidates with embedding similarity, normalized token score, excludes cosine penalty, final hybrid score, and the winner
- Flags for lookahead tie-breaking and fallback usage at each level

Implemented in `classify_company_noga_explain()` in `app/services/ml/noga.py`. Exposed in the company detail UI as a "NOGA explain" button (violet, header area) visible only to superadmins; opens a modal with the full trace.

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

**Progress-count performance (`include_stale`):** [app/services/ml/noga_pipeline.py](app/services/ml/noga_pipeline.py) `reclassify_noga`'s `include_stale` mode ORs `noga_code IS NULL` with a cross-column comparison (`noga_classified_at < updated_at - interval`), which can't be served by a btree index and forces a full table scan — this timed out in production (job 692, 2026-06-16) even after bumping `statement_timeout` to 120s. The exact `COUNT(*)` for this path was replaced with a cheap planner estimate (`pg_class.reltuples`) since it's only used for progress display, not iteration logic. The plain `only_missing_noga` (non-stale) path keeps an exact, index-backed count via the `ix_companies_no_noga_code` partial index added in migration 0102.

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
# → the helvex-ml pod's JOB_TYPE_WHITELIST (infra/charts/helvex/templates/ml-worker-deployment.yaml)
```

Routing is by `JOB_TYPE_WHITELIST` / `JOB_TYPE_BLACKLIST` env var, not by queue —
every pod polls the same `job_runs` table and simply filters which types it claims.
With no whitelist and no blacklist set (local dev), one process handles all types.

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

### Core function — `app/services/billing/credits.py`

```
check_and_deduct(db, org_id, action, count=1) → (ok: bool, balance: int)
```

- Reads the org's current balance.
- Checks the deduction amount for `action` from the `CreditCostConfig` table (or hardcoded defaults).
- Atomically deducts (`SELECT … FOR UPDATE` row lock — no double-spend race) or returns `False` if insufficient (`after < 0` guard — balance never goes negative).
- Calls `_maybe_low_credit_alert(db, org_id, balance)` after every deduction.

**Deduction is at enqueue.** `_apply_credit_deduction_if_needed` (job_worker) charges when a metered job is queued; the charged `count` is either server-computed (`csv_export`, `recalculate_scores` via `count_companies`) or a user `limit` that also *bounds* the work — so you can't get more work than you pay for. The only `crud.create_job` call is inside the enqueue path, so no route bypasses deduction.

**Refunds are prorated + idempotent (SECURITY).** `_refund_job_credits_if_needed` (called on cancel/fail) refunds only the *undone* fraction: `cost × (total − done) / total`. This closes an abuse where a user runs a large metered job to near-completion (results persist via per-batch commits), cancels, and keeps the work while getting a full refund. Jobs that never started (`done=0`) get a full refund. A ledger check on `reference_id="refund:job:{id}"` makes the refund idempotent (no double-refund on a cancel-then-recovery race). Tests: `tests/test_credit_refund.py`.

**Inline (synchronous) actions refund on failure too.** Actions charged directly in an HTTP handler rather than via a job — currently the immediate single-company web search (`companies/detail.py::google_search_for_company`) — deduct up front but call `credits.refund_action(...)` in their failure branches so a Serper/network error costs the org nothing net. `refund_action` writes a linked `refund` ledger row, is idempotent by `reference_id`, and mirrors the free/unlimited exemptions of `check_and_deduct` (never over-credits). The company is resolved *before* the charge so a 404 never consumes credits. This is the ledger-only model (no separate operations table): net consumption is reconstructed from the deduction/refund pair.

**CSV export — single charge + full refund on failure.** The two export routes (`companies/list.py::export_companies_csv`, `jobs.py::enqueue_csv_export`) charge `bulk_export_basic` once, by tier cap, in the route. They pass that deduction to `enqueue_job(credit_deduction={..., "prorate": False})`, which records it in the job's `_credit_deduction` stats so `_refund_job_credits_if_needed` issues a **full** refund on failure/cancel (an export produces an atomic S3 file — a failed export keeps nothing, so it is never prorated). `csv_export` was **removed** from `_resolve_credit_action_and_count` to stop a prior **double charge** (route + enqueue) that also mis-counted *all* matching rows ignoring the cap. The `_credit_deduction` record now carries a `prorate` flag (default `True` for metered jobs whose partial work persists; `False` for atomic deliverables).

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
| `S3_ACCESS_KEY` | No* | Hetzner Object Storage key (shared by backup + export buckets) |
| `S3_SECRET_KEY` | No* | Hetzner Object Storage secret |
| `S3_ENDPOINT_URL` | No* | e.g. `https://nbg1.your-objectstorage.com` |
| `S3_BUCKET_EXPORTS` | No* | `helvex-exports` — async CSV export storage; *required for csv_export job type |
| `JOB_WORKER_CONCURRENCY` | No | Jobs run in parallel per pod; `1` by default. Multiplies with a job's own `crawl_concurrency` — see values.yaml |
| `CRAWL_PAGE_WORKERS` | No | Threads for off-loop page processing (lxml parse + S3 upload); `32` by default. Per-pod, shared by all crawl jobs — raise alongside `crawl_concurrency` and give the pod CPU to match |
| `JOB_TYPE_WHITELIST` / `JOB_TYPE_BLACKLIST` | No | Comma-separated job types this pod may / may not claim |
| `JOB_POLL_INTERVAL` | No | Seconds between queue polls when idle; `5` by default |
| `JOB_SHUTDOWN_JOIN_TIMEOUT` | No | Seconds shutdown waits for jobs to checkpoint; `25` by default |
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

### ⚠️ How a new env var actually reaches a pod (two-step allow-list — easy to get wrong)

Adding a secret in GitHub → Settings → Secrets → Actions is **not sufficient** on
its own. `helvex-env` is built from an **explicit `--from-literal` allow-list** in
the deploy workflow, so a GitHub secret that isn't referenced by a `--from-literal`
line is silently ignored. Both steps are required for every new var:

1. **Create the GitHub Actions secret** (`Settings → Secrets and variables →
   Actions`).
2. **Add a matching `--from-literal` line** to the `kubectl create secret generic
   helvex-env` block in **both** `.github/workflows/deploy-prod.yml` **and**
   `deploy-dev.yml`. Without this line the value never lands in the cluster secret
   and the app falls back to the field's default in `app/config.py`.

Then two propagation facts:

- **`envFrom` injects env only at container start.** Editing/re-applying the
  secret does **not** update running pods — a rollout is required. A normal
  `[deploy-prod]` rolls a new image (so pods restart and pick up the new env);
  a secret-only change needs `kubectl rollout restart deployment/helvex -n
  helvex-prod`. There is **no `checksum/config` annotation** forcing restart on
  secret change.
- **Never `kubectl patch` the secret as a durable fix.** The next deploy rebuilds
  `helvex-env` from the workflow allow-list and overwrites any manual edit —
  silently reverting it. The allow-list line is the source of truth.

**Bool/typed vars — guard against empty:** reference optional bool/typed secrets
with a fallback, e.g. `"${{ secrets.WORLDLINE_PAYMENT_PAGE_ENABLED || 'false' }}"`.
An unset secret renders as `""`, and an empty string fails Pydantic `bool` parsing,
which crashes startup under prod's strict config validation.

**Verify a var is live in the running pod:**
```bash
kubectl exec deploy/helvex -n helvex-prod -- \
  python -c "from app.config import settings; print(settings.<field_name>)"
# or, raw:
kubectl exec deploy/helvex -n helvex-prod -- printenv <ENV_VAR_NAME>
```
`printenv` exiting non-zero means the var isn't in the pod at all → the allow-list
line (step 2) is missing.

*(This exact gap kept the Worldline Payment Page disabled: the GitHub secret existed
but `WORLDLINE_PAYMENT_PAGE_ENABLED` had no `--from-literal` line, so the flag stayed
at its `False` default in the pod. Fixed 2026-07 in both deploy workflows.)*

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
| `postgres-backup-schedule.yaml` | ScheduledBackup | S3 backups; CNPG's own `retentionPolicy` (`postgres.backupRetention`) only prunes *within the currently active* `backupServerName` prefix — it has no awareness of prior cluster incarnations |
| `postgres-backup-prune.yaml` | CronJob | Deletes S3 `<serverName>/` directories other than the currently active one (from `pg-backup-meta` ConfigMap) once their embedded timestamp exceeds `retentionDays`. A directory with no timestamp suffix (the legacy stable name `helvex-pg`, pre-dating the timestamped-naming scheme) is *not* current by definition once superseded, so it's deleted outright rather than skipped — a prior bug here (`SKIP (no timestamp)`, never deleting it) let ~298GB of orphaned backups accumulate silently for months |
| `postgres-restore-point-sync.yaml` | CronJob | Writes `restore-point.json` (S3) + `pg-backup-meta` ConfigMap so the next `restoreFromBackup: true` deploy knows which `serverName` to restore from. Validates the resolved `restoreSource` still exists in S3 before writing it — a value from a previous incarnation can go stale once `postgres-backup-prune` deletes it, and restoring from a since-deleted path would break the next disaster-recovery deploy; falls back to the current active `serverName` when stale |
| `postgres-weekly-export.yaml` | CronJob | Weekly `pg_dump` (long-retention, separate failure domain, **not** tied to `backupServerName`/`restoreSource` — fixed `STORAGEBOX_PATH`) uploaded to Hetzner Storage Box via SFTP/rclone; init container retries readiness with `timeout`-wrapped `pg_isready` before dumping — the `postgres:16-alpine` image's musl DNS resolver can intermittently hang past its own connect timeout on a freshly-started pod, so every DB-reaching command in this job is wrapped in `timeout` rather than relying on the client tool's own timeout flags |
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
| `loadbalancer` | Hetzner LB (`lb11`); targets all non-DB nodes; health check on `GET /health`; `proxyprotocol = true` on the HTTP/HTTPS TCP services so Traefik can see the real client IP (see below) |

### Real client IP propagation (PROXY protocol)

The Hetzner LB does **TCP passthrough** (not HTTP), so without PROXY protocol Traefik would only ever see the LB's private IP (`10.0.1.x`) as the connecting peer — collapsing every visitor into one IP for `app/auth.py`'s `get_client_ip()`, which breaks IP-keyed rate limiting (login, register, forgot-password, etc. in `app/api/routes/auth.py`) and makes `activity_log.ip` useless for audit purposes.

Fix (both sides required, or the LB's PROXY-protocol header is sent but never decoded):
- `loadbalancer` module: `proxyprotocol = true` on both `hcloud_load_balancer_service` blocks.
- `servers` module: the control-plane cloud-init's Traefik `HelmChartConfig` sets `ports.{web,websecure}.proxyProtocol.trustedIPs` and `forwardedHeaders.trustedIPs` to the cluster's private subnet (`var.cluster_private_cidr`, threaded through from `envs/prod/variables.tf`'s `subnet_cidr`) — this tells Traefik to trust and decode the PROXY protocol header only from the LB's own network, then set `X-Forwarded-For` to the real client IP for the app.

Since node cloud-init only runs once at provisioning, this change only takes effect on **new** nodes; existing nodes need `kubectl apply` of the updated `HelmChartConfig` manually (same manifest as in `control-plane.yaml.tpl`) plus a `kubectl rollout restart deployment/traefik -n kube-system`, and the LB service change needs `terraform apply` from `infra/terraform/envs/prod`.

### Node replacement is not zero-downtime today (no HA on control-plane or DB)

`hcloud_server.user_data` forces a destroy+recreate on any change (there's no in-place update for cloud-init), so any edit to `control-plane.yaml.tpl`/`worker.yaml.tpl` — or plain state/config drift — can put a node up for replacement. Whether that's an outage depends on the tier:

| Node | Redundancy today | Impact of replacement |
|---|---|---|
| `app1` (control-plane) | None — single k3s server | Full API/ingress outage until the new node is up and Traefik is healthy |
| `db1` (Postgres, CloudNativePG) | None — `postgres.instances: 1` | Real outage: CNPG must bootstrap fresh from the S3 backup + WAL replay (no data loss given WAL archiving, but not instant) |
| `ml1`, apiWorker, frontend, etc. | Yes — `replicaCount: 2` (apiWorker) or stateless | Drain reschedules pods elsewhere; `ml1` briefly pauses ML jobs (background, not user-facing) |

**To make control-plane/DB replacement zero-downtime**, both need real redundancy first (tracked in [roadmap.md](roadmap.md)): a 2nd/3rd k3s server node for embedded-etcd HA, and CNPG `instances: 2` for a standby replica enabling a planned switchover.

**General safe node-swap procedure** (works today for any tier, avoids Terraform's `-/+ replace` entirely): add the replacement node under a **new** key in the `servers` map (new private IP) so `apply` creates it alongside the old one instead of recreating the same resource address; join it to the cluster and migrate/drain workloads over; verify healthy; only then remove the old key and `apply` again as a clean destroy of the old node. This is the only way to swap a load-bearing node without an outage window using this Terraform layout.

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

> ⚠️ **Identity scoring is being reworked.** The current single `web_score`/`confidence`
> scalar conflates three questions (is this the company's site? / how good is the site? /
> how relevant is it to me?) and UID matching is a crude binary compare. The approved
> redesign splits **identity** (probability + category + evidence ledger, ledger-first
> then a GBM) from **content** (global facts) and moves **fit** to the per-org scoring
> layer; `web_score` is retired as a relevance input.
> See [`docs/code-review/web-identity-rework.md`](docs/code-review/web-identity-rework.md).
> Phases 1, 2, 3 and part of 4 have shipped (below); 4's full company-level vocabulary
> cutover, UID hardening, `web_score` retirement, and the GBM (phases 4b–6) have not.
> The description below is the *current* implementation.

### Overview

Three-phase pipeline: crawl ~210k Swiss company websites → store raw HTML in S3 → extract structured data (contacts, UID, socials, keywords) into `company_web_extract`.

### Pod topology

| Pod | Image | Job types | Node |
|---|---|---|---|
| `crawler-http` × 2 | `helvex-app` | `web_crawl_http`, `web_crawl_content`, `web_url_populate`, `web_select_url`, `web_crawl_single`, `directory_crawl` | main node |
| `api-worker` | `helvex-app` | (many others, no longer `web_extract`) | main node |
| `ml-worker` | `helvex-ml` | `web_crawl_http`, `web_crawl_playwright`, `web_crawl_content` (idle-fill), **`web_extract`** + all ML job types | ml node (cax21) |

`web_extract` runs **only on `ml-worker`** — it is the only image with the spaCy NER models (`fr/it/en_core_news_sm`, `de_core_news_md`) bundled (see `Dockerfile.ml-base`). This replaces the earlier "Pod A crawls, Pod B extracts" parallelism on `crawler-http`/`api-worker`: extraction no longer runs concurrently with crawling on the main node, it queues for ml-worker instead. Trade-off accepted to keep spaCy off the main-node app image.

**Every `JOB_HANDLERS` entry must appear in some pod's `JOB_TYPE_WHITELIST`** (or be excluded from the main pod's `JOB_TYPE_BLACKLIST` so it falls through to it). In prod, `apiWorker` + `mlWorker` are both enabled, which sets `DISABLE_JOB_WORKER=true` on the main `app` pod (`deployment.yaml`) — so a job type reachable by *no* worker's whitelist just sits `queued` forever with no error, silently. This has recurred more than once when a new handler was registered in `job_handlers/__init__.py` without a matching whitelist entry (`backfill_shab_old_uid_extraction`, `discover_directory_domains`, `rescore_scope`, `directory_crawl` were all found orphaned and fixed together 2026-07-25). No automated check catches this today — when adding a `JOB_HANDLERS` entry, grep all three `*-deployment.yaml` whitelists (`api-worker`, `ml-worker`, `crawler-http`) to confirm the new type is covered by at least one.

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
                         company_web_pages rows (crawled=TRUE), crawl_status=crawled
                         + full site inventory persisted (crawled=FALSE rows) from the
                         sitemap URL list, classified by page_type (see below) — pages
                         beyond the fetch budget are recorded but not fetched
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
       ↓ (separate job, manual trigger)
directory_crawl        Queries company_url_candidates for URLs in DIRECTORY_CRAWL_DOMAINS
                       (moneyhouse.ch, local.ch, northdata.com, treuhandvergleich.ch, etc.)
                       httpx fetch → trafilatura text + rating/review/category heuristics
                       → company_directory_data PK (company_id, url); no S3 (text in DB)
                       Output feeds into claude_classify _build_user_text() as "External profiles"
```

Both crawler tiers run robots.txt + sitemap.xml discovery (`crawler_sitemap.py`) before
page selection: sitemap URLs fill subpage slots the homepage nav misses, and the robots
`Crawl-delay` raises the per-domain rate limit.

#### Page inventory & taxonomy (web-pipeline holistic rework, Layer A — phase 1)

The crawler discovers far more of the sitemap than it fetches. `crawler_common.classify_all_urls`
classifies every same-origin sitemap URL (capped at 60/company — `_MAX_INVENTORY_URLS`) into a
`page_type` and persists it as a `company_web_pages` row even when not fetched
(`crawled=FALSE`, `discovered_via='sitemap'`, no HTML/S3 key). Fetched pages get `crawled=TRUE`,
`discovered_via='homepage'|'nav'`, and a `priority` (fetch order).

Page-type taxonomy (`_SUBPAGE_PRIORITY` in `crawler_common.py`, DE/FR/IT/EN keyword sets):
`impressum`, `privacy`, `contact`, `about`, `team`, `services`, `products`, `references`,
`news`, `jobs`, `other`. Only a fetch-worthy subset (`_FETCH_WORTHY_TYPES` =
`impressum`/`privacy`/`contact`/`about`/`team`/`services`) is ever spent on the crawl budget
(`_FETCH_PRIORITY`, used by `find_subpage_links`/`classify_urls_by_path`) — `news`/`jobs`/
`products`/`references` are inventoried for visibility (paginated catalogs/job boards, low
signal-per-byte) but not auto-fetched; a later phase adds on-demand fetch from the profile UI.

`crud/crawler.py::save_page_inventory` inserts inventory-only rows, skipping URLs already saved
as fetched pages this run; `get_page_inventory` returns the merged list (fetched first, then
by priority) — used by `GET /{id}/web-extract`'s `pages` array and rendered in `WebsitePanel`'s
"Crawl coverage" table with a Crawled/Discovered status badge.

This is phase 1 of `docs/code-review/web-pipeline-holistic-rework.md` (ingestion only — no
identity/scoring changes yet).

#### Structured content extraction (phase 2 — team/services → NOGA/AI feed)

`crawler_extract.py` now runs page-type-aware structured extraction alongside the existing
plain-text/contact parsing, all deterministic (no API cost):

- **`team` pages** → `_extract_team_struct(soup)`: headings shaped like a person's name (regex
  `_NAME_SHAPE_RE`, 2–4 Title-Case tokens) become entries; the short text immediately following
  (`_following_text`) is checked against `_ROLE_KEYWORDS` (DE/FR/IT/EN) to capture a role. Names
  without a recognised role are still kept (`role=None`) — the name itself is useful signal.
  Team text is also fed into the existing `_extract_persons`/`_extract_persons_ner` pipeline
  (added to `impressum_text_parts`), so the plain `persons` list benefits too.
- **`services`/`products` pages** → `_extract_services_struct(soup)`: each heading + its
  following short text becomes `{"title", "summary"}`, skipping nav/legal-boilerplate headings
  (`_NON_SERVICE_HEADINGS`) and too-thin summaries.
- **`about`/`homepage`** → `about_text`: the longest cleaned paragraph among about/homepage
  pages (capped 2000 chars) — richer than the existing 1000-char `description`.

Aggregated (deduped across pages) in `resolve_company_extract()` and persisted on
`company_web_extract` (migration `0119`): `persons_struct` JSONB, `services_struct` JSONB,
`about_text` TEXT. A company whose only crawled evidence is team/services content (no
impressum/contact/UID) now still gets a persisted row — the identity-confidence early-return
gate was extended to check `has_content` alongside the existing identity signals, without
changing the confidence formula itself (identity math is phase 3/4 territory).

**Feeds back into classification** (the actual point — thin/boilerplate Zefix `purpose` text is
a recurring NOGA quality complaint, see ROADMAP "NOGA v2"):
- `app/services/ml/noga.py::classify_company_noga` — `_web_content_text()` fetches the
  company's best web extract and appends `about_text` + up to 5 service titles (capped 600
  chars) to the stripped-purpose text before embedding/token classification. One indexed query
  per company (cheap relative to the embedding inference cost); doesn't expand NOGA eligibility
  to companies with zero purpose (`only_detailed_raw` gating unchanged — a separate, bigger
  decision left for later).
- `app/services/scoring/claude_classify.py::claude_classify_batch` — bulk-prefetches
  `about_text`/`services_struct` for the whole batch in one query (`_web_text_by_company`,
  mirroring the existing `_dir_data_by_company` directory-profile prefetch pattern — reduced to
  best-per-company in Python, not SQL `DISTINCT ON`, so it stays portable to the SQLite test DB),
  appended to `_build_user_text()`'s prompt as `"Website content: …"`.

Surfaced on the profile: `GET /{id}/web-extract` now returns `persons_struct`, `services_struct`,
`about_text`; `WebsitePanel`'s Content card shows a Team field (name + role chips, falling back
to the plain `persons` chips when no structured entries exist) and a Services field
(title + summary per entry).

Tests: `tests/test_crawler_extract_structured.py`, `tests/test_noga_web_content.py`.

#### Evidence ledger (phase 3 — identity, additive only)

`resolve_company_extract()` now also emits `evidence` (JSONB, migration `0120`): a typed,
inspectable list of `{dimension, direction: "+"|"-", strength: decisive|strong|medium|weak,
value}` entries built by `_build_evidence_ledger()` from the **same inputs already driving
`confidence`** — this phase is a restructure, not new detection or a behavior change:

| dimension | direction | strength | source |
|---|---|---|---|
| `uid_matches_zefix` | + | decisive | `uid_matches is True` |
| `uid_mismatch` | − | strong | `uid_matches is False` |
| `address_match` | + | strong / medium | `addr_full_match` / `addr_partial_match` |
| `zone_name_match` | + | strong (≥0.55) / medium (≥0.30) / weak (>0) | `zone_name_conf` |
| `signal_coverage` | + | weak | `base` (signal count / 7) |

`confidence`/`method`/`compute_verdict` are **unchanged** — the ledger is persisted alongside
them, not used yet. It's the feature vector the next phase (categorical verdict, replacing the
confidence ladder) computes from; deliberately excludes dimensions the design calls for but
that need new detection logic not yet built (`phone_matches_reg`, `is_marketplace`/`is_parked`,
`purpose_sim` — the last is computed by a separate later ML-worker job, not at extraction time).
Exposed via `GET /{id}/web-extract`'s `evidence` field (no frontend display yet — that lands
with phase 4's identity card, which needs the categorical verdict to be meaningful).

Tests: `tests/test_crawler_evidence_ledger.py`.

#### Categorical identity verdict (phase 4 — per-candidate labels + verdict fixes)

`website_status.py` gains a per-candidate categorical label alongside the scalar `confidence`,
plus two real behavioral fixes to `compute_verdict` — all additive; the company-level
`companies.website_status` vocabulary (verified/confirmed/likely/social_only/directory_only/none)
is **unchanged** in this phase (a full vocabulary cutover is a separate, larger, cross-cutting
change — frontend badge, tier gating, filters — deliberately deferred).

- **`categorize_identity(confidence, uid_matches, has_evidence, thr) -> (category, probability)`**
  — maps one candidate into `MATCH_UID` / `MATCH_STRONG` / `MATCH_WEAK` / `MISMATCH` / `UNKNOWN`.
  `probability` is currently just `confidence` (the existing deterministic combine) — the future
  GBM phase swaps the combine step behind this same signature. `AMBIGUOUS`/`RELATED_ENTITY` are
  cross-candidate/cross-entity outcomes, computed in `compute_verdict`, not per-row here
  (`RELATED_ENTITY` needs a parent/subsidiary graph lookup — not yet built).
  Computed and persisted in `handle_web_extract` right after `resolve_company_extract` (which
  already has `db`/thresholds in scope) onto `company_web_extract.identity_category` /
  `identity_probability` (migration `0121`). Exposed via `/web-extract` and `/web-extracts`;
  shown as a badge in `WebsitePanel` (`IdentityCategoryBadge`, distinct from the existing
  company-level `WebsiteStatusBadge`).
- **Auto-pick tiebreak** (`_pick_best_candidate`) — `compute_verdict` no longer silently
  max-picks by raw confidence within a tier. It now prefers `name_address_verified` candidates
  first, then confidence — a partial implementation of the identity rework's 4-step tiebreak
  (steps 1 "own-verified-socials-link" and 2 "UID-bearing" aren't differentiable with data
  collected today — UID-true candidates are always tier VERIFIED already, so they never compete
  within CONFIRMED/LIKELY; registry-phone doesn't exist yet). Sets `Verdict.ambiguous: bool` when
  ≥2 *distinct-domain* candidates land within `_AMBIGUITY_MARGIN` (0.05) of the winner — the pick
  is still made (never left blank pending review), but the judgment call is now visible rather
  than hidden behind a confident-looking URL.
- **No snippet fallback after a real crawl** — if a crawl was attempted but no candidate
  cleared any tier (e.g. every candidate's UID mismatched), `compute_verdict` used to fall
  through to `classify_search_results` (the pre-crawl snippet score), letting a weak
  search-result guess overrule genuine negative crawl evidence. Now: `rows` non-empty but
  nothing tiered → `Verdict(NONE, ...)` directly, no fallback.
- **Cut pre-crawl scoring entirely (identity rework phase 3, fully landed)** — the remaining
  fallback (never-crawled companies got a `classify_search_results` verdict) is gone too.
  `compute_verdict` now returns `Verdict(None, None, 0, None)` — i.e. `website_status`/
  `website_url` stay `NULL` — for any company with zero `company_web_extract` rows,
  regardless of how good its search snippet score was. `web_enrichment.py`'s
  `_search_verdict_fields` (used by `enrich_company_website`, `rescore_from_stored_results`,
  `recalculate_google_scores`) now calls `compute_verdict` directly instead of
  `classify_search_results` — so a fresh Google/ScrapingDog search **never** sets a
  positive `website_status` by itself anymore; only an actual crawl does.
  `classify_search_results` itself is unchanged and still callable (kept as a documented
  crawl-ordering helper per the design doc's §3.4), it's just no longer wired into any
  persisted verdict. Candidate creation for the crawler (`company_url_candidates` via
  `_sync_url_candidates`) was decoupled from the verdict in the same change — it used to
  run only `if enriched` (i.e. only on a positive verdict), which would have starved the
  crawl queue entirely once fresh searches stopped producing positive verdicts; it now
  runs unconditionally after every search. Existing rows written before this change keep
  their pre-phase-3 search-only verdict until `recompute_website_status` is re-run (superadmin
  crawler admin page) — that job already used `compute_verdict`, so re-running it backfills
  the correct `NULL` for any never-crawled company. Regression test:
  `test_compute_verdict_unknown_when_never_crawled`
  (`tests/test_website_status.py`).
- **Fixed a real `is True`/`is False` bug** in `_extract_tier`: `get_web_extracts_with_urls` uses
  raw SQL (`text()`), which returns plain 0/1 ints (not the bool singleton) on some
  driver/dialect combinations (observed on SQLite; Postgres/psycopg2 already returns real bools)
  — `uid_matches_zefix is True` silently misclassified those as the `None` branch. Changed to
  `==`, which preserves the None/True/False tri-state correctly on both.

Tests: `tests/test_website_status.py` (categorize_identity, tiebreak/ambiguity, the
`compute_verdict` behavior fixes).

Remaining: per-org fit scoring (Layer D, coupled with the scoring-multitenancy rework) is not
yet implemented — see `docs/code-review/web-pipeline-holistic-rework.md` §5/§10 for the plan.

### Website verdict — does this company have a website, and how many?

`app/services/enrichment/website_status.py` computes the company-level verdict
(`companies.website_status`) plus a distinct-website count (`companies.website_count`).
No API cost. This replaces the old behaviour of forcing the top-scored search result
into `website_url` regardless of quality — and, since identity rework phase 3, is
**crawl-only**: a search result alone never produces a positive verdict (see below).

- **Domain buckets** (`scoring.classify_domain`): each result URL → `own` / `social` /
  `directory` / `news` / `none`, reusing the existing directory/social/news domain sets.
- **Directory-domain blocking** (`scoring._is_directory_domain`): `_DIRECTORY_DOMAINS` (hardcoded frozenset) is always unioned with DB overrides from `crud.get_active_google_directory_domains` — never replaced. Ensures both sets are active simultaneously.
- **Content-based directory detection** (`scoring.is_directory_page(html, url)`): runs at crawl-extraction time in `handle_web_extract`. Checks URL path patterns, "claim this listing" phrases, title suffix patterns (Branchenbuch/Verzeichnis/Vergleich), and "similar companies" phrasing — three tiers, any match rejects the candidate via `crawler_crud.reject_url_candidate`. This is the *post-crawl* net; the pre-crawl filter below is what keeps the crawl from happening at all.
- **Pre-crawl directory filter** (`crawler_crud.get_effective_crawl_blocklist(db)`, 2026-07-31): returns `CRAWL_BLOCKED_DOMAINS ∪ {approved rows in directory_crawl_domains}`, cached 300 s behind a lock (`invalidate_crawl_blocklist_cache()` clears it; the admin approve/reject/delete routes call it for their own pod, others pick it up via the TTL). Used by `select_best_candidate`, `get_next_crawlable_candidate`, and the `web_crawl_single` fallback guard.

  **Why the union is required:** the hardcoded `DIRECTORY_CRAWL_DOMAINS` seed list *is* a strict subset of `CRAWL_BLOCKED_DOMAINS`, so seeded directories were never selectable as company websites. But `handle_discover_directory_domains` inserts a domain only when it is **not** already in `CRAWL_BLOCKED_DOMAINS` — so every domain discovery has ever added is, by construction, absent from the blocklist. Before this change, approving one for directory crawling left it fully eligible as a company website: full multi-page crawl + S3 uploads on a directory listing, post-hoc `is_directory_page` rejection, then a wasted fallback crawl on the next candidate. The gap was the steady state for discovered domains, not an edge case.

  Only `status='approved'` rows are merged. `pending_review` rows are unreviewed guesses, and wrongly blocking one would silently cost a real company site.
- **SEO Visibility Score** (`scoring.compute_seo_visibility_score(organic_position, *, ads_count, has_local_pack, has_knowledge_graph) → int | None`): measures actual Google search findability, distinct from `web_score` (URL-selection confidence). Formula: `100 − (rank−1)×8 − ads×12 − 5×(local_pack) − 5×(knowledge_graph)`, clamped [0,100]; `None` when company site not in organic results.
  - Shared helpers: `find_organic_position(results, url) → int | None` (1-based rank of `url` domain in stored results); `extract_serp_features(google_search_full_raw) → (ads_count, has_local_pack, has_knowledge_graph)` (handles Serper `ads`/`places`/`knowledgeGraph` and ScrapingDog `paid_results`/`local_results`/`knowledge_graph` naming).
  - Stored as `companies.seo_visibility_score` + `seo_visibility_computed_at` (migration `0113`).
  - Persisted on demand by `GET /api/v1/companies/{id}/serp-analysis`; backfilled in bulk by `recalculate_google_scores` (no new API calls).
  - Displayed in the Search Presence card on the company profile (green ≥70, amber 40–69, red <40).
- **The only verdict path is `compute_verdict`** (called from `handle_web_extract`,
  the `recompute_website_status` job, and — since phase 3 — `web_enrichment.py`'s
  `_search_verdict_fields`, i.e. `enrich_company_website`/`rescore_from_stored_results`/
  `recalculate_google_scores` too). It reads every `company_web_extract` row (joined to
  candidate URL, including `purpose_sim`) and tiers each via `_extract_tier()`:
  - `uid_matches_zefix=True` or `name_address_verified` → **verified**
  - `uid_matches_zefix=False` → discarded (site belongs to another company)
  - else: purpose-semantic boost applied first (`purpose_sim > 0.30` → linear up to +0.15 on `confidence`), then confidence ≥ thresholds → **confirmed** / **likely** (`website_confirmed_confidence` default 0.65, `website_likely_confidence` 0.45)
  - no `company_web_extract` rows at all (never crawled) → `Verdict(None, None, 0, None)` —
    **unknown**, regardless of search score. No search-snippet fallback exists anymore.
- **`classify_search_results`** still exists (scores search results into
  verified/confirmed/likely/social_only/directory_only/none by domain bucket + score vs
  `website_confirmed_search_score`/`website_likely_search_score`), but per the design
  doc's §3.4 it's demoted to a **crawl-queue-ordering helper** only — not called by
  `compute_verdict` or `_search_verdict_fields`, and its output is never persisted to
  `companies.website_status`/`website_url`.
- **`web_score` from crawl confidence:** `compute_verdict` returns `web_score = round(best_confidence × 100)` from the winning extract (or `0` for a crawled-but-`NONE` verdict, or `None` for never-crawled). `handle_web_extract`, `recompute_website_status`, and now `enrich_company_website`/the rescore paths all write this to `companies.web_score` and recompute `combined_score`. The old ±delta (`adjust_web_score_for_extraction`) is kept for backward-compat but no longer called on the main path.
- **Purpose-site semantic similarity (`purpose_sim`):** `company_web_extract.purpose_sim` (float 0–1) stores cosine similarity between the Zefix `purpose` embedding and the crawled site description/keywords embedding. Computed by ML-worker job `enrich_web_purpose_sim` (in `noga.py`). Migration `0110`. Trigger via `POST /api/v1/admin/jobs/crawler/enrich-purpose-sim` (UI: crawler admin page).
- **Impressum/contact page bonus:** `resolve_company_extract` accepts `page_types: list[str] | None`. If `impressum` or `contact` is present in the fetched pages for a candidate, one extra signal is added to the base coverage count, raising base confidence.
- **Verdict ladder:** `verified` › `confirmed` › `likely` › `social_only` ›
  `directory_only` › `none` (NULL = unknown / not yet crawled). `website_url` is set
  only for the positive verdicts (verified/confirmed/likely); otherwise NULL. All six
  named statuses now imply an actual crawl happened — a company that's only been
  searched, never crawled, always shows NULL (frontend: `CoverageItem`/badges reading
  `company.website_url`/`website_status` in `company-detail-client.tsx` naturally read
  as "no website" until a crawl confirms one — this was the phase-3 fix).
- **Multiple websites:** `website_count` = distinct root domains among verified+confirmed
  extracts (≥2 ⇒ company has multiple genuine sites). Surfaced as a badge on the company
  table (`Site status` column) and the detail page Website tab.
- **Re-enrichment guard:** the batch `only_missing_website` filter anti-joins against
  `company_search_results` (has this company ever been searched at all — not
  `website_url`/`website_status`), so companies legitimately gated to NULL by the
  crawl-only verdict aren't re-searched forever just because they show no website.
- **Thresholds** are DB-configurable `AppSetting`s (`website_*`); `recompute_website_status`
  re-derives all verdicts after tuning them or to backfill.

### Job params — web_crawl_http and web_crawl_playwright

Both share: `batch_size`, `canton`, `max_pages`, `rate_limit_delay`, `order_by`, `limit`, `crawl_concurrency`.

`order_by` values (applied via `claim_crawl_batch`):
- `company_id_asc` — default, stable ordering
- `last_crawled_asc` — oldest crawled first (useful for refresh runs)
- `flex_score_desc` — highest flex score first (needs JOIN companies)
- `combined_score_desc` — highest combined score first (needs JOIN companies)

`limit` — stop after this many companies total; batch size is clamped to `min(batch_size, limit - done)`.

Playwright-only: `rerun: bool` — calls `crawler_crud.reset_playwright_crawled()` before starting, which resets `crawl_status IN (crawled, bot_blocked, http_error, timeout, no_content)` rows with `tier=playwright` back to `pending`.

**`crawl_concurrency`** (2026-07-30, throughput fix): number of companies crawled concurrently *within one job*, via `asyncio.gather` bounded by a `Semaphore` in `_crawl_targets_concurrently` (`app/services/jobs/job_handlers/web_crawl.py`). Previously `_run_crawl_batch` called `asyncio.run()` once per company inside a plain `for` loop — one company crawled at a time, full stop, regardless of `JOB_WORKER_CONCURRENCY` or pod count. That serialization (not dedup, not pod scheduling) was the actual throughput ceiling: `web_crawl_http`/`web_crawl_playwright` are already in `NO_DEDUP` (SKIP LOCKED makes concurrent job instances safe — see "Job dedup" below), so multiple pods/threads were already able to run separate job instances, but each instance itself did one company at a time.

Safe to parallelize because `crawl_fn` (`crawl_company_http` / `crawl_company_playwright`) is pure async I/O and `rate_limit()` (`crawler_common.py`) keys its delay per-domain, not globally — concurrent companies (different domains) never block each other. DB writes stay untouched: within a claimed batch, candidates are resolved and crawled concurrently first, then results are written to `ctx.db` strictly sequentially in the one worker thread (SQLAlchemy sessions aren't safe for concurrent use).

Defaults: `crawl_concurrency=40` for HTTP (network-bound, cheap per slot), `crawl_concurrency=2` for Playwright (each slot launches a full Chromium instance — kept low to avoid OOM on the ML worker pod). Exposed as a "Concurrency" field on both crawl trigger forms in `frontend/.../collection/collection-client.tsx`.

**Off-loop page processing** (2026-07-31, throughput fix): `_make_page_result` (both `crawler_http.py` and `crawler_playwright.py`) is blocking work — an lxml parse plus media/word counting and language detection, then a blocking boto3 S3 PUT. It used to run inline on the crawl coroutine, which stalled the event loop and with it every other company being crawled concurrently on it. That is why raising `crawl_concurrency` past ~10 previously did nothing: the semaphore admitted more sites, but they queued behind the loop's own blocking work. Measured effect was ~35 companies/min at `crawl_concurrency=10`, i.e. roughly one company per slot per 17 s against a ~7-request page budget.

Both crawlers now call it via `crawler_common.run_in_page_executor(fn, *args)`, backed by a module-level `ThreadPoolExecutor` sized by `CRAWL_PAGE_WORKERS` (default 32). Two ceilings, deliberately separate:
- `crawl_concurrency` (per job) bounds how many **sites are open** at once.
- `CRAWL_PAGE_WORKERS` (per **pod**, shared by every crawl job on it) bounds how much **CPU + S3 work** is in flight.

Raise them together; a large `crawl_concurrency` against a small page pool just relocates the queue. Give the pod CPU to match `CRAWL_PAGE_WORKERS`.

Paired with this, `s3_client._client()` now caches a single process-wide boto3 client behind a lock (`reset_client()` clears it). It previously constructed a fresh client on **every** call — each one loading the botocore JSON service model from disk, 50–300 ms before any byte moved — so a 5-page crawl paid that five times, on the event loop. `max_pool_connections=64` keeps the connection pool from re-serialising what the thread pool parallelised (botocore's default is 10).

**Correctness note — cancellation granularity:** the first version of this change used `asyncio.gather` (wait for the whole batch), which meant `ctx.assert_not_cancelled()` was only checked once per batch, after the single slowest task in it finished (up to `company_timeout`). That's the same check `_run_job` relies on to detect a job has been evicted by the stale-job recovery sweep (`requeue_interrupted_jobs` — see "Job recovery" below): if a sibling pod requeues+reclaims this job_run (heartbeat looked stale) and this execution doesn't notice for up to `company_timeout` seconds, both executions run concurrently, each with independent local `stats`/`done` counters, both writing progress into the same job row (visible as two interleaved, conflicting progress series in the job log). Fixed by switching to `asyncio.as_completed` — results (and the cancellation check) are handled as each individual crawl finishes, restoring the original per-company checkpoint granularity.

For maximum throughput, combine with triggering multiple concurrent `web_crawl_http` job instances (dedup is off for this type) so more than one pod/thread is active at once — `crawl_concurrency` and job-instance count are independent, multiplicative levers.

### Two-phase crawling (2026-07-31)

The crawl is split at the point identity is decided, because everything the
confidence ladder in `resolve_company_extract` reads lives on two pages.

| | Phase A — identity | Phase B — content |
|---|---|---|
| Job | `web_crawl_http` (+ `web_crawl_playwright` for js_required) | `web_crawl_content` |
| Crawl fn | `crawl_company_http` (`max_pages=3`) | `crawl_site_full` (`max_pages=60`, BFS) |
| Pages | homepage + impressum + contact | the entire website |
| Feeds | `uid_matches_zefix`, address, zone-weighted name, `confidence`, verdict, `web_score` | `service_keywords`, `persons_struct`, `services_struct`, `about_text` |
| Runs for | every company, every candidate | identity-confirmed companies only |
| Ordering | `company_id_asc` | `combined_score_desc` — full-site crawls are expensive, spend them on the best leads first |

`company_crawl_state.crawl_phase` (`identity | content | done`, migration `0129`)
is claimed on alongside `crawl_status`, so the two jobs take disjoint rows and
run concurrently. `crawl_status` is *per-phase*: a row can be `pending` in phase
`content` having already been `crawled` in phase `identity`.

**Why the split.** Phase A fetches 3 pages instead of 7 for every candidate,
and with a ~40% fallback rate most companies are crawled more than once before
identity settles — so the saving multiplies across the fallback chain rather
than applying once. Content pages are never spent on a candidate that turns out
to belong to a different company. Identity coverage across the whole corpus also
lands far sooner, with content streaming in behind it.

**Transitions** (`app/crud/crawler.py`):
- `advance_to_content_phase(db, company_id)` — called from `handle_web_extract`
  when identity is confirmed (UID match, or no UID with `confidence >= 0.65`).
  Its `WHERE crawl_phase = 'identity'` makes it idempotent and race-safe: a
  re-extract on an already-advanced company cannot re-queue a finished content
  crawl. Returns whether it performed the transition.
- `mark_phase_done(db, company_id)` — set when phase B finishes, and when phase A
  exhausts every candidate without confirming (otherwise those rows sit pending
  in the identity phase forever).

**Phase-gating in `handle_web_extract`.** Quarantine, fallback and advancement
all run only while `crawl_phase` is `identity`. After phase B the same extractor
re-runs over a much larger page set; without the gate a confirmed company would
re-enter the fallback chain and re-queue crawls it has already passed.

**Phase B never calls `delete_web_pages_for_company`.** It uses
`delete_content_pages_for_company`, which preserves `homepage` + `IDENTITY_PAGE_TYPES`
rows and all inventory-only rows. Deleting them would destroy the evidence the
company's own `website_status` was computed from, and the frontier seed with it.

**Frontier, and why phase B needs no discovery requests.** Phase A's sitemap pass
already wrote every classified-but-unfetched URL via `save_page_inventory`
(`crawled=false`). `_make_content_target_kwargs` reads those in the job thread
(never the event loop — the DB session is not concurrency-safe) and hands them to
`crawl_site_full` as `seed_urls`, together with `visited_urls` so phase A's pages
are never re-fetched. Only when the inventory is empty (no usable sitemap) does
phase B re-fetch the homepage, purely to expand the frontier from nav links — it
is not saved, since phase A's copy is what the extract row depends on.

**Bounds.** A single WordPress or WooCommerce site can expand without limit, so
`crawl_site_full` is bounded on three axes: `max_pages` (hard cap),
`max_depth` (link distance from the seed set), and `is_crawlable_page_url`, which
drops off-host links, binary/asset extensions, and crawl traps (pagination, tag
and date archives, feeds, search/filter query params, carts, logins).

**Failure semantics differ from phase A**: partial success is success. A page that
404s or times out is skipped; `failure_status` is only set when the crawl produced
no pages at all. `crawl_site_full` does no bot/JS detection, so phase B never
escalates to Playwright — which is what keeps a `tier='playwright'` +
`crawl_phase='content'` row from being stranded with no worker able to claim it.

**Transition backfill** (migration `0129`) maps the existing corpus onto the new
state machine:

| Existing state | Becomes |
|---|---|
| Crawled **and** ingested (`company_web_extract` row), confirmed | phase A complete → `crawl_phase='content'`, `crawl_status='pending'` (queued for phase B) |
| Crawled and ingested, not confirmed | `crawl_phase='identity'`, status untouched — the existing fallback machinery still governs them |
| Crawled but **never** ingested | neither phase complete → reset to pending phase A and re-ingested by the new pipeline |
| URL candidates only | already the default — `identity` / `pending` |

**Frontend — the "confirmed URL, content pending" state.** `GET /{id}/web-extract`
returns `crawl_phase` + `crawl_status` from `company_crawl_state`. `WebsitePanel`
treats `crawl_phase === 'content'` as its own UI state: a blue banner plus a
"Content pending" chip in the source strip, with a direct link out to the
verified site. The phase-B-populated fields (`aboutText`, `keywords`,
`servicesFound`, `people`) render "Not collected yet" instead of an em-dash —
an empty field there means *not gathered*, not *looked and found nothing*, and
a bare dash reads as a negative finding. `description` and `languages` keep the
dash: they come from the homepage, which phase A already has. The existing
`noStructuredData` warning is suppressed while content is pending so the two
states can't both claim the panel.

**`privacy` was dropped from `_FETCH_WORTHY_TYPES`.** It sat at priority 2 but is
absent from `crawler_extract._TEXT_PAGES`, so its main text was never extracted —
it only ever contributed the unconditional email/phone/social/UID regexes, all of
which impressum already supplies. It was consuming a budget slot ahead of
contact/about. Still inventoried, just not fetched.

### URL selection

- **Automatic (new):** `run_batch_collect` upserts candidates immediately after each Google enrich, via the shared `_sync_url_candidates` helper — unconditionally, regardless of the company-level verdict (identity rework phase 3 decoupled candidate creation from the verdict; see "Website verdict" below). `select_best_candidate` only fires if no candidate is selected, so re-enriching a company never demotes a manually-chosen or already-crawled URL.
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

### Crawler security hardening (2026-07-24)

The crawler fetches attacker-controlled content (arbitrary company websites) across three
fetch paths (httpx, curl_cffi fallback, Playwright browser) — hardened against decompression
bombs and SSRF:

- **Decompression ("zip") bomb defense** — httpx's automatic decoder (`Response.aiter_bytes`)
  calls `decompressor.decompress(data)` with **no output-size bound**: a single raw network
  chunk from a crafted gzip/deflate body can materialize gigabytes in one call before any size
  check runs (verified: a 200 KB crafted gzip body decompresses to 200 MB in one `decompress()`
  call). Fixed by `crawler_common.read_bounded_body()`, which reads **raw** (undecoded) bytes
  via `resp.aiter_raw()` and decompresses in small increments using
  `zlib.decompressobj().decompress(chunk, max_length=remaining)` — a hard per-call output cap
  stdlib zlib guarantees, so cumulative decoded output can never exceed `MAX_PAGE_BYTES`
  regardless of compression ratio. Used by `crawler_http._fetch` and `crawler_sitemap._get_text`
  (previously read `resp.text` fully, unbounded, before truncating). Only `gzip`/`deflate` are
  accepted (`BOUNDED_ACCEPT_ENCODING = "gzip, deflate"`, sent instead of a real browser's
  `br, zstd` too) — `brotli`/`zstandard`'s Python bindings expose no per-call output bound, so
  those encodings are refused (`DecompressionBombError`, fail closed) rather than decoded
  unbounded. Regression tests: `tests/test_crawler_decompression_bomb.py` (proves a real 200 MB
  gzip bomb is bounded to `MAX_PAGE_BYTES`, and documents the unbounded call it replaces).
  - `_fetch_curl_impersonate` (bot-block fallback): libcurl decodes transparently in C with no
    raw/undecoded access point, so the same technique doesn't apply — mitigated by requesting
    `accept_encoding=BOUNDED_ACCEPT_ENCODING` (asks compliant servers not to send br) plus a
    cumulative-bytes cap on the streamed, already-decoded body. Weaker than the httpx path;
    acceptable since this is a secondary fallback tier.
  - Playwright: `page.content()` fully serializes the DOM over CDP before any truncation could
    apply — a "DOM bomb" (JS generating millions of nodes) would transfer the whole string.
    `_get_bounded_html()` first checks `document.documentElement.outerHTML.length` via a cheap
    `page.evaluate()` call, and only pulls a bounded prefix over the wire when it's large.
- **SSRF** — every fetch path resolves the target host and refuses private/loopback/link-local
  (incl. cloud metadata `169.254.169.254`)/reserved/multicast addresses, via shared
  `crawler_common.resolve_is_public()` / `ssrf_request_guard()` (moved here from `crawler_http`
  so `crawler_sitemap` can use it too without a circular import — `crawler_http` lazily imports
  `crawler_sitemap` inside a function body):
  - httpx (`crawler_http._client`, `crawler_sitemap.discover_site_overview`): `event_hooks`
    fire per redirect hop, so a redirect to an internal address is refused mid-chain.
  - curl_cffi (`_fetch_curl_impersonate`): libcurl follows redirects internally with no
    interception point, so `allow_redirects=False` is set and redirects are followed manually
    in a bounded loop (`_CURL_MAX_REDIRECTS = 5`), re-checking `resolve_is_public()` before
    each hop — same guarantee as the httpx path, reimplemented at the application layer.
  - Playwright (`_guard_and_filter_resources`, extends the existing image/media/font blocker):
    since Playwright renders real JS, a malicious page's own script (fetch/XHR/further
    navigation) could otherwise pivot to internal services from the crawler's network
    namespace — every request the page makes, including the top-level navigation itself, is
    resolved and aborted if non-public.
  - Previously **`_fetch_curl_impersonate` had no SSRF guard at all** (a full bypass of the
    protection the primary httpx path already had) and Playwright had none either — both closed
    by this pass. `tests/test_crawler_ssrf.py` covers the shared `_ip_blocked`/guard logic.
- **XSS** — verified the frontend never renders raw crawled HTML (no `dangerouslySetInnerHTML`
  / `srcDoc` of crawler content anywhere); extracted fields only ever reach the UI as React text
  content (auto-escaped). Raw HTML is stored to S3 and only ever parsed server-side
  (BeautifulSoup/trafilatura — no JS execution) — Playwright is the only path that executes
  page JS, and it runs in a disposable headless Chromium container, not the app process.

### Search-result data extraction — `company_search_results` (2026-07-24)

Company table normalization (ROADMAP): `google_search_results_raw`, `google_search_full_raw`,
`google_search_params`, and `website_checked_at` moved off `companies` into their own table,
`company_search_results` (PK `company_id`, one row per company — same pattern as
`company_crawl_state`). This is search-**provider** data, not company master data; it's a
global fact (same tier as `company_web_extract`/`company_web_page`), not an org-scoped overlay.
The derived **verdict** fields (`website_url`, `web_score`, `website_status`, `website_count`,
`social_media_only`) stay on `companies` for now — those are addressed separately by the
scoring/multi-tenancy rework.

- **Storage upgrade**: the old columns were `Text` holding JSON-encoded strings
  (`json.dumps`/`json.loads` at every call site). The new columns are native `JSON`
  (`results_raw: list[dict]`, `full_raw: dict`, `params: dict`) — no more manual (de)serialization.
  `crud/company_search_result.py`: `get_search_result`, `upsert_search_result` (portable
  get-or-create, not Postgres `ON CONFLICT` — kept SQLite-testable), `bulk_get_search_results`.
- **Read path**: `_overlay()` (`app/api/routes/companies/_shared.py`) now also merges
  `website_checked_at`/`google_search_results_raw` from a `CompanySearchResult` row (renamed
  wire field kept for minimal frontend churn; type changed `str→list[dict]`), alongside its
  existing `OrgCompanyState` overlay. `_bulk_search_results()` mirrors `_bulk_org_states()` for
  list views (one query, no N+1). All 4 call sites (search.py demo, list.py bulk, detail.py ×2)
  updated.
- **Filters/sort moved off `Company` columns**: `crud/company.py::list_companies`'s
  `google_searched` filter (yes/no/no_result) now uses a correlated `EXISTS` against
  `company_search_results` instead of `Company.website_checked_at`; the `website_checked_at`
  sort key is special-cased (outer-joins `CompanySearchResult` only when that sort is actually
  requested, keeping the common no-sort case join-free at 700k rows) rather than living in the
  static `_SORT_MAP` (which — since it's built at import time from `Company.<attr>` — would have
  **crashed the app at startup** once the column was removed, had this not been special-cased).
  `get_company_stats()`'s `searched`/`searches_today` now count `company_search_results` rows.
- **Job handlers** (`web_crawl.py`): `handle_web_extract`'s per-batch meta query LEFT JOINs
  `company_search_results` instead of selecting a `Company` column (same column position, so
  downstream `row[N]` indexing is unchanged — only `json.loads(row[6])` → direct list use).
  `handle_recompute_website_status` and `handle_web_url_populate` similarly switched their raw
  SQL to JOIN the new table. `handle_web_crawl_single` fetches via `csr_crud.get_search_result`.
- **Also cleaned up while touching this area**: `OrgCompanyState`'s Google-scoring shadow columns
  (`website_url`/`web_score`/`google_search_results_raw`/`website_checked_at`/
  `social_media_only`/`website_status`/`website_count`) were confirmed 100% dead — the intended
  writer `update_org_google_results` had zero callers anywhere, and no code read these fields off
  `OrgCompanyState` either (`_overlay()` only ever merged `_ORG_FIELDS`, which never included
  them). Dropped the columns, the dead CRUD function, and the matching (also-dead) fields from
  the `OrgStateOut` API schema and the frontend `OrgCompanyState` TS type — this was previously
  flagged as a dead-code risk in `docs/code-review/scoring-multitenancy-rework.md` and as a
  "Cleanup" step of that rework; done now since the area was already being touched.
- Migration `0122`: creates `company_search_results`, backfills from the old `companies`
  columns (`::jsonb` cast), drops the 4 `companies` columns + the 7 dead `org_company_state`
  columns in one pass (not phased/deferred — this data is being relocated wholesale, not
  gradually cut over like the score tables).

Tests: `tests/test_company_search_result.py` (upsert/get/bulk-get, `google_searched` filter,
`website_checked_at` sort, searched-count).

### Scoring & multi-tenancy rework — phases 1-4 implemented (2026-07-24)

Per `docs/code-review/scoring-multitenancy-rework.md`: flex/web/ai/combined scores were global
on `companies`, so one org's rescore overwrote what every other org saw. This phase adds the
per-scope score tables, a materialization job, and config resolution — **read-cutover is
partial** (detail view + list items; map/CSV/sort are not yet cut over — see "Not done" below).
Nothing currently breaks: the migration backfills an org-default `company_score` row for every
org from the *then-current* global values, so the overlay is a no-op divergence until an org
customizes its `scoring_*` config and reruns `rescore_scope` — `companies.flex_score/web_score/
combined_score/ai_score` are still written by all existing code paths, unchanged.

**New tables** (migration `0123`):
- `company_score(org_id, user_id NULL|N, company_id)` — unique per scope. `user_id IS NULL` is
  the org-default row; `user_id = N` exists only once that user has overridden ≥1 `scoring_*`
  key. Holds `flex_score`, `web_score`, `combined_score`. Index `(org_id, user_id,
  combined_score)` for sort/filter.
- `org_company_ai(org_id, company_id)` — org-shared AI result: `ai_score` (promoted, sortable)
  + `ai_data` JSONB (category/freeform now; future per-company summaries or named prompt-scores
  need no migration). AI is computed once per org and reused by every member, never per-user.
- Backfill: both tables seeded for **every existing org** via a `organizations × companies`
  cross join in the migration (fine at current org counts; re-check row estimates before
  re-running if org count has grown a lot — this is a one-time DB-side bulk `INSERT...SELECT`,
  not an app-level 700k-row load, so it doesn't violate the batching rule, but it does scale
  with `orgs × companies`).

**Config resolution** (`app/services/scoring/config_resolution.py`) — reuses infrastructure that
already existed for other settings, no new plumbing:
- `effective_config(db, org_id, user_id=None)` — org's effective `scoring_*` settings (via the
  existing `AppSetting` → `OrgSetting` → base-org fallback chain, `crud.app_setting
  .get_effective_settings_batch`) with the user's own `UserOrgSetting` overrides layered on top
  (new `app/crud/user_org_setting.py`; `workspace/_shared.py`'s per-user setting helpers now
  delegate to it instead of duplicating the query).
- `resolve_scope(db, org_id, user_id)` — returns `user_id` only if that user has recorded ≥1
  `scoring_*` override for this org, else `None` (org-default). One indexed `(org_id, user_id)`
  lookup per read, no per-row COALESCE across scopes, per the design doc's goal.

**Materialization job** (`app/services/scoring/rescore_scope.py`, job type `rescore_scope`,
handler `job_handlers/rescore.py`) — two-pass chunked batch (same shape as the existing
`geocoding_pipeline.recalculate_flex_scores`, which this is modeled on): pass 1 computes each
company's raw flex score via `compute_flex_score_breakdown(config=effective_config(...))`
unchanged, in id-keyset batches; pass 2 population-wide min-max normalizes (flex_score requires
seeing the whole scope's raw-score distribution, so can't be finalized company-by-company) and
upserts `company_score` rows. `web_score` is copied from the global `Company.web_score` (**not**
recomputed per org — there is no per-org web-scoring lever yet; the website identity verdict
uses crawl confidence + global `AppSetting` thresholds, not `scoring_*` config. A known,
documented simplification, not a placeholder bug). `ai_score` is read from `org_company_ai`
(never recomputed here — that's `claude_classify`'s job). `combined_score` via the existing
`Company.compute_combined_score(...)`, unchanged.
- Dedup key: `rescore_scope:{org_id}:{user_id or '-'}` — **not** the job-type default
  `"{type}:{org_id}"`, added as a special case in `job_worker._compute_dedup_key` (otherwise two
  different users in the same org rescoring their own scope would collide and one would be
  silently skipped as "already running").
- Trigger: `POST /api/v1/jobs/scoring/rescore-scope` (any authenticated org member — resolves to
  their own scope automatically). Shows up in the existing generic Jobs UI with no bespoke
  frontend needed (same progress/event/cancel machinery every job type already gets).

**Read-path cutover — done for `_overlay()`** (`app/api/routes/companies/_shared.py`): `_overlay`
now takes an optional `score: CompanyScore` and overrides `flex_score`/`web_score`/
`combined_score` on the `CompanyRead` when present; new `_bulk_scores()` mirrors
`_bulk_org_states()`/`_bulk_search_results()` (one query, resolves scope via `resolve_scope`
first). Wired into all 4 call sites (list.py bulk, detail.py ×2). Falls back to the global
`Company` columns when no scope is materialized (org context is superadmin/absent, or
`rescore_scope` hasn't run) — identical to pre-rework behavior.

**Not done — flagged, not attempted, in this pass:**
- **`map.py` and `csv_export.py` cutover** — both use `min_web_score`/`max_web_score`/etc. as
  direct `Company` column **filters** (not just display), and `map.py` additionally computes a
  weighted-average SQL expression directly against `CompanyModel.ai_score/web_score/flex_score`
  for marker clustering. Cutting these over means joining `company_score` into filter/sort/
  clustering queries used by the live map view and bulk CSV exports — higher risk, needs
  dedicated verification (the design doc itself calls out "verify sort parity against the
  pre-cutover global ordering" as a required check before this is safe).
- **`list_companies`'s own sort/filter** (`crud/company.py`) — score-based filters
  (`min/max_web_score` etc.) and the `combined_score` sort expression still read `Company`
  columns directly, not `company_score`. Only the *displayed* values are cut over (via
  `_overlay`), not what's filtered/sorted on. Same risk profile as map/CSV above.
- **Phase 5 (config UI)** — no settings page yet exposing per-user `scoring_*` overrides or a
  rescore-scope trigger button; the backend (`UserOrgSetting` overrides + the trigger endpoint)
  is ready for one. Per the CLAUDE.md frontend-wiring rule, building this needs a UI/UX decision
  on placement and interaction that wasn't specified.
- **Phase 6 (cleanup)** — `companies.flex_score/web_score/combined_score/ai_score` and
  `OrgCompanyState`'s (already-dead, already-dropped) shadow columns are the *old* dual-write
  columns being superseded; they are deliberately **not** dropped or frozen yet. Retiring
  `web_score` from `combined_score` (per the web-identity-rework doc) also lands here, later,
  together — not attempted.

Tests: `tests/test_rescore_scope.py` (config resolution, dedup-independent per-scope
materialization), `tests/test_company_overlay_scores.py` (overlay fallback + override).

### Key CRUD functions — `app/crud/crawler.py`

| Function | Purpose |
|---|---|
| `claim_crawl_batch(db, tier, batch_size, canton, order_by)` | SKIP LOCKED claim; order_by controls sort |
| `reset_playwright_crawled(db, canton)` | Reset crawled/failed playwright rows to pending for rerun |
| `_CRAWL_ORDER_BY` | Dict mapping order_by string → SQL ORDER BY clause |
| `release_in_progress_states(db, tier)` | Crash recovery — releases stuck in_progress rows |
| `save_page_inventory(db, company_id, entries, already_saved_urls)` | Inserts inventory-only (`crawled=FALSE`) rows from `classify_all_urls`, skipping URLs already fetched this run |
| `get_page_inventory(db, company_id)` | Merged fetched+inventory page list, fetched first then by `priority` — backs `GET /{id}/web-extract`'s `pages` |

### Tables

| Table | Purpose |
|---|---|
| `company_url_candidates` | Serper/ScrapingDog URL candidates; one selected per company |
| `company_crawl_state` | Per-company crawl status, tier, bot flags, re-crawl scheduling |
| `company_web_pages` | Site page inventory: fetched pages (`crawled=TRUE`, S3 key for raw HTML, `needs_extraction` flag) + inventory-only rows (`crawled=FALSE`, sitemap-discovered but not fetched). `discovered_via` (sitemap/robots/nav/homepage), `priority` |
| `company_web_extract` | PK `(company_id, url_candidate_id)` — one row per company+URL crawled; `get_best_web_extract()` picks highest-confidence row for display/scoring. Includes `uid_matches_zefix` (verification flag), `name_address_verified` (migration `0100`, fallback verification when no UID found), `persons` (impressum management names + spaCy NER names), and `purpose_sim` (float, migration `0110`; cosine similarity between Zefix purpose embedding and site content, used as a confidence boost in `_extract_tier()`) |
| `company_search_results` | PK `company_id` — raw Google/ScrapingDog search data (migration `0122`, moved off `companies`): `provider`, `results_raw` (scored+sorted list), `full_raw` (complete provider response), `params` (search params sent), `searched_at`. All native `JSON` columns. |

### Key files

| File | Purpose |
|---|---|
| `app/services/enrichment/crawler_common.py` | Shared utilities: browser profiles + client hints, bot/JS detection, nav + sitemap subpage discovery, media counting |
| `app/services/enrichment/crawler_sitemap.py` | robots.txt + sitemap.xml discovery (URLs + crawl-delay); best-effort |
| `app/services/enrichment/crawler_http.py` | httpx crawler (HTTP/2, client hints, curl_cffi impersonation fallback) |
| `app/services/enrichment/crawler_playwright.py` | Playwright crawler (lazy import; resource-blocking, optional Chrome channel) |
| `app/services/enrichment/crawler_extract.py` | Deterministic structured-data extractor (trafilatura + regex/schema.org/phonenumbers) |
| `app/services/jobs/job_handlers/web_crawl.py` | Job handlers: `_run_crawl_batch` shared loop, `handle_web_extract`, per-type handlers |
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
(in `app/services/scoring/scoring.py`) nudges the Serper-based `web_score` using on-site verification
found during extraction:
- UID found and matches Zefix → **+40** (capped at 100)
- UID found but does not match Zefix → **−50** (floored at 0)
- No UID, but `name_address_verified` → **+20**

`base_web_score` must always be the **raw, un-adjusted** score
(`google_search_results_raw[0]["score"]`), never the currently-stored `company.web_score` —
this keeps repeated re-extraction (e.g. via the `reextract` admin action, which re-runs
extraction without re-crawling) idempotent instead of compounding the adjustment each run.

Wired into `handle_web_extract` (`app/services/jobs/job_handlers/web_crawl.py`): after each
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

`compute_relevance_score(company)` in `app/services/scoring/scoring.py` now reads `company.web_score` when
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

`reclassify_noga` in `app/services/ml/noga_pipeline.py` now bypasses the `only_missing_noga` guard
for detected branch offices (`is_branch_office(company) == True`). Previously, a branch with a
stale/wrong NOGA code was skipped when `only_missing_noga=True` because it already had a code.
Now branches always re-run `apply_noga_classification`, which inherits the parent's NOGA if
available or clears it if the parent can't be resolved. The `branches_handled` counter tracks
this separately from normal classifications.

**Per-company commit failures used to kill the whole nightly run (2026-07-29):** `reclassify_noga`
and `reclassify_low_confidence_noga` call `crud.update_company()` per company inside the batch
loop, which commits immediately. When that commit failed (observed cause: a Postgres statement
timeout on one row's `UPDATE companies ...`), the `except Exception` handler then logged
`company.uid` — but the session was left in SQLAlchemy's "pending rollback" state by the failed
flush, so even that attribute access raised `PendingRollbackError`, escaped the handler uncaught,
and crashed the entire job (observed: died at company 32,000 of 2,865,376). Same root cause in
`zefix_import.py`'s purpose re-extraction loop (`company.id` instead of `.uid`). Fixed in all three
by calling `db.rollback()` as the first line of the `except` block, before touching `company` at
all — a general pattern any per-row-commit batch loop must follow.

**Third instance — `handle_recompute_website_status` had no per-row error handling at all
(2026-07-29):** running the backfill described above (needed to null out pre-phase-3 stale
verdicts) died at 174,000/302,329 on the same `QueryCanceled: statement timeout` class of error,
this time on the row's own `UPDATE companies SET website_url=...` (likely lock contention with a
concurrent crawler/extract job touching the same row) — with zero try/except around the per-row
loop, any such failure killed the whole run immediately. Naively wrapping it in try/except plus a
blanket `db.rollback()` would have silently discarded every already-`.update()`'d-but-not-yet-
committed row earlier in that same 1000-row batch (this handler commits once per batch, not per
row, for throughput). Fixed instead with a SAVEPOINT per row (`with ctx.db.begin_nested():` around
just the verdict compute + `.update()` call): on failure only that row's change rolls back to the
savepoint, the rest of the batch's pending updates and the outer transaction stay intact, and the
loop continues to the next company. `stats["errors"]` now surfaces in both the progress message and
final done-message.

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

### Branch office website inheritance (read-time only, not persisted)

Since branch offices are skipped from search/crawl (above), their `website_url` stays `NULL`
forever unless surfaced some other way. `get_company` (`app/api/routes/companies/detail.py`)
resolves the parent via `head_offices` and, when `is_branch_office(db_company)` is true and the
branch has never been searched (`website_checked_at IS NULL`), adds three response-only fields
that are **never written to the DB**: `inherited_website_url`, `inherited_website_source_company_id`,
`inherited_website_source_company_name`. The Website panel in `company-detail-client.tsx` shows
this as "Same as {parent}" with a link to the parent's own site and detail page. Because nothing
is persisted, a branch that later gets its own genuine site via manual "Find website" immediately
takes priority (the `website_checked_at IS NULL` guard stops firing). `combined_score` for branches
is intentionally **not** patched by this — it still computes off the branch's own `NULL` web_score;
fixing that is deferred to the broader scoring-multitenancy rework.

### SIMAP awards rollup for branch offices

`GET /api/v1/companies/{id}/simap-awards` now also includes awards won by the company's branch
offices (resolved via `branch_offices` JSON through `_resolve_branch_company_ids`), so a parent
company's detail page shows contracts won under a Zweigniederlassung's own UID. Each award in the
response carries `via_company_id` / `via_company_name`, non-null only when the award was won by a
branch rather than the company itself. `SimapPanel` / `AwardCard` render a "via {branch name}"
badge in that case. Branch detail pages are unaffected — they still only see their own awards.

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

`app/services/notifications/activity.py` — `log_activity(db, *, action, user_id, org_id, ...)`.

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
`reclassify_noga`'s upfront `COUNT(*)` (used for progress %) ORs an `IS NULL` check with a cross-column comparison (`noga_classified_at < updated_at - interval`), which can't use a btree index and forces a sequential scan. Under load this exceeds the engine-wide 30s `statement_timeout` (`app/database.py`), the job fails, and `_maybe_enqueue_noga_nightly` (`app/main.py`) re-enqueues it within the same 03:00–03:59 window since `has_noga_nightly_run_today` (`app/crud/job_run.py`) deliberately excludes failed/cancelled runs (so a crash doesn't block retry). Fix: `db.execute(text("SET LOCAL statement_timeout = '120000'"))` scoped to just that one COUNT statement in `reclassify_noga()` and `reclassify_low_confidence_noga()` (`app/services/ml/noga_pipeline.py`) — resets to 30s on next commit, doesn't affect interactive API requests.

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

Check `job_runs.pause_reason` first:

| `pause_reason` | Meaning |
|---|---|
| `user` | Someone paused it in the UI. **Stays paused by design** — press Resume. |
| `shutdown` | Paused by a pod restart. Auto-resumes on startup and on the 180 s sweep. |
| `preempt` | Crawler yielded to a queued ML job; re-queued immediately. |
| `NULL` | Pre-migration-0128 row; treated as auto-resumable. |

`crud.resume_all_paused_jobs()` auto-resumes only `shutdown`/`preempt`. If a
`shutdown`-paused job is not resuming, check `min_heartbeat_age_seconds` (the
rolling-deploy guard skips jobs whose heartbeat is younger than 120 s), then the
job event log for a preflight failure (missing API key, insufficient credits).

---

## 19. Background Job System — Design Evolution

This section records the architectural changes made to the job system and the
rationale behind each decision.

> **Historical note:** parts of this section were written when the job system had
> an optional Redis/RQ worker mode. That mode has been removed entirely — there is
> no `USE_RQ`, no `REDIS_URL` for jobs, and no `app/worker_entrypoint.py`. Every
> pod now runs the same DB-polling `_job_worker_loop` and is specialised by
> `JOB_TYPE_WHITELIST` / `JOB_TYPE_BLACKLIST`.

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
| No dedup (parallel-safe) | `csv_export`, `web_select_url`, `web_crawl_single`, `web_crawl_http`, `web_crawl_playwright`, `web_crawl_content` |

New job types are automatically deduplicated without any code change. Add to `NO_DEDUP` only if the type genuinely supports concurrent runs.

**Trade-offs:**
- **Advantage:** Safe to trigger multiple times from UI or multiple pods; no wasted credits or duplicate DB writes.
- **Disadvantage:** A paused job with a dedup key blocks re-enqueue until it is resumed or cancelled. Users who want a fresh run must cancel first.

**`NO_DEDUP` requires per-row claiming.** Only add a type to `NO_DEDUP` if it claims disjoint rows under lock (e.g. `claim_crawl_batch`'s `SELECT ... FOR UPDATE SKIP LOCKED`) — otherwise concurrent runs race over the same query results. `web_search_batch` (formerly named `batch`) used to be in `NO_DEDUP` despite querying companies by filter+`LIMIT` with no locking: two runs (e.g. a double-click before the button disabled, or a retry) would both select the same unprocessed companies and double-spend Serper/ScrapingDog credits, while the second job just sat `queued` behind the first under the default `JOB_WORKER_CONCURRENCY=1`. Fixed by removing it from `NO_DEDUP`, falling back to the default one-active-per-org key (effectively a global singleton here since the route is superadmin-only/catalog-wide with `org_id=None`).

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

**Restart cap + dedup self-heal (`app/crud/job_run.py` `MAX_RESTART_COUNT = 5`):** Every time `requeue_interrupted_jobs()` / `requeue_recent_abandoned_jobs()` re-queues a crashed job, `restart_count` increments. Once it exceeds `MAX_RESTART_COUNT`, the job is force-marked `failed` instead of re-queued (crash-loop protection).

Observed incident: a `web_crawl_playwright` job (dedup key = one active per org) reached `restart_count` in the hundreds while still showing `status="running"`, permanently blocking every new job of that type via the dedup check — the restart-cap kill path should have caught it far earlier but apparently didn't stick (exact cause not confirmed — suspect a lost-update race between pods' independent periodic sweeps, since `requeue_interrupted_jobs()`'s query has no row lock, unlike `atomic_claim_job()`). As a defense-in-depth fix (not a root-cause fix), `_enqueue_job_in_session()` (`app/services/jobs/job_worker.py`) now checks the restart count of any dedup-blocking job before returning it: if `restart_count > MAX_RESTART_COUNT`, it force-fails that job on the spot (via `crud.mark_failed`) and falls through to the normal enqueue path, so a runaway job can never wedge a job type shut indefinitely.

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
| `app/services/ml/embeddings.py` | Shared multilingual embedding backbone (singleton SentenceTransformer) |
| `app/services/ingestion/incremental_classify.py` | Classify newly imported companies inline (NOGA + clusters + language detection) |
| `app/services/ml/stopword_discovery.py` | 4-phase automated boilerplate/stopword discovery pipeline |

### Shared Embedding Backbone — `app/services/ml/embeddings.py`

All ML code that needs sentence embeddings shares a single lazy-loaded model instance:

```python
DEFAULT_MODEL = "paraphrase-multilingual-mpnet-base-v2"

embed_texts(texts, *, model_name, batch_size=256)  → np.ndarray  # (N, D) float32
embed_single(text, *, model_name)                  → np.ndarray  # (D,) float32
build_company_text(company)                        → str          # purpose + keywords
nearest_neighbours(query_vec, index_matrix, top_k) → list[(idx, cosine)]
```

`_get_model()` is `lru_cache(maxsize=1)` — the model loads once and stays in memory.

### Incremental Classification — `app/services/ingestion/incremental_classify.py`

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

### Boilerplate Pattern Analysis — `app/services/ml/boilerplate_analysis.py`

Regex-based, corpus-frequency discovery — the mechanism `purpose_clean` falls
back to for non-DE/FR languages, and the DE/FR pattern set this replaced as the
*primary* stripping method (see "Semantic Boilerplate Stripping" above). Still
useful for discovering new regex patterns for IT/other languages, or sentence-
level (non-truncating) patterns that don't fit the semantic method's structural
model.

`run_boilerplate_analysis` is a corpus-frequency job triggered from the Collection page (`scoring/analyze-boilerplate`).

**Parameters:**
| Param | Default | Description |
|---|---|---|
| `sample_limit` | 200 000 | Max purpose rows to scan (set to 700 000 for full corpus) |
| `min_match_count` | 500 | Min frequency for a sentence to become a candidate |
| `max_candidates` | 200 | Cap on candidates saved per run |
| `language_filter` | `None` | Filter companies by `purpose_language` (DE/FR/IT/EN); `None` = all |
| `truncate_mode` | `False` | When True, only inspect the **last sentence** of each purpose text — surfaces clause-openers that mark the start of the boilerplate tail; saved with `truncate=True` |

**Algorithm:**
1. Stream purpose texts in batches of 5 000. In `truncate_mode`, only the final sentence per text is kept.
2. Normalize (lowercase, collapse whitespace, strip leading articles).
3. Count sentence frequencies.
4. Sentences above `min_match_count` that are NOT matched by any existing active pattern are converted to a regex (`\s+`-relaxed escape) and saved as **inactive** `BoilerplatePattern` rows tagged `[AUTO]` or `[AUTO-TRUNC]` for admin review.

**Seed function:** `seed_multilang_boilerplate(db)` inserts the standard DE/FR/IT hand-curated patterns (including truncation triggers) on first run; safe to call repeatedly.

**Frontend:** Triggered from Collection → Boilerplate Analysis section with all params exposed. Reviewed and activated in Settings → Boilerplate Patterns (includes regex quick-reference panel).

---

### Automated Stopword Discovery — `app/services/ml/stopword_discovery.py`

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

**Title extraction** — `_TITLE_RE` in `sogc_person_extractor.py` strips a fixed DE/FR/IT keyword set (`Dr., Prof., lic., dipl., Ing., Dott., Avv., PD, Me, MLaw, MAS, MBA, BSc, MSc, Fürsprecher(in)`) out of `lastname`/`firstname` into a dedicated `title` column, present on both `sogc_person_appearances` and (migration `0126`) `sogc_person_entities`. `Dr.`/`lic.` also swallow a trailing academic qualifier (`iur.|oec.|med.|phil.|theol.|rer. publ.`) so it doesn't leak into the name either. `_extract_title()` captures *every* match, not just the first, so compound titles ("Prof. Dr. iur.") are fully removed from the name and fully preserved in `title` — the original single-`search()` version silently dropped all but the first token. Applied in both the comma-split path and the French-narrative path (`_FR_NARRATIVE_*_RE`), which previously hardcoded `title=None` and never stripped it at all. Forward-only — existing rows are not backfilled.

**Hyphen/space name-key fold** (2026-07-26) — `_normalize()` now folds hyphens to spaces before hashing, so "Marie-Magdeleine" and "Marie Magdeleine" (the same name extracted with/without a hyphen across different PDF eras) collapse to the same `normalized_key` instead of minting two entities. Related: `normalize_pdf_text()` in `shab_archive_client.py` (used upstream, before extraction) now also (a) covers French/Italian accented letters in its dehyphenation regex — previously German-only (`äöüß`/`ÄÖÜ`), so a line-wrap before an accented letter fell through unrejoined, leaving a visible stray "word- word" in FR/IT text — and (b) tolerates stray inline whitespace between the newline and the wrapped letter (`-\n çoise` → `çoise`, not just `-\nçoise`).

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
| `app/services/registry/sogc_person_extractor.py` | Regex-based DE/FR/IT parser, bisher field parsing, entity upsert, confidence recomputation, batch job; `_AUDITOR_EXCERPT_RE` auditor skip guard; `_recompute_is_current_for_entities`; `run_repair_is_current` |
| `app/services/registry/sogc_entity_resolver.py` | Bisher-first entity resolution: union-find, bisher match lookup, entity merge; calls `_recompute_is_current_for_entities` after merges |
| `app/services/jobs/job_handlers/sogc_persons.py` | Job handler for extract_sogc_persons |
| `app/services/jobs/job_handlers/sogc_entity_resolution.py` | Job handler for resolve_bisher_links |
| `app/services/jobs/job_handlers/sogc_repair.py` | Job handler for repair_is_current |
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
| `app/services/registry/simap_import.py` | Core import function: paginates, fetches details + vendor profiles, upserts awards + vendors, matches CHE UIDs; returns stats |
| `app/services/jobs/job_handlers/simap.py` | Job handler for `simap_daily`, `simap_backfill`, and `simap_archive`; resume via `last_cursor` in `stats_json` |
| `app/crud/job_run.py` | `has_simap_daily_run_today()` guard (mirrors `has_shab_daily_run_today`) |
| `frontend/src/components/simap-panel.tsx` | Company detail panel: useSWR on `GET /api/v1/companies/{id}/simap-awards`; renders award cards with price, authority, CPV; null-returns if no awards; show-more collapse after 3 |
| `app/clients/simap_archive_client.py` | HTTP client for archiv.simap.ch: `search_archive_awards()` (POST /api/search with `type_cd_ob`, date params) and `get_archive_detail()` (GET /api/detail?meldungsnummer={id}) |
| `app/services/registry/simap_archive_import.py` | Import service for pre-2024 archive: paginates 116k OB02 records, de-dupes by projectid (DE>FR>IT), fuzzy-matches contractor name+zip to companies via pg_trgm; IDs prefixed "arch-" |

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
