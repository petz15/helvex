# Helvex Roadmap

## Dashboard & UI

- [X] **Dashboard filtering overhaul** — move filters to top of page, collapseable; TF-IDF / Purpose / Claude filters with freeform autocomplete, top 20 + show more, company count per value, exclude mode (double-click), save view as JSON per user?
- [ ] **Category overview page** — a page/dashboard which gives an overview over the different cluster, keywords, ai classifications etc. easier to understand than only over the filters
- [X] **Rename scoring fields** — "Zefix Score" → "Company Score", "Google/Website Match Score" → "Web Score" (future-proof for crawlers); update all labels, tooltips, column headers, CSV export
- [ ] **General QOL** — Impressum, Datenschutz pages, user settings page, general polish
- [X] **Save views** — serialize active filters/sort/columns as JSON, stored per user, quickly re-applied from a dropdown

## Company Data

- [ ] **Map: fix location clustering** — companies geocoded to PLZ centroid instead of address; increase map limit to 20 000; improve geocoding fallback logic -> already better but could still be improved
- [ ] **Import all companies + full detail** — bulk import entire Zefix register including detailed fields (purpose, capital, offices, etc.) in one run
- [ ] **Daily SHAB imports** — automated daily job pulling new/changed/deleted companies from SHAB to keep DB current without full re-import
- [ ] **CSV export** — export current filtered/sorted dashboard view as CSV; include all visible columns; respect active filters and column selection -> somewhat exists but not fully operational yet
- [ ] **Web crawler** — crawl company websites to extract description, contact info, product/service keywords; store as structured fields; feed into scoring and classification; replace/supplement current Google scrape
- [ ] **Google results & scoring** — Improve the selection and scoring of google results

## Company Profile

- [ ] **Company profile overhaul** — display TF-IDF cluster, purpose keywords, Claude classification prominently -> ongoing
- [ ] **Website correction flow** — "Report wrong website" button on company detail; shows all Google search results so user can pick the correct one; backend tallies user selections and auto-promotes a new URL if enough users agree; admins can override
- [ ] **Company views for registered users** — full company detail accessible free with email registration (gated behind login, not tier)

## Classification & Scoring

- [ ] **LLM classification extensions** — add OpenAI (ChatGPT) alongside Claude; user-configurable classification prompt per LLM; user-adjustable criteria
- [ ] **Custom review & proposal categories** — keep sensible defaults, allow users to define own categories per account
- [ ] **Per-user scoring rules** — custom distance origin, keyword boosts/penalties, cluster weights; DB: `user_scoring_config` (1:1 with users) + `company_user_score` (per user/company); scoring service already accepts a config dict

## Jobs & Infrastructure

- [ ] **Redis-based concurrent job queue** — move job execution to Redis queue (Celery or RQ) enabling concurrent jobs from multiple users simultaneously; replace current single-threaded DB-backed queue
- [ ] **Microservices architecture improvements** — decouple heavy jobs (classification, scraping, scoring) into separate workers; define clear service boundaries. make workers for standard jobs which only the system triggers (almost everything zefix related). create workers for free tier users, create workers for paid users
- [ ] **Tiered job queues** — two RQ queues: `helvex-priority` (starter/professional/enterprise + orgs) and `helvex-free` (free tier); `enqueue_job()` routes based on org/user tier; two separate K8s worker Deployments with different resource allocations; org creation alone does not move user to priority queue — requires a tier upgrade
- [X] **Email verification** — user signup flow with email verification; mutation/account changes require re-verification
- [ ] **Monitoring stack** — deploy Prometheus + Grafana on K3s; scrape app metrics (request rate, job queue depth, error rate), Kubernetes node/pod metrics, and Redis/PostgreSQL exporters; alert on pod restarts, high memory, queue stalls
- [ ] **Web analytics** — integrate Google Tag Manager + GA4 (or privacy-first alternative like Plausible/Umami); track page views, funnel steps (signup, first job, first export), feature usage; cookie consent banner for GDPR compliance


## Org-/Usermanagement
- [ ] **remove username** — Remove username, just keep email adress as user 
- [ ] **settings** — Add org management page and settings page for the users


## Monetisation & Tiers

- [ ] **Payment logic** — Stripe/Worldline integration; subscription billing, top-up credits for pay-per-use API calls
- [ ] **Ad slots** — display ads for free tier users map views (e.g. sponsored pin/banner) or rather loading screens
- [ ] **Tier system** — free / pro / team tiers with defined feature gates
- [ ] **High-paying tier: custom settings** — own scoring config, own LLM prompts, own categories, private job queue
- [ ] **API access** — REST API for high-paying tiers; token management, rate limits, usage dashboard
- [ ] **Modular tiers** — user-assembled feature bundles (pick scoring + API + X credits etc.) rather than fixed plans

## Security & Infrastructure

- [ ] **Cloudflare evaluation** — assess Cloudflare for DDoS protection, CDN/caching of static assets, bot management, and WAF rules; compare cost vs current Hetzner LB + cert-manager setup; consider Workers for edge auth or rate limiting
- [ ] **CAPTCHA evaluation** — evaluate CAPTCHA (hCaptcha / Cloudflare Turnstile / reCAPTCHA v3) for signup, login, and scraping-triggering actions; weigh friction cost against bot/abuse risk at current and projected traffic

## Bug Fixes & Known Issues

- [ ] **Email verification** — SMTP secrets must be set in GitHub Actions secrets (`SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`); deploy workflow populates `helvex-env` from these — if blank, verification emails are silently skipped
- [x] **WAL archiving backlog on fresh deploy** — WAL accumulates during initial bulk jobs (re-geocode) if archiving is not yet healthy; monitor `pg_stat_archiver` and scale down app before taking first backup to avoid disk pressure. Root cause: archiving was failing (wrong S3 path) for ~45 min while geocode job ran. On a healthy cluster archiving keeps up fine.
- [x] **Hourly base backups filling S3** — CNPG `ScheduledBackup` uses a 6-field cron (sec min hour …), not 5-field. `"0 2 * * *"` was parsed as "every hour at :02" instead of "daily at 02:00". Fixed to `"0 0 2 * * *"`. Also enabled `backupWalCompression: gzip`, `backupDataCompression: gzip`, and `wal_compression = on` in Postgres params.
- [ ] **WAL archiving backlog on bulk jobs** — For future large bulk jobs: run with `SET synchronous_commit = off;` in the session to reduce WAL flush overhead; for massive one-time loads use an unlogged staging table then insert into the real table.
- [ ] **S3 backup path isolation** — dev and prod must use separate S3 paths (`pg/` vs `pg-prod/`); CNPG refuses to archive to a non-empty path from a different cluster instance
- [ ] **Testing suite** — introduce consistent testing suite

## Multi-Language

- [ ] **DE / FR / IT support** — UI strings, labels, tooltips; Zefix data already multilingual by canton
