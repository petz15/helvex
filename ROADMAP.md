# Helvex Roadmap

# Overview

- **Angebotsgestaltung**: 
    - Then there is the explore function which reveals companies based on keywords, clusters, AI classification, regionality etc together with the scoring mechanisms and additional website enrichment. -> much improvement needed
    - History of removed companies (?)
    - People finder and graph of connections potentially with linkedin?
    - Add Simap information for tenders and awards (only difficulty is how the tenders from public institutions are displayed as I have no entries for them in the Helvex DB...maybe could add?)


- **Major features**: 
    - Webcrawler for company websites
    - Linkedin unoffical API or scraper for people finder/graph and get more information
    - API access
    - Integrations with other products such as appollo etc?
    - other industry specific directories
    - Definitely more overview of management
    - Potentially other verbände such as Anwaltsverband, lobbywatch etc
    - View of Auditoren

### MVP before public PROD
- Tiers are enforced - partially done (not fully checked and web searches are not gated yet + always uses api instead of checking if data already exists)
- Fix SHAB History!
- Different SHAB import as fallback?
- Clean code, completely clean up old code -> maybe only with better claude subscription
- Decide on VAT if include or exclude
- And Set up DEV env
- Potentially transition away from redis already for prod. Dont fully see the use of it besides more complexity
- Improve Explore page
- Potentially add some features
    - notifications of new companies
    - overview of SHAB changes cleanier, seperate page with preprocessing
    - Overview of People mentioned in the SHAB. Search for them and connections between multiple companies (I think competitors have that)
    - SIMAP

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
- SHAB: rerun 17.04.2026 because there seems to be an issue with the data example: https://helvex.dicy.ch/app/companies/783586 same on zefix fix utf encoding
- NOGA auf unternehmensseite falsch dargestellt und meistens nicht korrekt!
- NOGA auf filter, sollte auch hierarchisch darstellen und mit richtiger bezeichnung
- Remove business model -> not useful and not correctly implemented (category detail page)
- admin dashboard simple redirects non authorized users, maybe not the safest
- NOGA: Zweigniederlassung ist falsch zbs: https://helvex.dicy.ch/app/companies/238698
- NOGA nach embedding immernoch falsch aufgrund boiler sentences z.b. peter batt
- Make sure that users without access to BYOL API keys, always use the one provided by me (hidden not shown visible to the user)
- Fix translations for all pages (only headers etc done, needs more)
    - Such as filters
    - information on pages
    - Columns selection
    - hover over effects are all in english
- Tiers:
    - web results are not gated, immediately shown. Should have been atleast 1 week of pause form a simple tier. 
- Page size is not working on company overview
- superadmin payment transactions does not show payment method
- more improvements to stopping words -> the LLM improvement i.e. script is not integrated into the website as far as I can tell. 
- SHAB needs general fixes:
    - Languages
    - old Auditors showing up 
    - enconding issue
    - and more, best solved over preprocessing -> espcially for future management view
- Broad stop words for google search anything with vergleich in the domain name but not in the zweck or company name


### UI fixes:
- Email notifications remove from billing -> should be fixed but untested
- Remove AI preview
- Show settings in explore page (currently only shown when there is no data but there is always data)
- create a seperate section for organization. in general show all organizations the user is a part of. Add a button to move to new organization and remove the one in the header - chaning org takes a long time to load? or does it even. remove it any way
- Company profile page improve the AD banner, too small! -> still an issue
- Checkbox on the category detail page is not working
- Category detail page has a number next to the title which is meaningless
- Category detail page, an overlay how many companies have no scoring. which is weird that companies which have ai classification dont have a score...is it 0?
- Settings page, move to explore or elsewhere -> technically org settings right? need to check if these are per org or per user
- Move recaculate scores, jobs etc from settings to jobs? or elsewhere but not here. also these should only be available to certain orgs
- updated at in company overview should represent when the last sogc update was. potentially generally overhaul company search/overview for a cleaner search for one company. in company search, remove the small preview of a company profile. not really necessary in my opinion or atleast remove the scores as those are meant for explorer
- larger symbols and text in header
- as soon as explorer has been opend, opening any other page requires atleast 10 seconds to load if not more if it loads at all
- Add translations to pricing page and unify the public one and the private one
- When map moves and a map marker is opend while more map markers load in, the currently opened map marker always closes QOL issue
- If the purpose only is boilerplatte sentece, it is probably a holding company. might need to find general solution to this problem

# Questions

- should websearches be gated? or should they always remain private unless released by me (periodically?)?
    - if always private, some other organization updates a link, notify other orgs that their companies link has been updated (for free)
- flex rescore free or monthly etc?
- other features?


# Specific features

## Dashboard & UI

- [ ] **Fix Branding** — potentially change the icon to have a red cross in the middle (change google and linkedin app connection icons)
- [ ] **Add dark mode** — add dark mode
- [ ] **Map Adress search slow** - map adress search is slow atleast on the first adress. cold warm issue?


## Company Explore page


- [ ] **Create a mutation Timeline page** - A place where users can scroll through the daily company mutations with filters. Basically the company data but sorted by date instead of grouped by company and displayed on individual pages. This will probably require some preprocessing and then daily processing. 
- [ ] **scoring wizard general overhaul** - currently pretty useless as it only shows AI categories not all. the flow should be different. probably move away from singular score, or use that in the end. It should have filters etc and then scoring. or weighted scores based on different other scores. for location distance scoring, implement the option of multiple locations and if descending or ascending is better (some might have different locations/branches they are interested in).
- [ ] **NOGA numbers** - NOGA is not showing up any numbers
- [ ] **Introduction to new users** - currently new users just get thrown into explore without any guide
- [ ] **Improve Explore** - somehow explore page got worse with the last change. maybe I need to change the ML part first, check via a dashboard/overview (and implement that) then move on to explore. 
- [ ] **Noga Classification** - Too difficult via purpose keywords, keeps finding wrong categories. should use AI to do so (if not human supervision)


