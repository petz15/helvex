# Helvex Roadmap

# Overview

- **Angebotsgestaltung**: 
    - Then there is the explore function which reveals companies based on keywords, clusters, AI classification, regionality etc together with the scoring mechanisms and additional website enrichment. -> much improvement needed
    - History of removed companies (?)
    - People finder and graph of connections potentially with linkedin?
    - Add Simap information for tenders and awards (only difficulty is how the tenders from public institutions are displayed as I have no entries for them in the Helvex DB...maybe could add?)
    - Data from eSHAB: has much mor than just changes in companies


- **Architecture — Go API + non-ML workers (v2.0, long-term)** (high effort, high payoff at scale):
    Keep the Python ML worker pod unchanged (sentence-transformers, spaCy, trafilatura, Playwright, clustering). Rewrite everything else in Go:
    - HTTP API server (all routes, auth, billing) → Gin or Fiber
    - Non-ML job handlers: `bulk_import`, `detail_fetch`, `batch_enrich`, `web_url_populate`, `web_crawl_http`, `web_crawl_single`, `claude_classify`, SIMAP/SHAB imports, geocoding → Go workers polling `job_runs` table (same `JOB_TYPE_WHITELIST` boundary already in place)
    - External API clients (Zefix, Serper, Stripe, Worldline, S3) → Go
    - Schema ownership: keep Alembic in Python for migrations (already runs on pod startup); use `sqlc` to generate typed Go query functions from the same SQL — no Go ORM needed
    - Python ML worker keeps SQLAlchemy models as-is; two schema representations need discipline to keep in sync
    Effort: ~6–10 weeks solo to reach feature parity. Hard parts: Stripe webhook state machine, job pause/resume/heartbeat logic (`job_worker.py`). Payoff when: selling API access with latency SLAs, or concurrent-user load makes the Python GIL visible on the API server.

- **Architecture — Company table normalization** (medium effort, high long-term value):
    Currently `companies` is a ~60-column fat-row table that tightly couples Zefix raw data, scoring, NOGA, TF-IDF, and geocoding. This works while every company comes from Zefix, but is a growing liability as we add non-Zefix sources (SHAB stubs, future people-only entities, etc.).
    Recommended split:
    - `company_zefix_data` side-table for `zefix_raw`, `zefix_*` admin IDs, `sogc_pub`, `old_names`, `translations` — stubs simply have no row here instead of NULLs everywhere
    - Keep scoring (`flex/web/ai/combined`), NOGA, and geocoding on the main table — these are query-critical and JOINs on 700k rows are expensive
    - Keep TF-IDF on the main table for the same reason
    Discriminator: use the new `source` column (`zefix` / `shab_stub`) already added to distinguish row origin. Existing `org_company_state` dual-write pattern is unaffected.
    Trade-off: cleans up the data model and makes multi-source ingestion first-class; costs a JOIN in the Zefix import path and all detail-view endpoints.

- **Major features**: 
    - Webcrawler for company websites -> started, needs to be checked
    - Linkedin unoffical API or scraper for people finder/graph and get more information
    - API access
    - Integrations with other products such as appollo etc?
    - other industry specific directories
    - Potentially other verbände such as Anwaltsverband, lobbywatch etc
    - View of Auditoren -> started needs to be checked
    - CRM parts (add contact, etc) -> there are github projects I could use?
    - Brand and Product ranking via LLM (thx Tim G.)
    - Data from SIMAP (augeschrieben + won) -> integrating government bodies might be somewhat difficult -> started need be checked, government bodies are not done yet (potentially this could be simply be added as other companies or extented bodies?) 
    - SIMAP suche anbieten bzw verbesserte suchmaske
    - epublikationen für weitere dinge wie betreibungen
    - medizinische leistungserbringer (?) helsana liste https://www.helsana.ch/de/private/services/leistungserbringer-suche.html?
    - Language translation for companies other languages. plan is probably all to english and then translate search terms to english and return results? -> this as additional feature for paid tiers

### MVP before public PROD
- Tiers are enforced - partially done (not fully checked and web searches are not gated yet + always uses api instead of checking if data already exists)
- Different SHAB import as fallback?
- And Set up DEV env
- Improve Explore page
- Potentially add some features
    - notifications of new companies

## PROD CHANGES
- wordline notification url
- linkedin and google
- email validator? 
- SMTP for emails?
- DNS eintrag auf balogh consulting bei hostpoint
- umami (also keys probably) potentially posthog?


## Bug Fixes & Known Issues
### General fixes:
- billing/payment/pricing
    - existing subscription then upgrading is not working
- admin dashboard simple redirects non authorized users, maybe not the safest
- ~~NOGA: Zweigniederlassung ist falsch~~ — Fixed: branch offices now bypass `only_missing_noga` guard and always re-run `apply_noga_classification` to inherit from parent.
- Make sure that users without access to BYOL API keys, always use the one provided by me (hidden not shown visible to the user)
- Fix translations for all pages (only headers etc done, needs more)
- Tiers:
    - web results are not gated, immediately shown. Should have been atleast 1 week of pause form a simple tier. 
