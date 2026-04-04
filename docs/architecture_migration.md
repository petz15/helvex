# Zefix Analyzer → Firmiq: Architecture Migration Plan

## Current State (as of 2026-03-31)

FastAPI backend + Next.js 14 App Router frontend + PostgreSQL (CloudNativePG) + Redis (RQ job queue) + ML pipeline (TF-IDF/HDBSCAN + spaCy) + Claude AI classification + NOGA classification. Deployed on K3s (Hetzner, single cluster `helvex-prod`) via Helm chart managed by GitHub Actions.

**Implemented (deviations from original plan):**
- ✅ Next.js frontend fully replaces Jinja2; separate K8s Deployment
- ✅ RQ (Redis Queue) replaces the planned Redis Streams model — simpler, no XREADGROUP bookkeeping
- ✅ Three-worker split: `zefix-worker` (bulk/detail/initial/batch), `api-worker` (scoring/geocode/NOGA/Claude), `ml-worker` (HDBSCAN/TF-IDF); each listens on its own RQ queue
- ✅ `WORKER_TYPE` env var dispatches each pod to its queue; `api-worker` runs 2 replicas for concurrency
- ✅ `job_timeout=-1` + `_heartbeat()` in every `_progress` callback — no SIGALRM kills; jobs live as long as they report progress
- ✅ LLM Batch API two-phase: `claude_classify` with `use_batch_api=True` submits → `waiting_external`; api-worker daemon thread polls Anthropic every 5 min; queue never blocked for 24h
- ✅ KEDA `ScaledObject` for ml-worker: scales 0→1 when `rq:queue:helvex-ml` has jobs; 5-min cooldown
- ✅ CloudNativePG: 1 primary + 1 standby, WAL archiving + daily base backups to Hetzner Object Storage
- ✅ Email verification, JWT auth, org management, user settings
- ✅ Company Explorer page (Unternehmens-Explorer) replacing old search/hunt page
- ✅ NOGA classification integrated into company profiles and explorer filters
- ✅ Stopword management in settings, integrated into DB and ML pipeline
- ✅ Monolith single Helm chart (not per-service charts) — deferred split until load requires it
- ⏳ Cluster autoscaler (node-level, Hetzner CA) — prerequisites: `hcloud-cloud-controller-manager` + CA Helm chart; deferred
- ⏳ Monitoring/Grafana stack — started but paused (resource cost vs value at current scale)
- ❌ Doppler K8s operator — using native K8s secrets populated by GitHub Actions deploy workflow

---

## Target Architecture: Modular Microservices on Kubernetes

### Core Principles
- Split along natural seams in the existing codebase
- Each service is independently deployable
- Async communication (Redis Streams) for long-running jobs
- Sync REST for latency-sensitive queries (UI data fetching)
- Shared `zefix-core` Python package for models/CRUD/schemas (prevents schema drift across services)
- `docker-compose.yml` stays working throughout the entire migration for local dev

### Brand
**Helvex** — "Helvetic" and techy

---

## Services

### 1. `frontend` — Next.js (K8s pod)
- Next.js 14 App Router + shadcn/ui + Tailwind CSS + TanStack Table
- Replaces Jinja2 templates
- Deployed as a K8s pod (not Vercel — keeps API latency low for Swiss users)
- Runs alongside Jinja2 during migration; Jinja2 removed only once Next.js covers 100% of routes

### 2. `api` — FastAPI (API Gateway / BFF)
- JWT Bearer authentication, rate limiting, tier enforcement middleware
- Exposes full OpenAPI spec
- Delegates work to workers by publishing to Redis Streams
- Sources from: `app/main.py`, `app/ui/routes.py` (REST endpoints only), `app/crud/`