## Company Data


- [ ] **Daily SHAB imports** — works but zefix has a utf problem, I might have to switch to SHAB and maybe even use their pdfs
- [ ] **CSV export** — export current filtered/sorted dashboard view as CSV; include all visible columns; respect active filters and column selection -> somewhat exists but not fully operational yet. No way to set which columns the CSV exports currently!
- [ ] **Web crawler** — crawl company websites to extract description, contact info, product/service keywords; store as structured fields; feed into scoring and classification; replace/supplement current Google scrape
- [ ] **Google results & scoring** — Improve the selection and scoring of google results
- [ ] **NOGA Data** — add NOGA data (or similar) which is something other sites have such as business-monitor.ch or moneyhouse.ch -> first implementation done via AI classification; needs improvement preferably without AI or optional with AI; displaying is not looking too good yet; only shows the level it is confident in but not the full hierarchy -> NOGA classification should be done via AI
- [ ] **Free tier**- show some limited or teaser data for free tier
- [ ] **Flex Rescore** - Needs to be implemented differently. Should be 1x rescore or maybe 2x rescore per month for all available companies. then like 1000 credits per rescore. Depends how much actual computation power it needs
- [ ] **Multiple scores** - allow for different set of scores in case users are looking for different types of companies. e.g. for a specific campaign or promotion etc
- [ ] **Fix Multilang issue** - Need to decide if I have one language as base (such as english), convert everything to english for stuff like categorization etc then go and translate back i.e. use the original language again? other method would be to do it per language and then add a filter for languges with optional translations for other langugages. 



## Company Profile


- [ ] **Pre-processing company timeline** - process all shab timeline with a seperate table which then can be used for more features such as cleaner overview of changes, connections of people over multiple companies etc. past changes also with cancelled companies etc!
- [ ] **Graph overview of relationships** — based on past SHAB changes and name changes, take overs etc -> create nicer visuals for timeline. evaluate js on the fly calculations vs backend/DB
- [ ] **Cross-company person graph** — normalize sogcPub organ changes into `persons` / `company_persons` tables via a pipeline job; build a graph UI showing where signers appear across multiple companies, what roles they held, and when — enabling network analysis of directors, beneficial owners, and corporate groups -> could use a graph DB for that
- [ ] **Do not immediatly show scoring unless explore has been setup for org or user** —



## Classification & Scoring

- [ ] **LLM classification extensions** — add OpenAI (ChatGPT) alongside Claude; user-configurable classification prompt per LLM; user-adjustable criteria. Potentially add groq for even cheaper prices especially for the noga usecase. potential: groq (context length might be an issue if NOGA has to be provided), deepseek, 
- [ ] **Custom review & proposal categories** — keep sensible defaults, allow users to define own categories per account
- [ ] **Per-user scoring rules** — custom distance origin, keyword boosts/penalties, cluster weights; DB: `user_scoring_config` (1:1 with users) + `company_user_score` (per user/company); scoring service already accepts a config dict
- [ ] **Imporve Purpose Keyword extraction** - Seeing as this is central for NOGA and clustering, these keywords should be as accurate as possible. Review how it is done. Generally improve ML later as much as possible
- [ ] **NOGA Improvement** - More NOGA related improvements on displaying and other.
- [ ] **Semantic K Means unavailable** - cant find it in the Collection part


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
- [ ] **refund and other admin function** - check QOL of billing such as refunds and other methods. What happens when an automatic payment fails? -> subscription upgrades not working correctly, other functions not fully tested, definitely not complete. 
    - What happens to other users when an account is downgraded to free?
    - Subscription upgrade and downgrade flow



## Security & Infrastructure

- [ ] **Cloudflare evaluation** — assess Cloudflare for DDoS protection, CDN/caching of static assets, bot management, and WAF rules; compare cost vs current Hetzner LB + cert-manager setup; consider Workers for edge auth or rate limiting
- [ ] **CAPTCHA evaluation** — evaluate CAPTCHA (hCaptcha / Cloudflare Turnstile / reCAPTCHA v3) for signup, login, and scraping-triggering actions; weigh friction cost against bot/abuse risk at current and projected traffic
- [ ] **verify api security** - Test and verify the security of the API  which is pretty open (how is it secured against attackers, bots and crawlers/unofficial APIs). 
- [ ] **A General pass over security not jsut api** - WAF, bot protection etc
- [ ] **Testing suite** — introduce consistent testing suite



## Architecture & Refactoring


- [ ] **API key management** — token creation/revocation UI for org admins to manage their API credentials; currently only available via admin panel
- [ ] **uvicorn async** - Each open SSE connection holds one synchronous uvicorn worker thread (blocking I/O). At current scale (<50 concurrent users) this is fine; at higher scale the endpoint should be rewritten as `async def` with `anyio.sleep` and an async Redis client.
- [ ] **Github Action Secrets Mess** - Currently many github action secrets are thrown in there which are my ENV variables, this should be managed and documented much better. Especially when I implement a DEV/INT env I should seperate a lot of these variables



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