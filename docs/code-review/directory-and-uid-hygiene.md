# Directory-domain handling + UID source hygiene

**Status:** planned, not implemented. For review.

## Context

Two connected problems, both surfaced by the identity-mismatch investigation
(`related-entity-identity.md`): of 12,195 MISMATCH extracts, ~91% are not subsidiaries but
simply the **wrong site** — directory listings, aggregators and PDFs carrying some other
company's UID. Fixing that is partly a candidate-quality problem (which domains may be
selected as a company website) and partly an evidence problem (which pages may be trusted
to *contradict* a company's identity).

### What the code does today

| mechanism | where | question it answers |
|---|---|---|
| `_DIRECTORY_DOMAINS` → `CRAWL_BLOCKED_DOMAINS` | hardcoded frozenset, `scoring.py` | never a company's own site |
| `directory_crawl_domains` (`status='approved'`) | DB + review UI | **both** the above **and** the below |
| `DIRECTORY_CRAWL_DOMAINS` | hardcoded frozenset, `web_crawl.py:1603` | harvest profile data from it |
| `_DISCOVERY_SKIP_DOMAINS` | hardcoded frozenset, `web_crawl.py` | never even propose for review |

**The blocklist composition is already good and must not change.**
`get_effective_crawl_blocklist` unions the hardcoded floor with DB-approved domains, caches
for `_BLOCKLIST_TTL`, and on DB failure *falls back to the static set* rather than crawling
with an empty blocklist. Moving that floor into the DB would delete a deliberate safety
property. An earlier draft of this plan proposed "unify the registry" — that was wrong, and
this plan does not do it.

### The three real defects

1. **`status='approved'` does double duty.** `get_effective_crawl_blocklist` reads approved
   rows as *"block from candidate selection"*; `handle_directory_crawl` reads the same rows
   as *"harvest profile data from this"*. There is **no way to express "block, but never
   harvest"** — exactly the wanted state for `kompass.ch`, `moneyland.ch`,
   `business-monitor.ch`.

2. **The harvest list is either/or, not a union** (`web_crawl.py:1675`):
   ```python
   domain_list = list(db_domains) if db_domains else list(DIRECTORY_CRAWL_DOMAINS)
   ```
   Approving a *single* domain in the DB silently discards the entire hardcoded harvest
   list. The blocklist unions; this one replaces. Latent today only while the table is empty.

3. **Two concrete list errors.** `kompass.ch` / `kompass.com` sit in `DIRECTORY_CRAWL_DOMAINS`
   (we would spend directory crawls on them) despite being explicitly unwanted.
   `moneyland.ch` is in *neither* list, so it is currently **selectable as a company website**.

### Where directory detection happens

`is_directory_page` runs at **extract** time (`web_crawl.py:1079`), on the homepage HTML,
*before* `resolve_company_extract` — so a detected directory is rejected before it can be
confirmed. That ordering is correct. The cost is that phase A has already fetched and
S3-stored ~5 pages by then.

---

## Step 1 — Separate "block" from "harvest" (revised: no registry unification)

Keep both hardcoded frozensets as the floor. Add the missing **dimension**, not a new home
for the data.

- Migration: add `harvest: bool NOT NULL DEFAULT FALSE` to `directory_crawl_domains`.
  `status` keeps its meaning (`pending_review` / `approved` / `rejected`, approved ⇒ blocked
  from candidate selection). `harvest` is independent.
- `get_effective_crawl_blocklist` — **unchanged**. Still floor ∪ approved.
- `handle_directory_crawl` — replace the either/or with a union, filtered on the new flag:
  ```python
  domain_list = sorted(DIRECTORY_CRAWL_DOMAINS | get_harvestable_directory_domains(db))
  ```
  New CRUD `get_harvestable_directory_domains(db)` → `approved AND harvest`.
- Remove `kompass.ch` / `kompass.com` from `DIRECTORY_CRAWL_DOMAINS`; add `moneyland.ch` to
  `_DIRECTORY_DOMAINS` so it stops being selectable.
- Review UI: the approve action splits into **Block** (status=approved, harvest=false) and
  **Block + harvest** (status=approved, harvest=true). Reject unchanged.

Net: `local.ch` / `treuhandvergleich.ch` → blocked **and** harvested;
`kompass.ch` / `moneyland.ch` / `business-monitor.ch` → blocked, never harvested.

**Files:** `alembic/versions/` (new), `app/models/directory_crawl_domain.py`,
`app/crud/directory_crawl_domain.py`, `app/services/jobs/job_handlers/web_crawl.py`
(`:1603`, `:1675`), `app/services/scoring/scoring.py`,
`frontend/src/app/[locale]/app/admin/crawler/crawler-client.tsx`, `frontend/src/lib/api.ts`.

## Step 2 — UID evidence asymmetry

The rule: **a UID may prove identity from anywhere, but may only *disprove* it from a page
we trust.**

- **Positive (unchanged):** if the target's Zefix UID appears on *any* crawled page, that is
  a match. An exact match to Zefix cannot be a false positive, so page type is irrelevant.
  Restricting this to impressum/contact would lose the very common site-wide-footer UID.
- **Negative (new):** only set `uid_matches_zefix = False` when the contradicting UID came
  from an `impressum` or `contact` page. A foreign UID on an arbitrary page is weak grounds
  for MISMATCH — and MISMATCH currently triggers permanent candidate rejection. Otherwise
  leave `None` ⇒ `MATCH_WEAK`, no rejection, no cross-attribution.
- **Multi-UID guard (new):** if any single page carries **≥3 distinct checksum-valid UIDs**,
  treat it as a listing page and exclude it from the negative verdict. This catches
  directory pages without classifying the *site*, and is the cheapest defence against the
  SHAB-PDF / aggregator cohort.

`resolve_company_extract` already tracks `uid_by_page`, so both changes are local to the
UID-resolution block (`crawler_extract.py:~985-999`) and stay DB-free.

**Files:** `app/services/enrichment/crawler_extract.py` only.

## Step 3 — One-time discovery backfill

`discover_directory_domains` already scans `company_url_candidates` for high-frequency
domains, skips `_DISCOVERY_SKIP_DOMAINS` and known ones, and inserts the rest as
`pending_review`. It has never been run at a low threshold.

- Run with `min_companies` ≈ 20–30, `limit` 500.
- Review the queue; classify each as Block / Block + harvest / Reject.
- **Keep the human gate.** A false positive permanently hides a real company website.
  Auto-approval only for domains appearing across a very large number of companies
  (proposed ≥500 distinct), where a mistake is implausible.

## Step 4 — Keep the list current automatically

- Add `discover_directory_domains` to the nightly scheduler in `app/main.py` (alongside
  `shab_daily` / `reclassify_noga`).
- Auto-enqueue at the **end of `web_search_batch`**, i.e. once new Serper results land.
- **Not** before `web_extract`: the blocklist only affects `bulk_select_best_candidates`, so
  blocking a domain after selection changes nothing until candidates are re-selected. The
  useful moment is right after new candidates appear.
- Newly approved domains take effect within `_BLOCKLIST_TTL` via the existing cache.

## Step 5 — Abort a directory crawl after the homepage

Move the cheap half of detection earlier so a directory costs 1 fetch instead of 5.

- In `crawl_company_http`, after the homepage fetch and before subpage discovery, run
  `is_directory_page(body, url)`; on a hit return a `CrawlResult` with
  `failure_status="directory"` and no pages.
- Keep the extract-time check as the backstop — it sees more pages and stays authoritative.
- Saves 4 fetches + 4 S3 uploads per directory candidate.

## Not doing

- **Raising cross-attribution priority.** `add_cross_attributed_url_candidate` uses
  `score=0.5` and does not auto-select. Since ~91% of mismatches are wrong-site cases, most
  cross-attributions originate *from* directory/PDF pages; promoting them would push
  directory URLs onto other companies as high-priority candidates and amplify the bug.
  Measure first: `SELECT COUNT(*) FROM company_url_candidates WHERE source = 'cross_attributed';`
- **Detecting directories during phase B.** If a directory reached phase B, identity was
  wrongly confirmed and the defect is upstream.

## Tests

- `harvest` independence: approved+harvest=false is in the blocklist but **not** the harvest
  list; approved+harvest=true is in both. Pins the kompass case.
- Harvest list is a **union** of floor and DB, not a replacement — approving one domain must
  not shrink it (pins defect 2).
- `kompass.ch` not harvestable; `moneyland.ch` in the effective blocklist.
- UID positive: target UID on a `services` page still yields `uid_matches=True`.
- UID negative: foreign UID on a `services` page yields `None`, **not** `False`, and the
  candidate is **not** rejected.
- UID negative: foreign UID on `impressum` still yields `False` (no regression).
- Multi-UID guard: a page with 4 valid UIDs, none matching, yields `None`.
- Early abort: a directory homepage yields 0 pages and fetches no subpages.

## Verification

1. Baseline, then re-run after step 2 + a re-extract:
   ```sql
   SELECT identity_category, COUNT(*) FROM company_web_extract GROUP BY 1;
   ```
   Expect MISMATCH (12,195) to fall and MATCH_WEAK to rise — the goal is fewer *false*
   rejections, not more confirmations.
2. `SELECT COUNT(*) FROM company_url_candidates WHERE status='rejected';` should stop growing
   as fast.
3. After step 3: check the review queue, and that `local.ch` / `treuhandvergleich.ch` land as
   Block + harvest while `kompass.ch` lands as Block only.
4. After step 5: `stats["directory_blocked"]` rises while pages-per-directory-company drops
   to 1.

## Open questions

- Auto-approval threshold in step 3 — proposed ≥500 distinct companies. Too aggressive?
- Should `DIRECTORY_CRAWL_DOMAINS` eventually be seeded into the DB as
  `approved + harvest=true` and retired? This plan deliberately keeps it as a permanent
  floor instead, matching the blocklist pattern.
- Step 2's multi-UID threshold (≥3) is a guess. Worth measuring the distribution of distinct
  UIDs per page first — a query against stored extracts would settle it.