### 3. `collection-worker` — Data Ingestion
- Consumes `jobs:collection` stream
- Handles: `bulk_import`, `batch_collect`, `zefix_detail_collect`, `google_search`, `re_geocode`
- Sources from: `app/services/collection.py`, `app/api/zefix_client.py`, `app/api/google_search_client.py`, `app/api/geocoding_client.py`
- Downloads geocoding SQLite (~300–400 MB) from Hetzner Object Storage into `emptyDir` at pod startup

### 4. `scoring-worker` — Scoring & Enrichment
- Consumes `jobs:scoring` stream
- Handles: `recalculate_zefix_scores`, `recalculate_google_scores`, `claude_classify`
- `app/services/scoring.py` stays as a pure library in `zefix-core` (no DB imports); bulk job runner logic moves here

### 5. `ml-worker` — ML Pipeline
- Consumes `jobs:ml` stream
- Handles: `cluster_pipeline` only
- Heavy dependencies (scikit-learn, spaCy, pandas) isolated in its own image
- Resource requests: `cpu: 2000m, memory: 3Gi`

---

## Infrastructure

### Cloud: Hetzner
- European/Swiss data residency for GDPR compliance
- Current: `app1` (cx33, 4 vCPU/8 GiB — control-plane + all workers) + `db1` (cx23, 2 vCPU/4 GiB — CloudNativePG worker node)
- Recommended upgrade: `app1` → cx43 (8 vCPU/16 GiB) once three-worker split is active to give each worker headroom
- ML node: provisioned on-demand by Cluster Autoscaler (cx41 class) when ml-worker jobs queue up; scales to 0 between jobs
- ~€44–60/month depending on ML node usage (cx33 €17 + cx23 €8 + LB11 €6 + Object Storage ~€1; cx43 upgrade adds ~€15)

### Orchestration: K3s
- Single cluster, two namespaces: `zefix-dev` and `zefix-prod`
- Helmfile for GitOps (dev/prod overlays via `infra/environments/`)
- ArgoCD deferred until team grows beyond solo

### Message Queue: RQ (Redis Queue)
- Redis Helm chart (StatefulSet) — not Upstash
- Three queues: `helvex-zefix`, `helvex-api`, `helvex-ml`
- RQ workers: each picks jobs only from its queue; multiple replicas on `helvex-api` provide concurrency without double-processing
- `job_runs` PostgreSQL table is the source of truth; Redis is dispatch + heartbeat only
- **Note:** original plan used Redis Streams + `XREADGROUP`; RQ was chosen for simplicity and built-in job lifecycle management

### Database: CloudNativePG (K8s-native)
- Runs inside K3s cluster as a CRD
- 1 primary + 1 standby replica (streaming replication)
- PgBouncer `Pooler` CRD in **transaction mode** (required for stateless multi-service)
- WAL archiving + daily base backups to Hetzner Object Storage
- Per-service DB users scoped to the tables they own

### Object Storage: Hetzner Object Storage (S3-compatible)
- Geocoding SQLite (~300–400 MB)
- CSV exports
- spaCy model artifacts
- TF-IDF vectorizer pickles
- PostgreSQL base backups

### Edge: Cloudflare (free tier)
- TLS termination, DDoS protection, asset caching
- Traefik (K3s built-in) handles internal ingress

### Secrets: Doppler
- K8s operator syncs to native K8s Secrets
- Configs: `dev`, `prod`, `ci`
- No `.env` files in containers, no secrets in git

---

## Target Repository Structure

```
firmiq/
├── packages/
│   └── zefix-core/         # shared models, CRUD, schemas, alembic
│       ├── pyproject.toml
│       └── zefix_core/
│           ├── models/     # all SQLAlchemy models
│           ├── schemas/    # all Pydantic schemas
│           ├── crud/       # all CRUD functions
│           ├── database.py
│           └── config.py
├── services/
│   ├── api/               # FastAPI — thin, no sklearn/spaCy
│   ├── frontend/          # Next.js + shadcn/ui
│   ├── collection/        # collection-worker
│   ├── scoring/           # scoring-worker
│   └── ml/                # ml-worker (heavy deps)
├── infra/
│   ├── helmfile.yaml
│   ├── environments/
│   │   ├── dev.yaml
│   │   └── prod.yaml
│   └── helm/              # one chart per service
├── docs/
│   └── adr/               # Architecture Decision Records
├── docker-compose.yml     # local development (keep working throughout)
└── .github/
    └── workflows/
        ├── ci.yml         # ruff, pytest, eslint, type-check
        ├── build.yml      # build Docker images, push to GHCR
        ├── deploy-dev.yml # helmfile apply --environment dev
        └── deploy-prod.yml# manual trigger or tag → prod
```

