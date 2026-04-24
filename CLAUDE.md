# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

**Helvex** (product name) is a B2B lead intelligence platform for Swiss SMEs. It bulk-imports ~700k companies from the Zefix commercial register, enriches them with Google Search results and Claude Haiku AI scoring, geocodes addresses offline, and exposes a dashboard with filtering, mapping, and CSV export. Monetized via Stripe/Worldline credits with per-org multi-tenancy.

## Commands

### Local development (no Docker)

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.backend.txt
cp .env.example .env        # fill in POSTGRES_* and SERPER_API_KEY at minimum
alembic upgrade head
uvicorn app.main:app --reload
```

### Docker Compose (full stack)

```bash
bash scripts/gen-certs.sh   # once, generates self-signed TLS cert
docker compose up --build
# App: https://localhost/ui (accept self-signed cert warning)
# Health: https://localhost/health
```

### Frontend

```bash
cd frontend
npm install
npm run dev     # dev server
npm run build   # production build
npm run lint
```

### Database migrations

```bash
alembic revision --autogenerate -m "description"
alembic upgrade head
alembic history
```

### Tests

```bash
pytest                        # uses in-memory SQLite, no PostgreSQL required
pytest --cov=app              # with coverage
pytest tests/test_routes.py   # single file
```

### Background jobs (CLI, outside HTTP)

```bash
python -m app.run_collector bulk              # import all companies from Zefix
python -m app.run_collector batch --limit 100 # Google Search enrichment
python -m app.run_collector initial --name "Muster AG"
```

### Kubernetes deploy

```bash
# Triggered by git commit tag — e.g., "[deploy-app]" or "[deploy-prod]"
cd infra && helmfile -e prod apply   # manual apply

kubectl rollout restart deployment/helvex -n helvex-prod
kubectl logs -f deployment/helvex -n helvex-prod
```

## Architecture

### Data pipeline

```
Zefix REST API → bulk_import → companies table
                                    ↓
                        detail_fetch (purpose, address, geocoding)
                                    ↓
                        batch_enrich → Serper.dev → web_score + website_url
                                    ↓
                        claude_classify → Claude Haiku → ai_score + ai_category
                                    ↓
                        combined_score = 0.70·AI + 0.20·Web + 0.10·Flex
```

**Flex score** is computed from Zefix data alone (no API calls) to prioritize which companies get expensive Google/Claude processing.

### Application layers

| Layer | Location | Purpose |
|---|---|---|
| Routes | `app/api/routes/` | HTTP endpoints; thin, delegates to services |
| Services | `app/services/` | Business logic, job orchestration, scoring |
| CRUD | `app/crud/` | DB access only, no business logic |
| Models | `app/models/` | SQLAlchemy ORM (67 migrations) |
| Schemas | `app/schemas/` | Pydantic DTOs |
| Clients | `app/api/` | External API wrappers (Zefix, Serper, geocoding) |

### Background job system

Two modes (controlled by `USE_RQ` env var):
- **Thread mode** (default): daemon thread polls `job_runs` DB table; runs jobs in-process
- **RQ mode**: separate `rq worker` process connects to Redis queue; entry point is `app/worker_entrypoint.py`

Both modes share the same DB-persisted `job_runs` table. This means jobs survive Redis/pod restarts. The dedup key (`"bulk:org_id"`, etc.) prevents >1 active job of the same type per org. A heartbeat timestamp (`last_heartbeat_at`) guards against double-execution on pod restart.

Job lifecycle: `queued → running → [paused ↔ running] → completed/failed/cancelled`

### Multi-tenancy model

`org_company_state` table decouples master data (Company table, Zefix source) from per-org workflow state (review_status, tags, contact_info). Same company can have different workflow state across orgs. Never denormalize workflow fields into the `companies` table.

### Scoring

Three independent scores stored separately in DB:
- `flex_score` — Zefix data only; computed locally, no API
- `web_score` — Serper.dev result quality (0–100 match against company name/location/purpose)
- `ai_score` — Claude Haiku classification (configurable system prompt, ~$0.25/1k companies)
- `combined_score` — weighted average (weights configurable in Settings UI without code changes)

### Startup sequence (`app/main.py`)

On lifespan start: run Alembic migrations (DB-locked, safe for multi-pod) → seed `AppSetting` rows → recover interrupted jobs → kick job worker thread → auto-enqueue one-time re-geocoding if not done.

### Auth

Two mechanisms checked on every request (`_user_id_from_request()` in `app/auth.py`):
- **Session cookie** — itsdangerous URLSafeTimedSerializer, 8h TTL
- **JWT Bearer** — PyJWT HS256, same `SECRET_KEY`, 8h TTL

Public paths (bypass `auth_gate` middleware): `/health`, `/static/*`, `/login`, `/api/v1/auth/*`, `/api/v1/billing/webhooks/*`, `/api/v1/companies/demo`.

### Geocoding

Offline only — no Google Maps API. Primary: swisstopo Gebäudeadressverzeichnis (building-level, ~4M addresses). Fallback: GeoNames PLZ centroid (~2km). No quota or API key required at runtime.

### Key config

`app/config.py` uses Pydantic Settings from `.env`. In `APP_ENV=prod/staging`, strict validation enforces: `SECRET_KEY` ≥ 32 chars, non-trivial DB password, SMTP config, and `APP_BASE_URL` set.

### Infrastructure

- **K8s**: K3s on Hetzner (2 nodes); Helm via Helmfile; CloudNativePG for PostgreSQL HA
- **CI/CD**: GitHub Actions — path-aware; deploy triggered by git commit tags like `[deploy-app]`, `[deploy-ml]`, `[deploy-prod]`
- **Monitoring**: Prometheus + Grafana (kube-prometheus-stack); `/metrics` scraped every 30s

## Important files

- [app/main.py](app/main.py) — app factory, middleware stack, lifespan
- [app/services/scoring.py](app/services/scoring.py) — flex/web/ai/combined score logic
- [app/services/collection.py](app/services/collection.py) — Zefix import, Google enrichment pipeline
- [app/services/job_worker.py](app/services/job_worker.py) — job dispatch and thread/RQ orchestration
- [app/services/cluster_pipeline.py](app/services/cluster_pipeline.py) — TF-IDF K-Means, HDBSCAN, semantic clustering
- [infra/helmfile.yaml](infra/helmfile.yaml) — orchestrates all K8s releases
- [.github/workflows/](.github/workflows/) — CI/CD pipelines
