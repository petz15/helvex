# Helvex Architecture

Helvex is a B2B SaaS platform built on K3s/Hetzner, migrating from the Zefix Analyzer monolith. This document outlines key architectural decisions and infrastructure concerns.

## Overview

- **Platform:** K3s on Hetzner (single cluster, two namespaces: zefix-dev, zefix-prod)
- **Database:** CloudNativePG (Postgres) with WAL archiving to Hetzner Object Storage
- **Frontend:** Next.js 14 on K8s pod (low latency to Swiss users)
- **Backend:** Python monolith (Phase 0-3), worker split in Phase 3
- **Job Queue:** Redis Streams + RQ (Phase 2-3) — *see Procrastinate alternative below*
- **Authentication:** JWT (in-house, Phase 0)
- **Multi-tenancy:** PostgreSQL RLS (Phase 4)
- **CI/CD:** Helmfile (GitOps)

---

## Core Architecture Decisions

### Compute & Orchestration
- **Single K3s cluster** (not separate dev/prod clusters) with namespace isolation
- **Next.js on K8s pod** (not Vercel) — needed for low-latency access to Swiss users and tight integration with backend
- **Helmfile for GitOps** (ArgoCD deferred)

### Database & Persistence
- **CloudNativePG** (not Hetzner Managed DB) for K8s-native Postgres management
  - Phase 1: `instances: 1` (single primary), WAL archiving to Hetzner Object Storage provides PITR without live replica cost
  - Phase 3+: Add `instances: 2` for read offloading via `-ro` Service (analytics, reporting — NOT automatic; app must explicitly target the pooler endpoint)
  - **Note:** Adding a replica does NOT increase write throughput
- **Shared zefix-core Python package** for models and CRUD across services (monolith → workers)

### Job Processing
- **Current:** Redis Streams + RQ with two queues (`helvex-priority`, `helvex-free`) for tier-based rate limiting
- **Alternative considered:** Procrastinate (Postgres-native async queue using `SELECT ... FOR UPDATE SKIP LOCKED`)
  - **Pros:** Eliminates Redis dependency, simpler stack, sufficient for B2B SaaS scale
  - **Cons:** Requires rework if Phase 2 already started
  - **Decision point:** If Phase 2 not yet implemented, strongly consider Procrastinate; else defer to Phase 4 refactor

### Payments & Monetization
- **Stripe integration** in api service (not separate payment microservice)
- **EthicalAds** (not Google AdSense) for free tier — privacy-respecting, tech audience aligned

### Admin & Operations
- **Admin panel as Next.js route group** (not AdminJS or separate CMS)
- **Jinja2 removal** deferred to Phase 5

---

## Infrastructure & Cross-Cutting Concerns

### Observability (Phase 1 Gap)
⚠️ **CRITICAL GAP:** No logging, tracing, or metrics strategy documented.

**Required for production SaaS:**
- **Structured logging:** JSON to stdout, K3s log collector aggregates (ELK, Grafana Loki, or similar)
- **Distributed tracing:** Jaeger or Tempo for request tracing across services
- **Metrics:** Prometheus scrapes app + CNPG + K3s, visualized in Grafana
- **Alerting:** Alert on latency, error rates, database replication lag, job queue depth

**Action:** Define observability stack before Phase 1 prod deployment.

### Caching & Rate-Limiting
- **If Procrastinate adopted:** Redis becomes optional. Revisit caching strategy:
  - Postgres + PgBouncer connection pooling may satisfy app query caching
  - Rate-limiting via Postgres token-bucket table (simple, suitable for B2B scale)
  - Alternative: lightweight in-app caching with TTL
- **If Redis retained:** Use for session store, cache, rate-limiting; Procrastinate adds minimal complexity

**Decision:** Deferred pending Procrastinate adoption decision.

### File Storage & CDN
- **User uploads, exports, assets:** S3-compatible (Hetzner Object Storage)
- **Strategy not yet defined:**
  - Direct-to-client signed URLs?
  - Server-side upload then object store?
  - CDN layer for static assets and exports?

**Action:** Confirm file handling strategy before Phase UX deployment.

### Session Management
- **Location:** Not yet documented. Options:
  - Postgres session table (simple, works well with RLS in Phase 4)
  - In-memory (acceptable for single-pod backend, loses sessions on restart)
  - Redis (if retained for other purposes)
- **Decision:** Recommend Postgres table for audit trail + RLS alignment

**Action:** Document before Phase 0 completion.

### Multi-Tenancy Isolation (Phase 4)
- **Chosen:** PostgreSQL RLS (row-level security)
- **Rationale:** Simpler operational model for B2B SaaS; sufficient for paid tier blast-radius
- **Alternative rejected:** Schema-per-tenant isolation (better isolation, higher operational complexity)
- **Note:** RLS alone requires careful app-level filtering; CNPG + PgBouncer pooling must pass `app.current_org_id` as session var or similar

### Full-Text Search
- **Current:** Postgres native FTS (sufficient for document search use case)
- **Alternatives rejected unless requirements change:**
  - Elasticsearch (overkill, operational complexity)
  - Meilisearch (consider only if typo-tolerance + vector search needed)

### Email Delivery
- **Status:** Not yet documented
- **Options:**
  - Transactional email service (SendGrid, Postmark, Mailgun) — recommended for SaaS
  - In-house SMTP (Postal, or raw Exim/Postfix on K3s) — higher ops burden
- **Decision:** Recommend managed service for reliability and deliverability

**Action:** Confirm before Phase 0 (auth emails needed).

### Secrets Management
- **Phase 1:** Doppler for secret rotation and audit
- **K3s alternatives:**
  - sealed-secrets (GitOps-friendly, encrypts secrets in git)
  - external-secrets-operator (syncs from Doppler/AWS Secrets Manager/HashiCorp Vault)
- **Decision:** Doppler + external-secrets-operator bridge for K8s native reconciliation

**Action:** Evaluate compliance/audit requirements and choose before Phase 1.

### Authentication & Authorization
- **Phase 0:** JWT in-house (not OAuth provider integration yet)
- **Token storage:** Browser localStorage vs httpOnly cookie — **not yet documented**
- **Recommended:** httpOnly cookies for XSS protection, secure flag for HTTPS-only transmission
- **Note:** CSRF protection required; document strategy pre-Phase 0

---

## Decision Matrix

| Concern | Phase | Decision | Status | Owner |
|---------|-------|----------|--------|-------|
| Job queue (Redis vs Procrastinate) | 2-3 | Redis Streams + RQ | Pending Procrastinate eval | Peter |
| Observability stack | 1 | Not documented | **GAP** | TBD |
| Caching strategy | 1-3 | Deferred pending Redis decision | Pending | Peter |
| File storage strategy | UX | S3-compatible (Hetzner) | Pending details | TBD |
| Session store | 0 | Not documented | **TBD** | Peter |
| Email service | 0 | Not documented | **TBD** | Peter |
| Secrets backend | 1 | Doppler + K8s integration | Pending | Peter |
| Multi-tenancy isolation | 4 | RLS (row-level) | Decided | Peter |
| Search engine | 4+ | Postgres FTS | Deferred | Peter |

---

## Related Documents
- `payment-flows.md` — Stripe integration flow
- `networking-modes.md` — Flannel/Tailscale hybrid cluster
- `home-ml-node-plan.md` — GPU node planning
- `runbook-fresh-deploy.md` — Deployment procedure