---

## Data Flow (Implemented)

```
Cloudflare / cert-manager (TLS)
      │
┌─────▼──────────┐
│   Next.js      │  K8s Deployment (cpu: 200m, memory: 256Mi)
└─────┬──────────┘
      │ REST/JWT
┌─────▼──────────────────┐
│  FastAPI (app)          │  K8s Deployment, 1 replica
│  JWT auth               │  (cpu: 500m, memory: 512Mi)
│  Job dispatch → RQ      │
└──┬──────────────────────┘
   │ enqueue to RQ (Redis)
┌──▼──────────────────────────────────────────┐
│                   Redis (RQ)                │
│  helvex-zefix | helvex-api | helvex-ml      │
└──┬──────────────┬──────────────┬────────────┘
   │              │              │
┌──▼──────────┐ ┌─▼───────────┐ ┌─▼──────────────┐
│zefix-worker │ │api-worker   │ │ml-worker       │
│1 replica    │ │2 replicas   │ │KEDA 0→1        │
│bulk/detail/ │ │geocode/score│ │HDBSCAN/TF-IDF  │
│initial/batch│ │NOGA/Claude  │ │cpu:500m-2      │
│             │ │+LLM poll    │ │mem:1-2Gi       │
│             │ │  thread     │ │(on-demand node)│
└─────────────┘ └─────────────┘ └────────────────┘
      │              │                │
┌─────▼──────────────▼────────────────▼──────┐
│           CloudNativePG                    │
│  1 primary + 1 standby                    │
│  WAL archiving → Hetzner Object Storage   │
└────────────────────────────────────────────┘
```

---

## Migration Phases

### Phase 0 — Security + User Model *(GATE: must complete before public prod)*

1. Alembic migration: add `email`, `tier`, `stripe_customer_id`, `stripe_subscription_id`, `subscription_status`, `email_verified`, `org_id` to `User` (`app/models/user.py`)
2. New `organizations` table
3. Replace cookie sessions with JWT Bearer auth on all non-public routes
4. Add CSRF protection, security headers middleware (HSTS, CSP, X-Content-Type-Options)
5. Rate limiting on `/auth/login` and `/auth/register`
6. Ensure all mutating routes write `user_id` to `AuditLog`
7. Basic email verification flow

**Exit gate:** all routes require auth; OWASP Top 10 self-checklist passes.

---

### Phase 1 — K3s + CloudNativePG + Monolith Deployment *(PROD milestone)*

Deploy the existing monolith to K3s — no microservices split yet.

#### Status (as of 2026-03-31)

**Done:**
- ✅ Hetzner Object Storage bucket `helvex-backups` created (nbg1), Terraform S3 backend configured
- ✅ Terraform provisioned: `app1` (cx33, control-plane) + `db1` (cx23, worker), LB, private network, firewall
- ✅ K3s installed on both nodes; both `Ready`
- ✅ DNS: `helvex.dicy.ch` → LB; TLS via cert-manager + Let's Encrypt
- ✅ ARC (Actions Runner Controller) — ephemeral runner pods, survives rebuilds
- ✅ CloudNativePG cluster live: 1 primary + 1 standby, WAL archiving to S3, daily base backups
- ✅ App + frontend deployed; CI/CD via GitHub Actions `[deploy-app]` / `[deploy-prod]` tags
- ✅ Data migration complete (pg_dump → CloudNativePG)
- ✅ Three-worker split deployed: zefix-worker, api-worker (2 replicas), ml-worker (KEDA scale-to-0)
- ✅ LLM Batch API two-phase: `waiting_external` status; api-worker poll thread handles completion
- ✅ KEDA ScaledObject for ml-worker live (scales 0→1 on queue depth)

