# Zefix Analyzer → Firmiq: Architecture Migration Plan

## Current State

FastAPI monolith + Jinja2 server-rendered UI + PostgreSQL + in-process background job thread + ML pipeline (TF-IDF/K-Means + spaCy) + Claude Haiku AI classification. Deployed via Docker Compose with Nginx reverse proxy. Single-user, no tiers, no multi-tenancy.

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
- 3 nodes: CX32 × 2 (api + workers), CX22 × 1 (PostgreSQL)
- ~€44/month total (2× CX32 €15 + CX22 €8 + LB11 €6 + Object Storage ~€1)

### Orchestration: K3s
- Single cluster, two namespaces: `zefix-dev` and `zefix-prod`
- Helmfile for GitOps (dev/prod overlays via `infra/environments/`)
- ArgoCD deferred until team grows beyond solo

### Message Queue: Redis Streams
- Redis StatefulSet (10 GB PV) — not Upstash (latency overhead for rate limiting)
- Three stream keys: `jobs:collection`, `jobs:scoring`, `jobs:ml`
- `XREADGROUP` consumer groups — multiple replicas won't double-process
- `job_runs` PostgreSQL table is the source of truth; Redis is dispatch-only

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

## Data Flow (Target)

```
Cloudflare (TLS, CDN, DDoS)
      │
┌─────▼──────────┐
│   Next.js      │  K8s pod (cpu: 200m, memory: 256Mi)
└─────┬──────────┘
      │ REST/JWT
┌─────▼──────────────────┐
│  api (FastAPI)          │  K8s Deployment, 2 replicas
│  JWT auth + rate limit  │  (cpu: 500m, memory: 512Mi)
│  Tier enforcement       │
│  OpenAPI docs           │
└──┬──────────────────────┘
   │ publish to Redis Streams
┌──▼──────────────────────────────────────┐
│              Redis Streams              │
│  jobs:collection | jobs:scoring | jobs:ml│
└──┬─────────────┬───────────────┬────────┘
   │             │               │
┌──▼───────┐ ┌───▼──────────┐ ┌─▼────────────┐
│collection│ │scoring-      │ │ml-worker     │
│-worker   │ │worker        │ │TF-IDF,       │
│(Zefix,   │ │(scores,      │ │K-Means, spaCy│
│Google,   │ │Claude AI)    │ │cpu:2000m     │
│geocoding)│ │              │ │mem:3Gi       │
└──────────┘ └──────────────┘ └──────────────┘
      │             │               │
┌─────▼─────────────▼───────────────▼──────┐
│           CloudNativePG                   │
│  PgBouncer (transaction mode)             │
│  1 primary + 1 standby                   │
│  WAL → Hetzner Object Storage             │
└───────────────────────────────────────────┘
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

1. Provision 3 Hetzner nodes, install K3s
2. Deploy CloudNativePG, configure WAL backup to Hetzner Object Storage
3. Configure Doppler K8s operator
4. Deploy existing `app` as single K8s Deployment (2 replicas)
5. Deploy Redis StatefulSet
6. Configure Traefik IngressRoute + TLS
7. Set up `zefix-dev` and `zefix-prod` namespaces
8. GitHub Actions CI/CD pipeline
9. Data migration: `pg_dump` from Docker Compose → `pg_restore` into CloudNativePG

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

### Phase 2 — Redis Streams Job Queue

Replace `kick_job_worker` threading model in `app/ui/routes.py` with Redis Streams.

Strategy: dual-write (thread + Redis Streams) for one week, then remove thread dispatch.

Stream mapping:
- `jobs:collection` → `bulk_import`, `batch_collect`, `zefix_detail_collect`, `google_search`, `re_geocode`
- `jobs:scoring` → `recalculate_zefix_scores`, `recalculate_google_scores`, `claude_classify`
- `jobs:ml` → `cluster_pipeline`

Message: `{ "job_id": <int>, "job_type": <str>, "params": <dict> }`

**Exit gate:** all 9 job types complete via Redis Streams, zero duplicate executions.

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

### Phase 5 — Admin Panel + EthicalAds + Jinja2 Removal

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

| Resource | Specification | Monthly Cost |
|---|---|---|
| K3s node × 2 (api + workers) | CX32 — 4 vCPU, 8 GB RAM | ~€30 |
| Database node | CX22 — 2 vCPU, 4 GB RAM | ~€8 |
| Load Balancer | Hetzner LB11 | ~€6 |
| Object Storage | ~300 GB (geocoding + exports + backups) | ~€1 |
| **Total** | | **~€44/month** |

AWS equivalent: ~€150–200/month.

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