- superadmin payment transactions does not show payment method
- the sogc_publications still have issues, language (id: 1976422 oder 1976562) not always detected correctly and encoding not fixed everywhere (id: 1976814 oder 1977166) -> even though it says fixed!
- Broad stop words for google search anything with vergleich in the domain name but not in the zweck or company name
- API key management not working as intended. multiple bugs and no clear way to manage models. code needs to be checked how it deals with multiple providers and model when sending requests
- NOGA v2: 366482 is terrible, how is this possible. 367779 another example. a part is too short zweck (difficult to mitigate unless website available) another part is the dirty gmbh text. 
- Scrapingdog: check some url results, I found one where it gave me results from other countries but I cant recreate id: 46434 recruit4u


### UI fixes:
- Email notifications remove from billing -> should be fixed but untested
- updated at in company overview should represent when the last sogc update was. potentially generally overhaul company search/overview for a cleaner search for one company. in company search, remove the small preview of a company profile. not really necessary in my opinion or atleast remove the scores as those are meant for explorer

# Questions

- should websearches be gated? or should they always remain private unless released by me (periodically?)?
    - if always private, some other organization updates a link, notify other orgs that their companies link has been updated (for free)
- other features? and how to price what


# Specific features

## Dashboard & UI

- [ ] **Fix Branding** — potentially change the icon to have a red cross in the middle (change google and linkedin app connection icons)
- [ ] **Map Adress search slow** - map adress search is slow atleast on the first adress. cold warm issue?


## Company Explore page

- [ ] **Create a mutation Timeline page** - A place where users can scroll through the daily company mutations with filters. Basically the company data but sorted by date instead of grouped by company and displayed on individual pages. This will probably require some preprocessing and then daily processing. 
- [ ] **scoring wizard general overhaul** - currently pretty useless as it only shows AI categories not all. the flow should be different. probably move away from singular score, or use that in the end. It should have filters etc and then scoring. or weighted scores based on different other scores. for location distance scoring, implement the option of multiple locations and if descending or ascending is better (some might have different locations/branches they are interested in).
- [ ] **NOGA numbers** - NOGA is not showing up any numbers
- [ ] **Introduction to new users** - currently new users just get thrown into explore without any guide
- [ ] **Improve Explore** - somehow explore page got worse with the last change. maybe I need to change the ML part first, check via a dashboard/overview (and implement that) then move on to explore. 
- [ ] **Noga Classification** - Too difficult via purpose keywords, keeps finding wrong categories. should use AI to do so (if not human supervision)
- [ ] **Brand ranking** (merci Tim G.) - add a brand ranking possibly via llm ranking (their information i.e. ranking). possibly for specific products or general


## Company Data

- [ ] **SIMAP archive backfill** — import historical procurement award data from `archiv.simap.ch` (covers pre-July-2024 data; the current `simap_backfill` job only covers July 2024+ via `simap.ch/api`).
  - **API:** `POST https://archiv.simap.ch/api/search` with `type_cd_ob: "OB02,OB08"` (Zuschläge); integer-paged (`pageNo`/`recordsPerPage`); detail via `GET /api/detail?meldungsnummer={id}` (returns JSON). Vendor data is in `OB02.SPEC.OB02.AWARD.PRIM.CONTRACTOR.LIST.PRIM.CONTRACTOR` — name + address, **no CHE UID**.
  - **Company matching — name index approach:** Since the archive has no CHE UIDs, matching requires a name-based lookup. Build a `{normalized_name → company_id}` index at job start from: (1) `companies.name` (current), (2) `sogc_changes.raw_excerpt` where `change_type='name'` (historical renamed-to names), (3) bisher-pattern extraction from those same excerpts (renamed-from names). Normalize: lowercase, strip legal suffixes (AG/GmbH/SA/Sàrl), collapse whitespace. Use vendor `ZIPCODE` (4-digit PLZ) as a tiebreaker only when the normalized name maps to multiple companies — never as a required constraint. If ambiguous after ZIP tiebreak → `company_id = NULL`. Past addresses from SOGC (`change_type='address'`) are free-form text and too noisy to use reliably; skip.
  - **⚠ Blocked on complete SOGC/SHAB history:** The name index quality is directly proportional to SOGC coverage. Without full historical SHAB imports (pre-2018 PDF archive + full eSHAB data), companies that renamed before our SOGC window will be missed entirely. Run this backfill only after Historic SHAB import and eSHAB data are complete.
  - **Implementation:** New `app/clients/simap_archive_client.py` + `app/services/simap_archive_import.py` (name index builder + paginated import). Reuses existing `simap_awards` / `simap_award_vendors` tables. New job type `simap_archive_backfill`, admin trigger in collection page. Resume via stored `page_no` in `stats_json`.