**Deviations from original plan:**
- Dropped `app2` worker node (cost saving — add back when load requires it)
- Dropped Doppler K8s operator — using native K8s secrets populated by deploy workflow
- Replaced self-hosted GitHub Actions runner with ARC
- RQ (Redis Queue) instead of Redis Streams — simpler job lifecycle management
- Single Helm chart for all services (monolith chart), not per-service charts
- Cluster autoscaler (node-level) deferred — KEDA handles pod-level only for now

#### Next Steps

1. **Install helm + helmfile on app1** (manual, one-time):
   ```bash
   ssh root@91.98.21.142
   curl https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
   HELMFILE_VERSION=0.171.0
   curl -Lo /tmp/helmfile.tar.gz https://github.com/helmfile/helmfile/releases/download/v${HELMFILE_VERSION}/helmfile_${HELMFILE_VERSION}_linux_amd64.tar.gz
   tar -xzf /tmp/helmfile.tar.gz -C /tmp && mv /tmp/helmfile /usr/local/bin/helmfile
   ```

2. **Run `terraform apply`** — provisions static primary IP for app1, updates server

3. **Install and join Tailscale on `app1` and `db1`**:
   ```bash
   ssh ubuntu@<app1-public-ip>
   curl -fsSL https://tailscale.com/install.sh | sh
   sudo tailscale up --authkey <TAILSCALE_AUTH_KEY> --hostname app1

   ssh ubuntu@<db1-public-ip>
   curl -fsSL https://tailscale.com/install.sh | sh
   sudo tailscale up --authkey <TAILSCALE_AUTH_KEY> --hostname db1
   ```

4. **Run helmfile** (deploys cert-manager, CloudNativePG, Redis, ARC, app):
   ```bash
   helmfile -e prod apply
   ```

5. **Data migration** — `pg_dump` local → `pg_restore` into CloudNativePG

6. **Smoke test** — login, dashboard, run one job

**Dev→prod promotion gate:**
- `/health` returns ok for 1 hour in dev
- CloudNativePG backup object confirmed in Hetzner Object Storage
- Manual smoke test: login, list companies, run one `batch_collect` end-to-end

---

### Phase UX — Next.js Frontend *(starts Week 2, parallel to Phase 1)*

Stack: Next.js 14 App Router + shadcn/ui + Tailwind + TanStack Table

New URL structure:
```
/                        → landing + login
/app/dashboard           → company list
/app/companies/[id]      → company detail
/app/pipeline            → kanban by review_status
/app/map                 → geographic map
/app/jobs                → job queue
/app/settings            → app configuration
/app/admin               → admin panel (superadmin only)
/account/billing         → subscription + invoices
/account/team            → team members
```

Key UX improvements:
- Dashboard: collapsible filter sidebar + results table + slide-in company preview panel
- Pipeline view: Kanban columns by `review_status`
- Score bars as visual indicators, not raw numbers
- Job queue: real-time progress (SSE/polling), per-user quota usage bar

Run alongside Jinja2 — remove Jinja2 only when Next.js covers 100% of routes.

---

### Phase 2 — RQ Job Queue + Three-Worker Split ✅ DONE

Replaced in-process background thread with RQ (Redis Queue) workers as separate K8s Deployments.

**Implemented queue mapping:**
- `helvex-zefix` → `bulk`, `detail`, `initial`, `batch` (zefix-worker, 1 replica, always up)
- `helvex-api` → `re_geocode`, `recalculate_scores`, `recalculate_google_scores`, `reextract_purpose`, `reclassify_noga`, `claude_classify` (api-worker, 2 replicas, always up)
- `helvex-ml` → `hdbscan_cluster`, `recompute_keywords`, `cluster_analysis` (ml-worker, KEDA scale-to-0)

