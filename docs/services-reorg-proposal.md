# Proposal: regroup `app/services/` into domain subpackages

**Status:** ✅ executed (2026-07-16). Clean move, no shims — all imports rewritten to the new paths.
**Verification:** `import app.main` OK; `pytest` 106 passed / 2 pre-existing failures (unrelated `test_collection_batch`). Every moved file byte-verified equal to `rewrite(HEAD)` modulo intended edits.

### Notes from execution (gotchas that bit)
- **`__file__`-relative resource paths break on move.** Four modules resolved data/static files via `Path(__file__).parents[...]`; moving them one directory deeper required `+1` parent level: `ml/noga.py` + `ml/noga_lookup.py` (`parents[2]→[3]`, repo-root `noga_lookup.json`), `ml/cluster_pipeline.py` (`app/static/`), `billing/payments/pricing.py` (`app/data/eu_vat_rates.json`). Always grep moved files for `__file__` and fix path depth.
- **Existing subpackages** `job_handlers`→`jobs/`, `payments`→`billing/`, `providers`→`platform/` moved as whole units; their internal imports rewrote fine.
- **`services/__init__.py`** re-exports from `collection` → now `from app.services.ingestion.collection import …`.
- **Non-import path references** also updated: `.github/workflows/*.yml` (both the CI smoke-import lines and the `paths:` change-filter globs), ARCHITECTURE.md, CLAUDE.md, and the docs/ tree.

## Motivation

`app/services/` is 46 flat modules. They cluster cleanly into ~8 domains. A subpackage
layout makes ownership obvious and — importantly — the `ml/` group would mirror the
`helvex-ml` image boundary (the modules that only import cleanly inside the ML worker
image), surfacing "what needs the heavy image" in the directory tree instead of only in
the hand-maintained Helm `JOB_TYPE_WHITELIST`/`BLACKLIST` strings.

## Proposed layout

```
app/services/
├── jobs/            job_worker.py, job_handlers/, rate_limit.py
├── ingestion/       collection.py, zefix_import.py, uid_import.py, incremental_classify.py
├── registry/        shab_import.py, shab_archive_import.py, simap_import.py,
│                    simap_archive_import.py, sogc_preprocessor.py,
│                    sogc_person_extractor.py, sogc_entity_resolver.py
├── enrichment/      crawler_http.py, crawler_playwright.py, crawler_common.py,
│                    crawler_sitemap.py, crawler_extract.py, directory_extract.py,
│                    website_status.py, web_enrichment.py, geocoding_pipeline.py
├── ml/              noga.py, noga_pipeline.py, noga_lookup.py, language_detection.py,
│                    embeddings.py, company_embedding_pipeline.py, cluster_pipeline.py,
│                    stopword_discovery.py, boilerplate_analysis.py, _pipeline_utils.py
├── scoring/         scoring.py, claude.py, claude_classify.py
├── billing/         billing_addresses.py, billing_renewal.py, payment_transactions.py,
│                    credits.py, tiers.py
├── notifications/   email.py, saved_view_alerts.py, activity.py
└── platform/        s3_client.py, csv_export.py, llm.py
```

(Grouping is a starting point — e.g. `incremental_classify.py` could arguably live under
`scoring/` or `ml/`; settle borderline cases during execution.)

## Execution notes (when picked up)

- **Wide but mechanical.** Every `from app.services.X import Y` across `app/api/routes/`,
  `app/services/jobs/job_handlers/`, `app/main.py`, `scripts/`, and `tests/` must move —
  ~200+ import sites. Low intellectual risk, high churn.
- **No shims** (per decision above): rewrite call sites, delete the old paths.
- **Do it in isolation** — nothing else in flight — one subpackage at a time, running
  `pytest` + an import smoke test (`python -c "import app.main"`) after each group so a
  missed import is caught immediately rather than at runtime on a specific pod.
- **Watch the `ml/` group vs the Helm whitelists.** Moving files does not change job-type
  strings, but it's the natural moment to cross-check that the ml-worker
  `JOB_TYPE_WHITELIST` and api-worker `JOB_TYPE_BLACKLIST`
  (`infra/charts/helvex/templates/*-deployment.yaml`) still match the handlers that
  actually need the `helvex-ml` image.
- **Local imports inside functions** (the codebase uses many `from app.services.x import y`
  *inside* functions, e.g. job handlers) must be updated too — grep for the full
  `app.services.` prefix, not just top-of-file imports.

## Pod/image boundary reference (why `ml/` matters)

Job routing is pure config, not code: all pods poll the one `job_runs` table and filter by
`JOB_TYPE_WHITELIST`/`JOB_TYPE_BLACKLIST` (`app/services/jobs/job_worker.py`). The ml-worker runs
the `helvex-ml` image (`Dockerfile.ml` → `Dockerfile.ml-base`) which bundles
sentence-transformers/torch + spaCy models + geocoding DB; the main `helvex-app` image omits
them. So ML job types *must* run on ml-worker. See ARCHITECTURE.md §16 "Pod topology" and the
image table for the authoritative description.
