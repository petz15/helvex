# Helvex Roadmap

# Major changes

- **Angebotsgestaltung**: 
    - Create a simpler product called "search" which simply shows zefix information in a nice way and has google search for website (this might be limited in the tiering, add reveal website 5 free per month). 
    - Then there is the explore function which reveals companies based on keywords, clusters, AI classification, regionality etc together with the scoring mechanisms and additional website enrichment. 
    - Map functionality keep it simple with optional socring overlay.
    - CRM stuff ? pipeline is already in place, more to come?
    - History of removed companies (?)
    - People finder and graph of connections potentially with linkedin?

- **Major features**: 
    - Webcrawler for company websites
    - Linkedin unoffical API or scraper for people finder/graph and get more information
    - API access
    - Integrations with other products such as appollo etc?
    - other industry specific directories

### Todos
- Check new CSV export
- Implement Wordline integration next to stripe (or instead of stripe?)
- Adjust sites for Superadmin and normal admin
- Adjust sites for setup (seperate some features out from account page)
- Continue on Explore page

## Dashboard & UI

- [X] **General QOL** — Impressum, Datenschutz pages, user settings page, general polish;
- [X] **Save views** — serialize active filters/sort/columns as JSON, stored per user, quickly re-applied from a dropdown
- [X] **Switch Index page** — change the entry page to something more welcoming to first time visitors
- [X] **Separate Search/Hunting page → Company Explorer (Unternehmens-Explorer)** — dedicated explorer page with guided onboarding flow; separate default columns and filter presets; batch actions (score selected, classify, add to list); legal form filter; date founded/deleted range filters; NOGA category filter; map toggle integrated into explorer; smoother flow for first-time users
- [ ] **Add dark mode** — add dark mode
- [X] **Demo on real pages instead of mock** — Find a way to demo the real webapp no use mock pages. seems weird. alternatively let users in without sign in but severly restrict access?
- [ ] **Fix Branding** — potentially change the icon to have a red cross in the middle (change google and linkedin app connection icons)
- [X] **For search** - for search different default columns and filtering options. add web searched as a flag to it. potentially add more filterint for peopel or company type/history
- [ ] **Improve colors** - improve some of the colors, look and feel for the website

## Company Data

- [X] **Map: fix location clustering** — companies geocoded to PLZ centroid instead of address; increase map limit to 20 000; improve geocoding fallback logic -> already fixed but visualization could still be improved
- [X] **Bulk import progress & abort** — bulk import now streams progress events and supports mid-run abort via cancel button in the Jobs UI; stuck-job abort added to handle workers that stop sending heartbeats
- [ ] **Import all companies + full detail** — bulk import entire Zefix register including detailed fields (purpose, capital, offices, etc.) in one run
- [ ] **Daily SHAB imports** — automated daily job pulling new/changed/deleted companies from SHAB to keep DB current without full re-import -> also means that those listed in the SHAB need a full detail import?
- [ ] **CSV export** — export current filtered/sorted dashboard view as CSV; include all visible columns; respect active filters and column selection -> somewhat exists but not fully operational yet
- [ ] **Web crawler** — crawl company websites to extract description, contact info, product/service keywords; store as structured fields; feed into scoring and classification; replace/supplement current Google scrape
- [ ] **Google results & scoring** — Improve the selection and scoring of google results
- [ ] **NOGA Data** — add NOGA data (or similar) which is something other sites have such as business-monitor.ch or moneyhouse.ch -> first implementation done via AI classification; needs improvement preferably without AI or optional with AI; displaying is not looking too good yet; only shows the level it is confident in but not the full hierarchy
- [X] **Fix Zefix down** — on 28.03.2026 the zefix API seemed unreachable with status code 500; improved error detection and retry strategy; now distinguishes rate limiting from outages

## Company Profile