**Key decisions:**
- `job_timeout=-1` for all jobs; `_heartbeat()` called in every `_progress` callback — no SIGALRM kills
- `claude_classify` with `use_batch_api=True`: submit-only → `waiting_external` status; api-worker daemon thread polls Anthropic every 5 min
- KEDA `ScaledObject` + `TriggerAuthentication` for ml-worker: 0→1 on queue depth, 5-min cooldown
- Node-level autoscaling (Hetzner Cluster Autoscaler) deferred as next step after KEDA validated

**Note:** original plan used Redis Streams (`XREADGROUP`); RQ was chosen for simpler job lifecycle and built-in heartbeat/registry management.

---

### Phase 3 — Worker Container Split + Stripe + Tiers

1. Create `packages/zefix-core/` with shared models/CRUD/schemas/alembic
2. Create per-service Dockerfiles
3. Update `docker-compose.yml` to run all services locally
4. Deploy split services to `zefix-dev` namespace

Parallel — Tier enforcement:
- API middleware checks `user.tier` + Redis daily quota counters
- Worker pre-check re-validates tier before processing
- Stripe: `POST /api/billing/checkout`, `POST /webhooks/stripe` in `api` service
- `processed_stripe_events` table for Stripe webhook idempotency

Tier matrix (see User Tiers section below).

**Exit gate:** end-to-end job across split services; Stripe test-mode checkout upgrades tier in DB.

---

### Phase 4 — Multi-Tenancy + Row-Level Security

1. Add `tenant_id` to: `companies`, `notes`, `job_runs`, `collection_runs`, `audit_log`, `app_settings`
2. Enable PostgreSQL RLS on those tables
3. API sets `SET LOCAL app.tenant_id = :tenant_id` per request transaction
4. Backfill existing data to `default` tenant
5. Add org/team management UI (`/account/team`)

**Exit gate:** cross-tenant access test suite passes with zero data leaks.

---

### Phase 5 — Admin Panel + Analytics + Ads + Jinja2 Removal

- **Google Tag Manager**: single `<Script>` in Next.js root layout (`app/layout.tsx`); GTM container manages GA4 + Google Ads conversion tracking. Cookie consent banner (IAB TCF v2) required — covers GTM, GA4, Ads, and reCAPTCHA under one consent flow.
  - GA4: user behavior, funnels, retention
  - Google Ads: conversion tracking for paid acquisition
  - reCAPTCHA: auth/form protection (v3 recommended — invisible, no UX friction)
  - All three load via GTM; one consent banner covers all Google tags
- **EthicalAds**: conditional `AdBanner` component in Next.js (free tier only); one ad per page
- **Admin panel**: Next.js route group `(admin)` + FastAPI `/api/admin/` routes, behind `user.is_superadmin`; covers user management, tier override, job monitoring, feature flags, audit log
- **Jinja2 removal**: delete `app/ui/routes.py` + all templates; 301 redirects from `/ui/...`

---

## User Tiers

| Feature | Free | Starter | Professional | Enterprise |
|---|---|---|---|---|
| Max companies | 500 | 5,000 | 50,000 | Unlimited |
| Seats | 1 | 1 | 3 | 10+ |
| bulk_import | No | 1/day | 5/day | Unlimited |
| google_search | No | 25/day | 200/day | Unlimited |
| claude_classify | No | 50/day | 500/day | Unlimited |
| cluster_pipeline | No | No | 1/week | Daily |
| CSV export | No | 500 rows | Unlimited | Scheduled |
| REST API | No | No | Yes | Yes |
| Ads shown | Yes | No | No | No |
| API rate limit | 60 req/min | 300/min | 1,000/min | 5,000/min |

---

