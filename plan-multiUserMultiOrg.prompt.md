## Plan: Multi-User + Multi-Org With Catalog + Overlays

Move to a 3-layer data model: (1) shared global “catalog” company facts (Zefix, geocode, immutable-ish), (2) org-shared workspace overlays for paid/customer configuration and outputs (e.g., Google scoring), and (3) private per-user overlays for free-tier personalization (notes, personal scoring overrides, private Claude results). Enforce org boundaries centrally via an org-context dependency and propagate org/user context into background jobs.

**Steps**
1. Define tenancy primitives (blocking for everything else)
   1) Make `organizations` the SaaS tenant/workspace (customer account).
   2) Introduce `organization_memberships` (user↔org many-to-many) with `role` (owner/admin/member/viewer) and `status` (active/invited).
   3) Choose a request-level org context mechanism:
      - Recommended: new org-scoped routes `/api/v1/orgs/{org_id}/...` plus optional `X-Org-Id` header for convenience.
      - Add `get_current_org()` dependency: validate membership; return org + role.
   4) Decide tier source-of-truth:
      - Recommended: `Organization.tier` is authoritative; `User.tier` becomes derived/legacy for now.

2. Split “catalog” vs “workspace overlay” data (schema design)
   1) Treat existing `companies` as the shared catalog (Zefix + geocode + stable identifiers).
   2) Create org-shared overlay table (name suggestion: `org_company_state`):
      - Keys: `org_id`, `company_id` (unique composite)
      - Org-shared editable fields: `tags`, `review_status`, `proposal_status`, `contact_name/email/phone`
      - Paid org-computed outputs: Google scoring fields and raw results (e.g., `website_url`, `website_match_score`, `google_search_results_raw`, `website_checked_at`, `social_media_only`)
      - Optional: “override” columns to shadow catalog fields without mutating catalog.
   3) Make notes private by adding per-user overlay:
      - Option A (minimal change): extend existing `notes` with `user_id` and `org_id` and enforce read/write via org context.
      - Option B (more flexible): `user_company_state` for notes + personal overrides + private Claude fields.
      - Recommended given your requirements: `user_company_state` for private personalization, and optionally keep `notes` as 1-to-many note history linked to `user_company_state`.
   4) Store private Claude results in user scope:
      - Put `claude_*` fields (freeform/category/raw) into `user_company_state`.
      - Keep any “standard Claude score” out of org overlays unless you later decide it should be shared.

3. Make settings multi-tenant (org-scoped settings overrides)
   1) Keep current `app_settings` as global defaults (admin-controlled).
   2) Add `org_settings` (or `app_settings` + `org_id` nullable) so each org can override scoring knobs.
   3) Update config loading in services to follow: org override → global default.
   4) Add audit logging for settings changes (who/when/old→new).

4. Propagate org/user context through all write paths (API + CRUD)
   1) New route patterns:
      - Catalog read: `/api/v1/catalog/companies` (read-only to all authenticated users)
      - Workspace data: `/api/v1/orgs/{org_id}/companies` returns merged view (catalog + org + user layers)
      - Writes for org-shared fields only under org routes; writes for private fields under user routes scoped by org.
   2) Update CRUD functions to accept `org_id` (and `user_id` where needed) and always filter on it.
   3) Centralize authorization:
      - Role gates: viewer(read), member(edit private), admin/owner(edit org-shared + settings)
      - Tier gates: free allows only private writes; paid allows org writes + job triggers.

5. Make background jobs tenant-safe (no global batch side effects)
   1) Add `org_id` + `user_id` to `job_runs`, `job_run_events`, `collection_runs`.
   2) When enqueuing jobs, always attach org_id/user_id.
   3) In workers, filter targets by org scope:
      - Operate over `org_company_state` rows (org’s “tracked companies”) rather than all `companies`.
   4) Write outputs to overlays only:
      - Google scoring writes to `org_company_state`.
      - Claude writes to `user_company_state` (per your “private” choice).
   5) Make job list/detail endpoints org-scoped.

6. Data migration strategy (minimize disruption)
   1) Bootstrap:
      - Create a “legacy org” and map existing users into it via memberships.
      - If you have existing single-tenant deployments, treat current state as that org’s baseline.
   2) Backfill:
      - For each company row that has org-like fields today (tags/status/contact/google fields), create `org_company_state` for the legacy org and move/copy those values.
      - For existing notes, assign to the author user where possible; otherwise assign to org owner and mark as migrated.
   3) Compatibility window:
      - Keep old columns on `companies` temporarily; API reads from overlays first and falls back to old columns until fully migrated.

7. Frontend updates (minimal but necessary)
   1) Add `/api/v1/auth/me` response shape: user + memberships + default org.
   2) Add org selector (simple dropdown) and store selected org id (cookie preferred).
   3) Update API client to include org context (path or `X-Org-Id` header).
   4) Update pages to call org-scoped endpoints; keep catalog pages read-only if needed.

**Relevant files**
- Backend models: `app/models/user.py`, `app/models/organization.py`, `app/models/company.py`, `app/models/note.py`, `app/models/app_setting.py`, `app/models/job_run.py`, `app/models/job_run_event.py`, `app/models/collection_run.py`, `app/models/audit_log.py`
- Backend routes: `app/api/routes/auth.py`, `app/api/routes/companies.py`, `app/api/routes/notes.py`, `app/api/routes/jobs.py`, `app/api/routes/ops_settings.py`
- CRUD: `app/crud/company.py`, `app/crud/note.py`, `app/crud/app_setting.py`, `app/crud/job_run.py`, `app/crud/audit_log.py`
- Services: `app/services/collection.py`, `app/services/job_worker.py`, `app/services/scoring.py`
- Frontend API: `frontend/src/lib/api.ts`

**Verification**
1. Add/extend tests to ensure org isolation:
   - A user in org A cannot read/write org B overlays.
   - Free tier can create/update `user_company_state` but cannot trigger org jobs or modify org settings.
   - Paid org admin can trigger Google scoring and see org-shared results.
2. Add migration test/fixture that creates legacy org and validates merged “company view” matches pre-migration behavior.
3. Manual smoke:
   - Two orgs, same catalog company: different Google scores in each org; same catalog facts.
   - Two users in same org: org-shared fields match; private Claude/notes differ.

**Decisions**
- Multi-company interpreted as multiple customer org/workspaces with multiple users.
- Standard data is a shared global catalog; customizations are overlays.
- Private per-user: notes, personal score overrides, Claude prompt results/freeform.
- Org-shared by default: Google scoring results, workflow statuses, tags, contact fields (unless you later change it).

**Further Considerations**
1. Optional hardening: Postgres Row-Level Security (RLS) keyed by org_id as defense-in-depth.
2. Performance: composite indexes on `(org_id, company_id)` and partial indexes for frequently queried statuses.
3. Data retention: decide retention policy for raw Google results JSON per org (storage cost).