- [ ] **CSV export** — export current filtered/sorted dashboard view as CSV; include all visible columns; respect active filters and column selection -> somewhat exists but not fully operational yet. No way to set which columns the CSV exports currently!
- [ ] **Web crawler** — crawl company websites to extract description, contact info, product/service keywords; store as structured fields; feed into scoring and classification; replace/supplement current Google scrape
- [ ] **Web extract — LLM enrichment layer** — on top of the deterministic `web_extract` job (emails/phones/UID/socials/keywords), add an optional Claude Haiku layer that summarizes the cleaned main text into a company description + service summary + category hint. Must be tier/credit-gated (reuse `claude_classify` gating + batch-API pattern); run only on cleaned text to bound tokens; never ungated. Deferred from the phase-1 crawler ingestion build.
- [ ] **Web crawler — external paid scrape fallback tier** — a 3rd `tier=external` last resort for hard Cloudflare/CAPTCHA sites that defeat both httpx (curl_cffi impersonation) and Playwright. Reuse the existing ScrapingDog integration pattern (`scrapingdog_search_client.py`); gate by org/credits so cost is bounded to the few sites that need it. Deferred from the phase-2 bot-protection build.
- [ ] **Web extract — company-profile UI** — surface extracted contacts (emails/phones/socials), address, UID, languages, and description on the company detail page, sourced from `company_web_extract`. Decide placement/QOL (per CLAUDE.md frontend-wiring rule). Deferred from the phase-1 crawler ingestion build.
- [ ] **Web extract — LLM description/summary layer** — deterministic description is meta/OG or first paragraph; often weak. Add the deferred Claude Haiku layer to summarise cleaned main text into a description + service summary + category hint (tier/credit-gated, run on cleaned text only). Re-extract loop (`/admin/jobs/crawler/reextract`) means this can be layered on stored HTML without re-crawling.
- [ ] **Web extract — persons → People graph** — `persons` (impressum management names) are now extracted. Resolve them against SOGC person records / signers and feed the People-finder/graph feature.
- [ ] **Web extract — extractor quality tuning** — using the coverage dashboard, raise low-fill fields. Likely next: address parser recall (many SME impressums use non-standard formats), bigram keyword quality (reuse `discover_stopwords`/`analyze_boilerplate` outputs), phone fax-vs-tel disambiguation.
- [ ] **Web extract — URL confidence: trained logistic regression** — replace the hand-tuned additive weights in `crawler_extract.resolve_company_extract` with a logistic regression trained on labeled company→site pairs. Features already computed: `uid_score`, `addr_score`, `zone_name_conf`, `base` (signal coverage). Bootstrap labels by exporting companies with confidence 0.25–0.65 (the uncertain middle band) and classifying them manually; ~500 pairs sufficient for sklearn. Weights learned from real failure modes will outperform hand-tuning and handle feature correlations (e.g. matching address + wrong UID = subsidiary page). Serialize trained model to `app/services/url_confidence_model.joblib`; fall back to current formula if model file absent.
- [ ] **Web extract — URL confidence: embedding similarity feature** — add cosine similarity between the Zefix `purpose` embedding and the crawled site's `description`/`service_keywords` as a fourth feature for the logistic regression above. Reuse `paraphrase-multilingual-mpnet-base-v2` already running on the ML worker. Catches semantic matches that token overlap misses (e.g. "Versicherungsberatung" vs "insurance consulting"). Zero extra API cost; run during the `web_extract` job pass since embeddings are already available.
- [ ] **Google results & scoring** — Improve the selection and scoring of google results
- [ ] **NOGA Data** — add NOGA data (or similar) which is something other sites have such as business-monitor.ch or moneyhouse.ch -> first implementation done via AI classification; needs improvement preferably without AI or optional with AI; displaying is not looking too good yet; only shows the level it is confident in but not the full hierarchy -> NOGA classification should be done via AI
- [ ] **Free tier**- show some limited or teaser data for free tier
- [ ] **Flex Rescore** - Needs to be implemented differently. Should be 1x rescore or maybe 2x rescore per month for all available companies. then like 1000 credits per rescore. Depends how much actual computation power it needs
- [ ] **Multiple scores** - allow for different set of scores in case users are looking for different types of companies. e.g. for a specific campaign or promotion etc
- [ ] **Fix Multilang issue** - Need to decide if I have one language as base (such as english), convert everything to english for stuff like categorization etc then go and translate back i.e. use the original language again? other method would be to do it per language and then add a filter for languges with optional translations for other langugages. 
- [ ] **Data from eSHAB** - has much mor than just changes in companies and pre 2018



## Company Profile



- [ ] **Do not immediatly show scoring unless explore has been setup for org or user** —



## Classification & Scoring

- [ ] **NOGA cross-encoder reranking** — after bi-encoder retrieves top-20 NOGA candidates, rerank with a local cross-encoder (e.g. `cross-encoder/mmarco-mMiniLMv2-L12-H384`, ~120MB, no API cost). Fixes embedding-space collisions (e.g. wastewater equipment trader → coal mining) and action-verb blindness (Import/Vertrieb/Handelsbetrieb ignored by bi-encoder). ~150–400ms/company on CPU; limit to low-confidence companies (adjusted score <0.75 or top-2 within 0.03) to reduce volume ~70%. Also consider BM25 hybrid (replaces current token overlap) and a section-level pre-classifier (train 21-class A–U on existing high-confidence rows) as complementary improvements.
- [ ] **LLM classification extensions** — add OpenAI (ChatGPT) alongside Claude; user-configurable classification prompt per LLM; user-adjustable criteria. Potentially add groq for even cheaper prices especially for the noga usecase. potential: groq (context length might be an issue if NOGA has to be provided), deepseek, 
- [ ] **Custom review & proposal categories** — keep sensible defaults, allow users to define own categories per account
- [ ] **Per-user scoring rules** — custom distance origin, keyword boosts/penalties, cluster weights; DB: `user_scoring_config` (1:1 with users) + `company_user_score` (per user/company); scoring service already accepts a config dict
- [ ] **Implement multiple LLM APIs** and check if they actually work. especially the selection (including model type). 
- [ ] **Translation** - Translating the different texts such as purpose, shab etc