- [X] **Company profile overhaul** — display TF-IDF cluster, purpose keywords, Claude classification prominently -> ongoing
- [X] **Website correction flow** — "Report wrong website" button on company detail; shows all Google search results so user can pick the correct one; backend tallies user selections and auto-promotes a new URL if enough users agree; admins can override
- [ ] **Company views for registered users** — full company detail accessible free with email registration (gated behind login, not tier)
- [ ] **History overview** —  Old names and taken over is already visible but not SOGC publications, which needs to be custom handled in order to display changes such as people and other changes. -> nicer overview
- [ ] **Graph overview of relationships** — based on past SHAB changes and name changes, take overs etc -> create nicer visuals for timeline. evaluate js on the fly calculations vs backend/DB
- [ ] **Cross-company person graph** — normalize sogcPub organ changes into `persons` / `company_persons` tables via a pipeline job; build a graph UI showing where signers appear across multiple companies, what roles they held, and when — enabling network analysis of directors, beneficial owners, and corporate groups
- [X] **Fix Zweck not showing up** — maybe add column for zweck at companies table but that would also require to import it for existing companies 
- [ ] **Do not immediatly show scoring unless explore has been setup for org or user** —

## Classification & Scoring

- [ ] **LLM classification extensions** — add OpenAI (ChatGPT) alongside Claude; user-configurable classification prompt per LLM; user-adjustable criteria
- [ ] **Custom review & proposal categories** — keep sensible defaults, allow users to define own categories per account
- [ ] **Per-user scoring rules** — custom distance origin, keyword boosts/penalties, cluster weights; DB: `user_scoring_config` (1:1 with users) + `company_user_score` (per user/company); scoring service already accepts a config dict

## Jobs & Infrastructure

- [X] **Redis-based concurrent job queue** — RQ (Redis Queue) with `job_timeout=-1` for all jobs; heartbeat-driven lifetime (cancel_requested is the only kill switch); fixed StackSummary crash in failure callback; kick_job_worker passes job_type on re-enqueue
- [X] **Three-worker microservice split** — `helvex-zefix` queue (bulk/detail/initial/batch), `helvex-api` queue (geocode/scoring/NOGA/Claude), `helvex-ml` queue (HDBSCAN/TF-IDF); each has its own K8s Deployment; worker type controlled via `WORKER_TYPE` env var; `_heartbeat()` centralised and called from every `_progress` callback
- [X] **LLM Batch API two-phase flow** — `claude_classify` with `use_batch_api=True` submits to Anthropic Batch API and immediately exits the RQ job (status `waiting_external`); api-worker background thread polls every 5 minutes and processes results; no queue blocking, no Redis TTL risk
- [X] **ML worker KEDA scale-to-zero** — `ScaledObject` + `TriggerAuthentication` in Helm chart; ML pod scales 0→1 when `rq:queue:helvex-ml` has jobs; 5-minute cooldown; `helvex-ml-worker` priority class (value 50); nodeSelector/tolerations scaffolded for dedicated Hetzner node pool
- [ ] **Cluster autoscaler (node-level)** — KEDA handles pod-level; Hetzner Cluster Autoscaler handles node provisioning for ML workload node pool; requires `hcloud-cloud-controller-manager` + CA Helm chart + node group config mapping `workload=ml` label to specific server type (cx41 or cx51); Terraform manages control-plane + DB nodes only; CA manages ML worker node pool separately
- [ ] **Tiered job queues** — two RQ queues: `helvex-priority` (starter/professional/enterprise + orgs) and `helvex-free` (free tier); `enqueue_job()` routes based on org/user tier; two separate K8s worker Deployments with different resource allocations; org creation alone does not move user to priority queue — requires a tier upgrade
- [X] **Email verification** — user signup flow with email verification; mutation/account changes require re-verification
- [ ] **Monitoring & Logging stack** — deploy Prometheus + Grafana on K3s; scrape app metrics (request rate, job queue depth, error rate), Kubernetes node/pod metrics, and Redis/PostgreSQL exporters; alert on pod restarts, high memory, queue stalls -> started but not fully done yet
- [ ] **Web analytics** — integrate Google Tag Manager + GA4 (or privacy-first alternative like Plausible/Umami); track page views, funnel steps (signup, first job, first export), feature usage; cookie consent banner for GDPR compliance