## Security Checklist

- [ ] Doppler K8s operator (not raw base64 K8s Secrets)
- [ ] Redis `requirepass` set, internal-only
- [ ] NetworkPolicy blocking `169.254.169.254` (Hetzner metadata API)
- [ ] All containers: `securityContext: runAsNonRoot: true`
- [ ] Docker base images pinned to digest
- [ ] K3s API server port 6443 restricted to your IP via Hetzner firewall
- [ ] `automountServiceAccountToken: false` on all Deployments
- [ ] Per-service DB users scoped to owned tables (no shared superuser)
- [ ] Pickle files in object storage: integrity check before load

---

## Open Security Issues

These are concrete security gaps spotted in the current webapp implementation/config (in addition to the target-state checklist above). They should be tracked as actionable issues with an owner + due date, and tied to Phase 0 / Phase 1 gates.

### 1) CSP still allows inline styles
- **Issue:** CSP still includes `style-src 'unsafe-inline'` because login/loading/error pages are rendered with inline CSS.
- **Why it matters:** Inline allowances keep the XSS defense model weaker than a strict nonce/hash policy.
- **Recommended fix:** Move inline CSS into static assets and migrate to nonce/hash-based CSP without `'unsafe-inline'`.
- **Priority:** **Medium**

### 2) Startup/background initialization race window
- **Issue:** Background startup task scheduling can allow serving requests before all initialization has completed.
- **Why it matters:** Sensitive routes may be reachable in a partially initialized state.
- **Recommended fix:** Gate sensitive endpoints on readiness and ensure startup readiness is enforced cluster-side (`readinessProbe`/app readiness gate).
- **Priority:** **Medium**

### 3) Dev registry/network exposure risk
- **Issue:** Registry/deployment config patterns (e.g., host networking / hostPath in dev) broaden host attack surface.
- **Why it matters:** Lateral movement and host compromise risk increases if reused beyond isolated local environments.
- **Recommended fix:** Limit to isolated dev only, add auth/TLS when applicable, and prefer cluster-internal networking patterns.
- **Priority:** **Medium**

### 4) Planned controls not fully enforced yet
- **Issue:** Some security checklist items in this document are currently aspirational (e.g., non-root everywhere, Doppler operator, strict service-account token policy).
- **Why it matters:** Security posture can be overestimated during rollout.
- **Recommended fix:** Convert checklist items into enforceable CI/cluster policy checks (lint + admission/policy tests) with explicit pass/fail gates.
- **Priority:** **High**

### 5) Roate keys
specifically for S3 as it was accidentally leaked

### Resolved Security Items

These items were addressed in code/config and are now considered closed for the current phase.

1. **Insecure fallback `secret_key` in runtime config**
- **Resolution:** Removed predictable fallback behavior and added production-like startup validation for strong secret configuration.
- **Implemented in:** `app/config.py`, `.env.example`

2. **Risky default credentials/config values**
- **Resolution:** Added production-like environment validation to fail fast on unsafe secret/password settings.
- **Implemented in:** `app/config.py`

3. **Deprecated `X-XSS-Protection` header in use**
- **Resolution:** Removed deprecated header and kept modern header strategy centered on CSP and other standard protections.
- **Implemented in:** `app/main.py`

4. **Public-route allowlisting too broad**
- **Resolution:** Reduced public allowlist surface and protected metadata from unauthenticated access.
- **Implemented in:** `app/main.py`

5. **Container hardening gap: non-root not enforced everywhere**
- **Resolution:** Enforced non-root execution and stricter container security context controls (drop capabilities, no privilege escalation).
- **Implemented in:** `infra/charts/helvex/templates/deployment.yaml`, `infra/charts/helvex/templates/frontend-deployment.yaml`

6. **Excessive operational metadata in health responses**
- **Resolution:** Reduced health response to minimal status output and removed detailed startup error disclosure from public health checks.
- **Implemented in:** `app/main.py`

