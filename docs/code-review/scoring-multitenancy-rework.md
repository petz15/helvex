# Scoring & Multi-Tenancy Rework — Design Reference

> Status: **approved design, not yet implemented** (2026-07-17).
> Goal: make flex / web / AI scores **per-org and per-user**, never global, without
> re-running expensive extraction per org. Raw facts are extracted once and shared;
> only *scoring* is scoped.

---

## 1. Why

Today scores are written onto the global `companies` table (`web_score`, `ai_score`,
`combined_score`, `flex_score`, `website_url`, …). Consequences found during review:

- Org-scoped enrichment/crawl jobs overwrite the **global** row, so one org's work
  changes what every other org sees (`web_crawl.py` writes `Company.web_score`; the
  intended per-org sink `update_org_google_results` is **never called** — dead code).
- `_overlay()` merges only workflow fields; scores are read straight off `Company`,
  contradicting the `OrgCompanyState` docstring ("always read from the overlay").
- `combined_score` is global but derived from a supposedly-private `ai_score`.
- The `OrgCompanyState` web-score columns are dead shadow columns → silent drift.

Different orgs/users have fundamentally different targeting (media-rich sites vs.
social-only vs. proximity-to-me + industry). A single global score cannot serve them.

## 2. Core principle

**`companies` and the raw tables hold facts extracted once. No score ever lives there.**
Scores live only in scope-keyed overlays. Extraction is global and shared; scoring is
per-scope and cheap (pure function of global facts × a config dict).

## 3. Target model

| Tier | Table | Holds | Key |
|---|---|---|---|
| Global facts | `companies`, `company_web_extract`, `company_web_page`, SERP raw JSON | Zefix, NOGA, geocode, crawl extracts, `image_count`/`video_count`/`has_contact_form`/`word_count`/`lang`, `website_status`, `social_media_only` | `company_id` |
| Org AI | `org_company_ai` (new) | promoted `ai_score` + `ai_data` JSONB (summaries, future prompt outputs) | `(org_id, company_id)` |
| Org score set | `company_score` (new), `user_id IS NULL` | `flex`, `web`, `combined` (includes org AI) | `(org_id, NULL, company_id)` |
| User score set | `company_score`, `user_id = N` | `flex`, `web`, `combined` recomputed with the user's overridden `scoring_*` (AI pulled from org) | `(org_id, user_id, company_id)` |
| User personal | `personal_score_override` | manual per-user pin | `(user_id, company_id)` |

`user_company_state` is **retired**: its AI columns migrate to `org_company_ai`;
`personal_score_override` moves to a thin user-scoped home.

### 3.1 `company_score` (minimal, materialized)

```
id, org_id, user_id NULL, company_id,
flex_score, web_score, combined_score,
config_version, scored_at
```
Index: `(org_id, user_id, combined_score DESC)` for sort/filter/paginate.
Scaling lever if needed: partition by `org_id`. ~40 B/row → 700k × 100 orgs ≈ 2.8 GB.

### 3.2 `org_company_ai` (extensible)

```
org_id, company_id,
ai_score INT NULL,          -- promoted: the one score combined_score + sort use
ai_data JSONB NULL,         -- ai_category, ai_freeform, per-company summaries,
                            --   and future AI outputs that don't need indexed sort
updated_at
```

Decisions locked:
- **AI is org-shared** (paid/computed once per org, reused by all members).
- **Backfill:** seed `ai_score` into **every** org from the current global `Company.ai_score`
  (free starting point — decision 1a).
- **JSONB + promoted column** (decision 2): sortable/filterable value is the column;
  evolving fields live in JSONB, no migration per new AI feature.