## Org-/Usermanagement
- [X] **remove username** — Remove username, just keep email adress as user 
- [X] **settings** — Add org management page and settings page for the users
- [X] **Alternative logins such as google, github etc?** — Add alternative login methods such as google accounts, linkedin -> started but not working currently 500 error
- [ ] **User Management for Orgs** - Set what users are allowed to do (read only or also execute)


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

- [X] **Email verification** — SMTP secrets must be set in GitHub Actions secrets (`SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`); deploy workflow populates `helvex-env` from these — if blank, verification emails are silently skipped
- [X] **WAL archiving backlog on fresh deploy** — WAL accumulates during initial bulk jobs (re-geocode) if archiving is not yet healthy; monitor `pg_stat_archiver` and scale down app before taking first backup to avoid disk pressure. Root cause: archiving was failing (wrong S3 path) for ~45 min while geocode job ran. On a healthy cluster archiving keeps up fine.
- [X] **Hourly base backups filling S3** — CNPG `ScheduledBackup` uses a 6-field cron (sec min hour …), not 5-field. `"0 2 * * *"` was parsed as "every hour at :02" instead of "daily at 02:00". Fixed to `"0 0 2 * * *"`. Also enabled `backupWalCompression: gzip`, `backupDataCompression: gzip`, and `wal_compression = on` in Postgres params.
- [ ] **WAL archiving backlog on bulk jobs** — For future large bulk jobs: run with `SET synchronous_commit = off;` in the session to reduce WAL flush overhead; for massive one-time loads use an unlogged staging table then insert into the real table.
- [X] **S3 backup path isolation** — dev and prod must use separate S3 paths (`pg/` vs `pg-prod/`); CNPG refuses to archive to a non-empty path from a different cluster instance
- [ ] **Testing suite** — introduce consistent testing suite
- [X] **Fix Branding favicon** — favicon is not consistent. sometimes it shows up correctly on chrome sometimes it doesnt. same with the firefox
- [X] **Fix Zweck not showing up in company profile page** — for some reason zweck is not showing up in the company profile page. maybe add a column for zweck in the companies table as it currently does not exist. 
- [X] **check company/2 404** - for somereason balogh consutling id = 2 is not showing up as company profile page. very weird as I cannot replicate it with other companies. maybe the issue is with the notes
- [X] **Rerun websearch bug** - whole websearch stuff is not configured correctly on the company page. first of all, it triggers a job and then goes to the job page instead of waiting for the result on the company page. second the first time web search button appears even if there is a webpage and lastly, the switch webpage button doesnt consistently appear. (check myself)
- [ ] **Node autoscaling**: cluster-autoscaler with Hetzner Cloud provider. Split responsibility: Terraform manages control plane + DB node; autoscaler manages worker node pool (CX32, minSize 0, maxSize ~5). Requires `hcloud-cloud-controller-manager`, worker cloud-init bootstrap template (derived from existing Terraform cloud-init), and removing worker nodes from Terraform state. Add PodDisruptionBudget for Redis before enabling scale-down. Trigger: when worker CPU regularly exceeds 70% or ml-worker jobs queue up.
-[ ] **Google Tag Manager**: single `<Script>` in Next.js root layout (`app/layout.tsx`); GTM container manages GA4 + Google Ads conversion tracking. Cookie consent banner (IAB TCF v2) required  covers GTM, GA4, Ads, and reCAPTCHA under one consent flow.
  - GA4: user behavior, funnels, retention
  - Google Ads: conversion tracking for paid acquisition
  - reCAPTCHA: auth/form protection (v3 recommended — invisible, no UX friction)
  - All three load via GTM; one consent banner covers all Google tags
- [ ] **How are new companies added to clusters?**: Find a logic how new companies are added to tf-idf/HBDscan clusters without recomputing all of them



## Multi-Language

- [ ] **DE / FR / IT support** — UI strings, labels, tooltips; Zefix data already multilingual by canton