7. **Missing dependency vulnerability gate in CI**
- **Resolution:** Added dependency audit step to CI.
- **Implemented in:** `.github/workflows/ci.yml`

---

## Cost Estimate (Hetzner, Production)

S3 Bucket: keep as small as possible; use Hetzner Storage Box for long-term backups (already covered by existing subscription).

| Resource | Specification | Monthly Cost |
|---|---|---|
| K3s control-plane + workers | cx33 — 4 vCPU, 8 GiB RAM (recommended: upgrade to cx43 8 vCPU/16 GiB) | ~€17–32 |
| Database node | cx23 — 2 vCPU, 4 GiB RAM | ~€8 |
| Load Balancer | Hetzner LB11 | ~€6 |
| Object Storage | ~100 GB (geocoding + WAL + base backups) | ~€1 |
| ML node (on-demand) | cx41 — 8 vCPU, 16 GiB RAM, provisioned by Cluster Autoscaler | ~€0 idle / ~€0.05/hr when active |
| **Total (cx33)** | | **~€32/month** |
| **Total (cx43 upgrade)** | | **~€47/month** |


---

## Files to Modify During Migration

| File | Migration Action |
|---|---|
| `app/models/user.py` | Phase 0: add email, tier, Stripe fields, org_id |
| `app/ui/routes.py` | Phase 2: extract job dispatch to Redis publish; Phase 5: delete |
| `app/main.py` | Phase 3: becomes `services/api/main.py` — remove Jinja2/UI routes |
| `app/services/collection.py` | Phase 3: move to `services/collection/` — refactor as queue consumer |
| `app/services/scoring.py` | Phase 3: library stays in `zefix-core`; bulk runner → `services/scoring/` |
| `app/services/cluster_pipeline.py` | Phase 3: move to `services/ml/` |
| `app/database.py` | Phase 3: moves to `packages/zefix-core/` |
| `docker-compose.yml` | Phase 1: add Redis; Phase 3: add all worker services |
| `Dockerfile` | Phase 3: replace with per-service Dockerfiles |
| `alembic/` | Moves to `packages/zefix-core/` |
| `nginx/` | Remove — Cloudflare handles edge; Traefik handles K8s ingress |

---

## Deliberately Deferred

- ArgoCD (replace Helmfile when team grows beyond solo)
- HPA on collection-worker (add when bulk imports exceed 10k companies/hour)
- OAuth2 / SSO (Enterprise tier, post-launch)
- Per-service separate databases
- OpenTelemetry distributed tracing
- Multi-region Hetzner deployment
- **Node autoscaling**: cluster-autoscaler with Hetzner Cloud provider. Split responsibility: Terraform manages control plane + DB node; autoscaler manages worker node pool (CX32, minSize 0, maxSize ~5). Requires `hcloud-cloud-controller-manager`, worker cloud-init bootstrap template (derived from existing Terraform cloud-init), and removing worker nodes from Terraform state. Add PodDisruptionBudget for Redis before enabling scale-down. Trigger: when worker CPU regularly exceeds 70% or ml-worker jobs queue up.
- **Audit log retention policy**: batch jobs generate high write volume (e.g. ~500k rows for a 50k-company import run). Add a scheduled DELETE for automated entries (`user_id IS NULL AND changed_at < NOW() - INTERVAL '90 days'`) via K8s CronJob or `pg_cron` once batch job frequency increases. Manual edits (`user_id IS NOT NULL`) should be kept indefinitely.

---

## Open-Source Considerations

- **OpenAPI docs** at `/docs` — expose publicly
- **GitHub Actions CI** — ruff, eslint, pytest, type-check on every push
- **Helm charts** — demonstrates K8s deployment knowledge
- **Architecture Decision Records** in `docs/adr/`
- **Docker Compose** for local dev — single command to run full stack
- **Semantic versioning + CHANGELOG.md**
- `.env.example` with all required variables documented (no real values)