## Jobs & Infrastructure


- [ ] **ML Worker autoscaling with KEDA** — install KEDA; configure ScaledObject to scale ml-worker pods (0 → N) based on Redis queue depth; requires KEDA + Hetzner Cluster Autoscaler for node-level scaling; enables true "cost to zero" idle state and efficient burst capacity (replaces current fixed replicas: 1 approach)
- [ ] **Monitoring & Logging stack** — deploy Prometheus + Grafana on K3s; scrape app metrics (request rate, job queue depth, error rate), Kubernetes node/pod metrics, and Redis/PostgreSQL exporters; alert on pod restarts, high memory, queue stalls -> started but not fully done yet probably not going to continue with prometheus or grafana for a while
- [X] **Web analytics** — integrate Google Tag Manager + GA4 (or privacy-first alternative like Plausible/Umami); track page views, funnel steps (signup, first job, first export), feature usage; cookie consent banner for GDPR compliance
- [ ] **Cluster autoscaler (node-level)** — KEDA handles pod-level; Hetzner Cluster Autoscaler handles node provisioning for ML workload node pool; requires `hcloud-cloud-controller-manager` + CA Helm chart + node group config mapping `workload=ml` label to specific server type (cx41 or cx51); Terraform manages control-plane + DB nodes only; CA manages ML worker node pool separately
- [ ] **Hetzner ML node provisioning flow** - finalize documented/manual flow + helper scripts to create and join dedicated Hetzner ML nodes with private IP networking
- [ ] **ML scheduling policy** - implement Helm affinity policy: required `workload=ml`, preferred primary ML node class, cloud fallback when unavailable
- [ ] **ML capacity mode policy** - define default behavior when no ML node is available (queue-only vs temporary fallback)
- [ ] **Change postgres backup/recovery** - 1) Maintain an explicit latest backup pointer After each successful backup, write a small object like latest.json in the same bucket/prefix. 2) Manual restore source override as first-class input Add a workflow dispatch input or repo variable like POSTGRES_RESTORE_SOURCE. If set, workflow uses it exactly. If not set, then run auto-discovery.
- [ ] **Middleware** - my middleware python program has a chokehold on the whole architecture, if that is down nothing works! Either change that, i.e. review changes or when deploying and something fails, make sure this one can revert to a stable build. 
- [ ] **DEV/INT env** - for save deployment checks
- [ ] **File storage strategy** — User uploads, exports, static Next.js assets: confirm S3-compatible (Hetzner Object Storage) path, direct-to-client signed URLs vs server-side upload, CDN for static asset delivery. Currently not documented.
- [ ] **Session management** — Where user sessions are stored not yet documented. Options: Postgres table (preferred, integrates with RLS), in-memory (loses sessions on pod restart), Redis (if retained). 



## Org-/Usermanagement

- [ ] **Flow for account deletion** - GDPR compliant flow for account deletion




## Monetisation & Tiers

- [ ] **Adjustments to pricing** - More adjustments to pricing page: remove flex rescore from, some consumptions are not available for certain tiers and the question marks are not filled in. also for first time org accounts, give about 1k credits or even more
- [ ] **Verified business discount** — 20% extra discount (on top of tier bonus) for verified business orgs; applied at Stripe price calculation
- [ ] **Check Free tier limitations enforcement** — export limit enforcement in CSV export endpoint; API rate limits (once API access is gated)
- [ ] **Ad banner integration** — Ads embed for free tier; currently renders fake ads -> get real ad agency once I have users


## Security & Infrastructure

- [ ] **Cloudflare evaluation** — assess Cloudflare for DDoS protection, CDN/caching of static assets, bot management, and WAF rules; compare cost vs current Hetzner LB + cert-manager setup; consider Workers for edge auth or rate limiting
- [ ] **CAPTCHA evaluation** — evaluate CAPTCHA (hCaptcha / Cloudflare Turnstile / reCAPTCHA v3) for signup, login, and scraping-triggering actions; weigh friction cost against bot/abuse risk at current and projected traffic
- [ ] **verify api security** - Test and verify the security of the API  which is pretty open (how is it secured against attackers, bots and crawlers/unofficial APIs). 
- [ ] **A General pass over security not jsut api** - WAF, bot protection etc
- [ ] **Testing suite** — introduce consistent testing suite

### Security audit pass (Jun 2026) — review before treating as settled