**Forward-compat — multiple named prompt-scores.** The roadmap includes org-defined AI
prompts, each producing its own sortable score ("rank by multimedia", "near me + works
with cars"), plus per-company summaries. When those land they get dedicated tables so
each score is independently indexable — **without** changing `org_company_ai`:
```
org_ai_prompt(org_id, id, name, prompt_text, is_primary, …)
org_ai_prompt_score(org_id, company_id, prompt_id, score, summary, scored_at)
```
`org_company_ai.ai_score` stays the org's *primary* prompt score (the one feeding
`combined_score`); auxiliary prompt scores live in `org_ai_prompt_score`. Summaries and
one-off AI aids that aren't sorted on stay in `ai_data` JSONB. **Not built now** — noted
so the current shape doesn't have to change later.

## 4. Config resolution (org default + user override)

- `effective_config(user) = merge(org scoring_* settings, user scoring_* overrides)`.
- A user may override **all** `scoring_*` keys.
- A user owns a materialized set (`user_id = N`) iff they have any override. Otherwise
  they read the org-default set (`user_id IS NULL`).
- AI is always taken from the org set — a user's overrides only re-weight flex/web and
  the AI/flex/web combination, never re-run AI.

Result: **one indexed join, one `user_id` predicate** per read — no per-row COALESCE.
At request time resolve the scope once: `user_id = N` if the user has overrides else NULL.

## 5. Materialization job — `rescore_scope`

Org- or user-scoped. Follows the existing chunked-batch pattern (keyset by `company.id`,
`LIMIT` batches — never load 700k into memory).

```
for each 5k batch (keyset paginated by company.id):
    load global facts (flex inputs, web raw, web-page features)
    cfg = resolve_config(scope)                # org defaults or merged user overrides
    flex = compute_flex_score(config=cfg)      # existing fn — unchanged
    web  = score_from_stored_results(cfg)      # existing scorer — unchanged
    ai   = org_company_ai.ai_score             # org scope
    combined = weighted(flex, web, ai, cfg)
    upsert company_score(scope, company_id, …)
```

Triggers: org config saved · user override saved · org AI run completes · new global
facts land (crawl/NOGA/geocode batch enqueues re-score for affected scopes).
Dedup key `rescore:{org_id}:{user_id or '-'}` (one active per scope).

## 6. Read paths to migrate (join `company_score` at the resolved scope)

- `crud/company.py::list_companies` — filter / sort / paginate (drop `Company.*_score` refs)
- `app/api/routes/map.py` — `min/max_web_score`, clustering
- `app/services/platform/csv_export.py`
- `app/api/routes/companies/_shared.py::_overlay` — merge scores in, not just workflow
- saved-view alert sweep, `companies/stats`

## 7. Migration phases (non-breaking)

1. **Facts split** — stop writing scores to `Company` (columns become read-only during
   transition). Guardrail test: no `org_id`-scoped path writes
   `Company.web_score/flex_score/combined_score`.
2. **New tables** — create `company_score` + `org_company_ai`; backfill
   `company_score(user_id NULL)` from current global scores and `org_company_ai` from
   global `Company.ai_score` into every org (1a). Reads don't regress.
3. **Job** — add `rescore_scope`; wire config-save + fact-update + AI-complete triggers.
4. **Read cutover** — switch list/map/CSV/overlay to the scope join behind a flag;
   verify sort parity against the pre-cutover global ordering.
5. **Config layer** — org-default + user-override merge; expose user `scoring_*`
   overrides in the settings UI; per-scope job trigger buttons + status.
6. **Cleanup** — drop `OrgCompanyState` web shadow columns, `user_company_state` AI
   columns, and the legacy `Company` score columns.

## 8. Frontend wiring (phase 5)

- Settings: user-level `scoring_*` override panel (inherits org defaults, shows deltas).
- Job trigger + status indicator for `rescore_scope` (org admins: re-score org; any user
  with overrides: re-score me).
- Company list/map/CSV read scoped scores transparently (no UI change beyond source).

## 9. Open / deferred

- Named prompt-scores + per-company AI summaries → §3.2 forward-compat tables, later.
- `personal_score_override` final home: column on the user's `company_score` rows vs.
  thin `user_company_override` table (lean: column).
- `company_score` partitioning: defer until row count warrants it.