Implemented per [architecture.md §22](architecture.md#22-security-hardening-pass-jun-2026) and
[runbook.md §25](runbook.md#25-keeping-k3s-and-the-servers-up-to-date). None of this has been
deployed/tested against the live prod cluster yet — review the Helm/Terraform/workflow diffs
before the next `[deploy-prod]` push.

- [ ] **Postgres HA (`instances: 2`) intentionally NOT implemented** — cost/architecture decision requiring your explicit sign-off, not auto-implemented. Prod currently runs a single Postgres instance.
- [ ] **Validate the new Postgres-only `NetworkPolicy`** — scoped to only the Postgres pods (app-tier pods, `cnpg-system`, same-cluster replicas, node-subnet `ipBlock` for kubelet probes) rather than a namespace-wide default-deny, as a judgment call to limit blast radius. Not yet validated against a live cluster — test in dev first that backups/replication/app traffic still work.
- [ ] **Decide when to make Trivy image scanning a hard deploy gate** — currently report-only (`exit-code: "0"` in `deploy-prod.yml`). Flip to `exit-code: "1"` once you've reviewed what it flags against current base images, to avoid unexpectedly blocking a prod deploy. Note: a real run on 2026-06-16 showed the Trivy step can itself fail (`exit code 1`) on a transient registry error (HTTP/2 `PROTOCOL_ERROR` while pulling a layer) — `exit-code: "0"` only suppresses the exit code for vulnerability findings, not scanner crashes. Added `continue-on-error: true` to all 4 scan steps so this can't block a deploy; if you make this a hard gate later, remove `continue-on-error` too or crashes will silently pass.
- [ ] **Add Trivy scanning to `deploy-dev.yml`** — currently prod-only; lower priority.
- [ ] **Confirm Renovate is actually active on this repo** — `renovate.json` is committed but needs the Renovate GitHub App (or equivalent) installed/enabled before it opens any PRs.
- [ ] **Re-confirm `admin_cidrs` covers how you actually connect** — scoped sudo + PAM now reject SSH/sudo from any IP outside `admin_cidrs` (`infra/terraform/envs/prod/variables.tf`). If your admin IP changes without updating this first, you lock yourself out of the control-plane.



## Architecture & Refactoring

- [ ] **API key management** — token creation/revocation UI for org admins to manage their API credentials; currently only available via admin panel
- [ ] **uvicorn async** - Each open SSE connection holds one synchronous uvicorn worker thread (blocking I/O). At current scale (<50 concurrent users) this is fine; at higher scale the endpoint should be rewritten as `async def` with `anyio.sleep` and an async Redis client.
- [ ] **Github Action Secrets Mess** - Currently many github action secrets are thrown in there which are my ENV variables, this should be managed and documented much better. Especially when I implement a DEV/INT env I should seperate a lot of these variables

### SOGC Entity Resolution — Event Sourcing (medium term)

Current state: `sogc_person_entity` rows are created and mutated during extraction.
The bisher-first resolver merges entities post-batch, but the entity table is still
a live mutable store — re-running the resolver can produce different results depending
on prior state.

Target architecture (event sourcing / CQRS):
- `sogc_person_appearances` becomes the **immutable event log** (append-only, never modified).
- `sogc_person_entity` becomes a **derived projection** computed entirely from appearances.
- User corrections (`sogc_person_flags` with should_merge / should_split) are stored as
  first-class events, separate from raw SOGC data.
- A `rebuild_person_entities` job drops all entity rows and re-derives them from scratch:
  1. Key-based dedup (lastname|firstname|hometown) → initial clusters
  2. Bisher resolver (union-find hard links) → merges entities across name changes
  3. Re-apply user correction flags → override algorithmic merges/splits
- Improving the resolution algorithm = run rebuild, get consistent results immediately.
- No stale state, no partial-merge artifacts from incremental runs.

Prerequisite: ensure `sogc_person_appearances` never has its `person_entity_id` used
as a join key outside the person resolution pipeline (audit all callers first).



## Other & QOL

- [ ] **Report Bug** - easy report Bug somewhere
- [ ] **Site version in UI**- the version of the site somewhere in the UI



## Completed
- [X] **Tier system** — 5 fixed tiers (free/simple/explorer/researcher/strategist) + custom modular tier; tier stored as integer (0–5) in DB for future-proofing
- [X] **Feature gates** — `has_feature(org, feature)` checks per-tier permissions; custom tiers read from `custom_features` JSONB column; all 24+ features gated (multi-user, no ads, LLM modes, API access, etc.)
- [X] **Credit consumption system** — 7 action types (batch_llm, immediate_llm, web_search, flex_rescore, recluster, bulk_export_basic, bulk_export_detail) with base costs; `compute_cost(action, count)` returns the full cost; deductions checked/enforced in `check_and_deduct()`
- [X] **Topup bonus model** — higher tiers receive bonus credits on purchase (free: 0%, simple: 10%, explorer: 15%, researcher: 20%, strategist: 30%); bonus granted via `topup_credits()` as separate ledger entry
- [X] **Credit entitlements** — simple: 1 free flex_rescore/month; explorer+: unlimited free flex_rescore; role-based access (viewer→read-only, contributor→write, admin/owner)
- [X] **Multi-org membership** — users join multiple orgs via `org_members` table (unique org_id/user_id); roles per-org; org switcher in UI
- [X] **Org invites** — token-based invite flow (7-day expiry), role embedded in token, domain restriction for verified business orgs
- [X] **Role hierarchy** — viewer/contributor/admin/owner per org; superadmin bypasses all checks
- [X] **Superadmin credits** — `credits_unlimited` flag on superadmin personal orgs bypasses balance checks entirely; no ledger entry for unlimited orgs
- [X] **Verified business** — auto-verify from Zefix link + web_score ≥70; manual verification fallback; domain-based email restriction for multi-user invite
- [X] **Custom tier configurator** — interactive modular pricing: base +web_months +export_100k +bonus_steps +priority_level +immediate_llm +byo_llm_keys +flex_auto_score +llm_auto_score; real-time CHF calculation
- [X] **Pricing page overhaul** — tier cards with visual hierarchy; full feature matrix table; consumption credits table with topup bonus preview; custom configurator with live pricing
- [X] **Tier stored as integer** — migration 0041 converts `organizations.tier` VARCHAR → INTEGER (0=free…5=custom); Organization model exposes `tier` as a property that translates int↔string, so all existing code sees a string and never breaks
- [X] **Role rename: member → contributor** — migration 0041 renames `org_members.role` and `users.org_role` from "member" to "contributor" to clarify write access; viewer (read-only) is now the default invite role
- [X] **Invite token with role** — `create_invite_token` and `decode_invite_token` now embed the role; old 2-element tokens (no role) gracefully default to viewer; org admin can pick the role when sending invites
- [X] **Removed dead code** — deleted `require_tier()` from auth.py (unused; org-scoped tier gating via `require_org_tier` in tiers.py is the standard)
- [X] **Credits unlimited for superadmin** — migration 0042 adds `organizations.credits_unlimited BOOLEAN`; superadmin personal orgs get this flag set at creation, causing `check_and_deduct` to always succeed without touching balance or ledger
- [X] **5-level job queue priority** — completed in earlier phase; queues named `{job_type}-p{0..4}`; workers listen in descending order; `get_queue_priority` maps tier/custom to priority level
- [X] **Email verification** — SMTP secrets must be set in GitHub Actions secrets (`SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`); deploy workflow populates `helvex-env` from these — if blank, verification emails are silently skipped
- [X] **WAL archiving backlog on fresh deploy** — WAL accumulates during initial bulk jobs (re-geocode) if archiving is not yet healthy; monitor `pg_stat_archiver` and scale down app before taking first backup to avoid disk pressure. Root cause: archiving was failing (wrong S3 path) for ~45 min while geocode job ran. On a healthy cluster archiving keeps up fine.
- [X] **Hourly base backups filling S3** — CNPG `ScheduledBackup` uses a 6-field cron (sec min hour …), not 5-field. `"0 2 * * *"` was parsed as "every hour at :02" instead of "daily at 02:00". Fixed to `"0 0 2 * * *"`. Also enabled `backupWalCompression: gzip`, `backupDataCompression: gzip`, and `wal_compression = on` in Postgres params.
- [ ] **WAL archiving backlog on bulk jobs** — For future large bulk jobs: run with `SET synchronous_commit = off;` in the session to reduce WAL flush overhead; for massive one-time loads use an unlogged staging table then insert into the real table.
- [X] **S3 backup path isolation** — dev and prod must use separate S3 paths (`pg/` vs `pg-prod/`); CNPG refuses to archive to a non-empty path from a different cluster instance
- [X] **Fix Branding favicon** — favicon is not consistent. sometimes it shows up correctly on chrome sometimes it doesnt. same with the firefox
- [X] **Fix Zweck not showing up in company profile page** — for some reason zweck is not showing up in the company profile page. maybe add a column for zweck in the companies table as it currently does not exist. 
- [X] **check company/2 404** - for somereason balogh consutling id = 2 is not showing up as company profile page. very weird as I cannot replicate it with other companies. maybe the issue is with the notes
- [X] **Rerun websearch bug** - whole websearch stuff is not configured correctly on the company page. first of all, it triggers a job and then goes to the job page instead of waiting for the result on the company page. second the first time web search button appears even if there is a webpage and lastly, the switch webpage button doesnt consistently appear. (check myself)
- [X] **remove username** — Remove username, just keep email adress as user 
- [X] **settings** — Add org management page and settings page for the users
- [X] **Alternative logins such as google, github etc?** — Add alternative login methods such as google accounts, linkedin -> started but not working currently 500 error
- [X] **Redis-based concurrent job queue** — RQ (Redis Queue) with `job_timeout=-1` for all jobs; heartbeat-driven lifetime (cancel_requested is the only kill switch); fixed StackSummary crash in failure callback; kick_job_worker passes job_type on re-enqueue
- [X] **Three-worker microservice split** — `helvex-zefix` queue (bulk/detail/initial/batch), `helvex-api` queue (geocode/scoring/NOGA/Claude), `helvex-ml` queue (HDBSCAN/TF-IDF); each has its own K8s Deployment; worker type controlled via `WORKER_TYPE` env var; `_heartbeat()` centralised and called from every `_progress` callback
- [X] **LLM Batch API two-phase flow** — `claude_classify` with `use_batch_api=True` submits to Anthropic Batch API and immediately exits the RQ job (status `waiting_external`); api-worker background thread polls every 5 minutes and processes results; no queue blocking, no Redis TTL risk
- [X] **ML worker KEDA scale-to-zero** — `ScaledObject` + `TriggerAuthentication` in Helm chart; ML pod scales 0→1 when `rq:queue:helvex-ml` has jobs; 5-minute cooldown; `helvex-ml-worker` priority class (value 50); nodeSelector/tolerations scaffolded for dedicated Hetzner node pool
- [X] **Tiered job queues** — two RQ queues: `helvex-priority` (starter/professional/enterprise + orgs) and `helvex-free` (free tier); `enqueue_job()` routes based on org/user tier; two separate K8s worker Deployments with different resource allocations; org creation alone does not move user to priority queue — requires a tier upgrade
- [X] **Email verification** — user signup flow with email verification; mutation/account changes require re-verification
- [X] **Fix Zefix down** — on 28.03.2026 the zefix API seemed unreachable with status code 500; improved error detection and retry strategy; now distinguishes rate limiting from outage
- [X] **Message-based job status** — replace polling with server-sent events (SSE) or WebSocket for real-time job status in UI
- [X] **Idempotent job retries** — ensure all job types can be safely re-run without duplicating work; implement job deduplication via hash/signature
- [X] **Usage dashboard** — org member view of credit balance (in CHF), transaction history, monthly spend, forecast
- [X] **Custom credit amount** — custom credit amount
- [X] **Payment processor integration** — Worldline product/price setup, subscription webhooks, credit top-up Checkout session handler - ongoing
- [X] **General QOL** — Impressum, Datenschutz pages, user settings page, general polish;
- [X] **Save views** — serialize active filters/sort/columns as JSON, stored per user, quickly re-applied from a dropdown
- [X] **Switch Index page** — change the entry page to something more welcoming to first time visitors
- [X] **Separate Search/Hunting page → Company Explorer (Unternehmens-Explorer)** — dedicated explorer page with guided onboarding flow; separate default columns and filter presets; batch actions (score selected, classify, add to list); legal form filter; date founded/deleted range filters; NOGA category filter; map toggle integrated into explorer; smoother flow for first-time users
- [X] **Demo on real pages instead of mock** — Find a way to demo the real webapp no use mock pages. seems weird. alternatively let users in without sign in but severly restrict access?
- [X] **For search** - for search different default columns and filtering options. add web searched as a flag to it. potentially add more filterint for peopel or company type/history
- [X] **Map: fix location clustering** — companies geocoded to PLZ centroid instead of address; increase map limit to 20 000; improve geocoding fallback logic -> already fixed but visualization could still be improved and scrolling in 
- [X] **Bulk import progress & abort** — bulk import now streams progress events and supports mid-run abort via cancel button in the Jobs UI; stuck-job abort added to handle workers that stop sending heartbeats
- [X] **Company profile overhaul** — display TF-IDF cluster, purpose keywords, Claude classification prominently -> ongoing
- [X] **Website correction flow** — "Report wrong website" button on company detail; shows all Google search results so user can pick the correct one; backend tallies user selections and auto-promotes a new URL if enough users agree; admins can override
- [X] **Fix Zweck not showing up** — maybe add column for zweck at companies table but that would also require to import it for existing companies 
- [X] **How are new companies added to clusters?**: Find a logic how new companies are added to tf-idf/HBDscan clusters without recomputing all of them -> should be done through dazzling-seeking-harp.md untested for now
- [X] **Generally Classifications** - in general the classifications are not working great. Might need a major overhaul -> dazzling-seeking-harp.md and look at git
- [X] **Improve classifications** - improvements to the classification NOGA and tf-idf/HBD-SCan see claude plan: dazzling-seeking-harp.md -> should be done. untested for now and not run yet. 
- [X] **Import all companies + full detail** — bulk import entire Zefix register including detailed fields (purpose, capital, offices, etc.) in one run
- [X] **Map** - add adress search in order to jump to a specific place
- [X] **Improve colors** - improve some of the colors, look and feel for the website
- [X] **Cookie banner settings** - adjust the cookie banner so users by default choose all
- [X] **Mobile optimization** - Optimize the website for mobile traffic (maybe limit some features)
- [X] **Options for Emails** - Create an options for emails and also probably need to configure how the email looks including
- [X] **Billing admin panel** — superadmin view of all orgs' tiers, subscription status, credit balance, transaction history; tier/credit adjustment UI
- [X] **Invoice & receipt generation** — PDF invoices for annual subscriptions; receipts for top-up purchases; email delivery -> could be improved
- [X] **Adress flow** - improve the adress flow, use default choosing one etc
- [X] **Credit grant system** — admin interface to grant/refund credits with reason; used for migration credits, promotions, support refunds
- [X] **automatic reocurring billing** on saferpay (worldline) I need to use the secure card data interface to save card data at saferpay which I can then utilize for later payments (reocurring payments like subscriptions or automatic topups). https://saferpay.github.io/jsonapi/#ChapterAliasStore
- [X] **Settings** - set up settings page for LLM, FLEX scoring, etc. Move out of account and improve it
- [X] **Categories page** - Needs to be fixed from the existing and integrated into the company explore page. first AI classification should be org specific, currently there is some data which should be changed to org 1.  NOGA classification dumps all levels into the field, instead it should display the hierarchy, always sum by total number also from lower hierarchy. add filtering options where necessary
- [X] **NOGA** - NOGA still doesnt quite look correct
- [X] **Browse page** - The search bar, when searching for keywords, clusters etc it should search all available not only the top 20 words. 
- [X] **Remove default flex score and categories** - Remove my default flex scores and categories only 
- [X] **ML Classifications** - Not sure that all of them are implemented quite correctly also explanations, drawbacks, multi org support for different tiers needs to be implemented, etc. 
- [X] **User Management for Orgs** - Set what users are allowed to do (read only or also execute)
- [X] **DE / FR / IT support** — UI strings, labels, tooltips; Zefix data already multilingual by canton
- [X] **Org member audit log** — track all role changes, additions, removals with user/timestamp
- [X] **Mobile optimization** - Optimize the website for mobile traffic (maybe limit some features)
- [X] **Seperate org management page** - seperate page for org management between general and billing
- [X] **Possibility to create new orgs** - users should be able to create as many orgs as they want to 
- [X] **History overview** —  Old names and taken over is already visible but not SOGC publications, which needs to be custom handled in order to display changes such as people and other changes. -> nicer overview and improvements, there are still some mistakes sometimes in the displayment of people (preprocessing might be necessary)
- [X] **Job queueing: Redis Streams + RQ vs Procrastinate** — Currently uses Redis Streams + RQ with two-tier queues. Alternative: Procrastinate (Python async-first, uses Postgres native `SELECT...FOR UPDATE SKIP LOCKED`) would eliminate Redis dependency, simplify stack to single Postgres, and handle B2B SaaS scale.
- [X] **Caching & rate-limiting strategy** — If Procrastinate adopted, Redis becomes optional. Current state: not documented. Options: Postgres + PgBouncer connection pooling (may be sufficient), Postgres token-bucket table for rate-limiting, lightweight in-app caching. **Action:** Deferred pending Procrastinate decision.
- [X] **reduce clutter** - reduce the spagetthi code and make it all much cleaner. e.g. multiple places for prices instead of one localised one, or how data is show to orgs/users
- [X] **remove Redis** - replace with postgres addon instead of having a seperate service which I dont really use tbh
- [X] **Imporve Purpose Keyword extraction** - Seeing as this is central for NOGA and clustering, these keywords should be as accurate as possible. Review how it is done. Generally improve ML later as much as possible
- [X] **Rate limiting** implemented a rate limiting for tiers for certain actions, but not sure if I want to keep this functionality
- [X] **refund and other admin function** - check QOL of billing such as refunds and other methods. What happens when an automatic payment fails? -> subscription upgrades not working correctly, other functions not fully tested, definitely not complete. 
    - What happens to other users when an account is downgraded to free?
    - Subscription upgrade and downgrade flow
- [X] **Pre-processing company timeline** - process all shab timeline with a seperate table which then can be used for more features such as cleaner overview of changes, connections of people over multiple companies etc. past changes also with cancelled companies etc!
- [X] **Graph overview of relationships** — based on past SHAB changes and name changes, take overs etc -> create nicer visuals for timeline. evaluate js on the fly calculations vs backend/DB
- [X] **Cross-company person graph** — normalize sogcPub organ changes into `persons` / `company_persons` tables via a pipeline job; build a graph UI showing where signers appear across multiple companies, what roles they held, and when — enabling network analysis of directors, beneficial owners, and corporate groups -> could use a graph DB for that
- [x] **Historic SHAB import** - from the official SHAB website, get all the pre 2018 SHAB publications -> and then use them for my sogc stuff. need merge logic from the current zefix imports to avoid duplications and keep manageable. needs pdf parsing
- [x] **Web extract — multi-candidate comparison & discard UI** — Done: `WebsitePanel` shows "All URL candidates" card with confidence badge, UID match icon, candidate status, review flag, and promote/discard actions per row. Backend: `GET /{id}/web-extracts`, `POST .../promote`, `DELETE .../discard`. Crawler admin shows review flags table and high-frequency domain stats.
- [x] **Web extract — wire into web_score** — Done: `scoring.adjust_web_score_for_extraction()` adjusts the raw Serper `web_score` using `uid_matches_zefix` (+40 match / −50 mismatch, capped/floored) and the new `name_address_verified` fallback signal (+20, when no UID found but name+address match Zefix exactly). Wired into `handle_web_extract`, recomputed idempotently from the raw score each run so re-extraction doesn't compound it. See architecture.md §16.
- [x] **Web extract — wire web_score into combined_score** — Done: `compute_relevance_score` now uses a 4-component formula when `web_score` is present (`ai×0.50 + web×0.20 + noga×0.20 + kw×0.10`); falls back to 3-component when absent. All call sites updated. Absent components renormalise proportionally.
- [x] **Web extract — UID-mismatch candidate quarantine** — Done: when best extract has `uid_matches_zefix=False`, `handle_web_extract` calls `reject_url_candidate()` and unconditionally triggers fallback crawl of the next candidate. `quarantined` counter in job stats tracks this per run.
