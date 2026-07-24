# Web Pipeline — Holistic Rework (consolidated design)

> Status: **approved scope, not yet implemented** (2026-07-24).
> Consolidates and supersedes as the single reference for web work:
> - [`web-identity-rework.md`](web-identity-rework.md) — identity split + evidence ledger (folded in as Layer B).
> - [`scoring-multitenancy-rework.md`](scoring-multitenancy-rework.md) — per-scope scores (folded in as Layer D; still the authority on the score tables).
>
> Goal: turn the loosely-coupled relay (search → verdict → crawl → extract → score,
> each stage with its own private notion of "confidence") into **one linked pipeline**
> that (a) decides *identity* from post-crawl evidence, (b) ingests the *whole site* into
> a structured page inventory + facts, and (c) presents a single holistic company web
> profile — with scoring pushed out to the per-org layer.

---

## 0. The problem, restated

The current stages each answer their own question with an overloaded scalar, and the rich
data the crawler already grabs is stranded. Concretely (verified against current code):

- `web_score` / additive `confidence` conflate **identity** (is it theirs?), **content**
  (is the site good?) and **fit** (does it matter to *this* org?) — a weak-everything 0.62
  is indistinguishable from a strong-address-no-UID 0.62 (`crawler_extract.resolve_company_extract`).
- Pre-crawl snippet scores leak into the final verdict when no crawl evidence exists
  (`website_status.classify_search_results` is the fallback in `compute_verdict`).
- Ambiguity is hidden — `compute_verdict` silently max-picks the best candidate per tier.
- The crawler already discovers the sitemap (`crawler_sitemap.discover_site_overview`) but
  only uses it to *fill 5 crawl slots* (`max_pages=5`); it never persists a page inventory,
  and page-type classification stops at homepage/impressum/privacy/other.
- Extracted `persons`, `service_keywords`, `socials`, `address` exist on
  `company_web_extract` but barely feed NOGA / AI / the profile UI.

## 1. Target architecture — four layers, one flow

```
                 ┌── crawl ordering only (zero scoring weight) ──┐
search (Serper) → candidates ─────────────────────────────────────┐
                                                                   ▼
        ┌──────────────────────── LAYER A: INGESTION ─────────────────────────┐
        │  sitemap + robots + nav  →  full PAGE INVENTORY (company_web_pages)  │
        │  page-type classifier: home/about/team/services/products/refs/…      │
        │  crawl the useful ones  →  raw HTML in S3  →  extract_page per page   │
        └──────────────────────────────────────────────────────────────────────┘
                 │ per-page structured signals + cleaned text
                 ▼
   ┌── LAYER B: IDENTITY (global truth) ──┐   ┌── LAYER C: CONTENT (global facts) ──┐
   │ evidence ledger per candidate        │   │ description, services, team/persons, │
   │ → (probability, category)            │   │ contacts, socials, firmographics,    │
   │ MATCH_UID / STRONG / WEAK / AMBIGUOUS│   │ languages, media counts, keywords    │
   │ / RELATED_ENTITY / MISMATCH / NO_SITE│   │ → feeds NOGA + AI + profile display  │
   │ / UNKNOWN  (auto-pick, no silent max)│   │ (extract once, shared across orgs)   │
   └───────────────┬──────────────────────┘   └──────────────────┬───────────────────┘
                   └──────────────► global facts ◄────────────────┘
                                        │
                          ┌── LAYER D: FIT (per-org) ──┐
                          │ company_score + org_company_ai │  ← scoring-multitenancy-rework
                          │ web_score retired from combined │
                          └────────────────────────────────┘
```

**Rule of the rework:** identity + content are **global, extracted once, shared**. Fit is
**per-org**, computed cheaply from those facts. No score ever lives on `companies` again.

---

## 2. Layer A — Ingestion & page inventory (the expansion)

Today: `crawl_company_http` fetches homepage + up to 4 subpages, best-effort from nav +
sitemap. We keep this machinery and extend it in three ways.

### 2.1 Persist the full page inventory (even uncrawled pages)

`discover_site_overview` already returns the sitemap/robots URL set. Today it's discarded
after filling crawl slots. Instead: **persist every discovered URL** as a
`company_web_pages` row with `page_type` classified and `crawled=false` until (if) we crawl
it. This gives the "overview of what pages exist on the website" directly, cheaply, with no
extra fetches.

- `company_web_pages` gains: `discovered_via` (`sitemap|robots|nav|homepage`),
  `crawled` (bool), `priority` (int — crawl ordering). `needs_extraction` already exists.
- New CRUD: `upsert_page_inventory(company_id, [(url, page_type, discovered_via)…])`.
- Homepage crawl still runs first; inventory is written alongside it.

### 2.2 Extend the page-type taxonomy + classifier

`classify_urls_by_path` currently maps a handful of paths. Extend the vocabulary and the
path/nav-anchor heuristics (DE/FR/IT/EN) to a stable enum:

| page_type | matches (multilingual) |
|---|---|
| `homepage` | `/` |
| `about` | über-uns, about, unternehmen, portrait, qui-sommes-nous, chi-siamo |
| `team` | team, mitarbeiter, people, geschäftsleitung, vorstand, équipe, management |
| `services` | leistungen, services, angebot, dienstleistungen, prestations, servizi |
| `products` | produkte, products, produits, sortiment, shop |
| `references` | referenzen, projekte, portfolio, cases, kunden |
| `contact` | kontakt, contact, contatto |
| `impressum` | impressum, legal, mentions-légales, chi-siamo/impronta |
| `privacy` | datenschutz, privacy, confidentialité |
| `news` | news, blog, aktuelles, medien, presse |
| `jobs` | jobs, karriere, carrière, stellen, offene-stellen |
| `other` | fallback |

Classifier order: JSON-LD `WebPage`/breadcrumb type → path segment → nav-anchor text →
`other`. Keep it deterministic (no API).

### 2.3 Smarter crawl budget (replace flat `max_pages=5`)

Priority-ordered crawl instead of first-5: always crawl `homepage`, `impressum`/`contact`
(identity + firmographics), `about`, `team`, `services`, `products` when present; skip
`privacy`/legal boilerplate for text mining (still inventory them). Budget stays bounded
(config `crawl_max_pages`, default raise 5 → 8; `crawl_max_pages_priority_tier` higher for
paid orgs — Layer D gating). Sitemap-only pages beyond budget stay in inventory as
`crawled=false` and can be pulled on demand from the profile UI ("crawl this page").

### 2.4 Per-type structured extraction (Layer C inputs)

`extract_page` already returns `PageSignals` (emails/phones/socials/uid/address/desc/text).
Add page-type-aware extraction so team/services pages become structured:

- **`team`** → `persons` with roles: parse repeated name+role blocks (headings/cards),
  NER fallback. Writes to the existing `persons` field, upgraded from `list[str]` to a
  structured `list[{name, role, page_url}]` (new column `persons_struct` JSONB; keep
  `persons` text array for back-compat/search).
- **`services`/`products`** → `services_struct` JSONB `list[{title, summary}]` from
  headings + following paragraph; plus the existing `service_keywords`.
- **`about`/homepage** → best `description` (already), longest cleaned paragraph as
  `about_text` for the ML feed.

All deterministic. The optional Claude Haiku summary layer (§6) sits on top, gated.

---

## 3. Layer B — Identity + presence (folds in `web-identity-rework.md`)

**Primary output is a company-level web-presence verdict with a confidence**, derived from
the per-candidate identity decisions:

| presence verdict | meaning | how derived |
|---|---|---|
| `HAS_SITE` (+ confidence 0–1) | high confidence the company has an own website | best candidate resolves to `MATCH_UID`/`MATCH_STRONG`/`MATCH_WEAK` |
| `SOCIAL_ONLY` | no own domain, but a genuine social profile exists | only social candidates cleared |
| `DIRECTORY_ONLY` | only registry/directory listings (moneyhouse, local.ch…) | only directory candidates |
| `NO_SITE` (+ confidence) | high confidence there is **no** website | searched/crawled, nothing credible — this is a *positive* "no site" signal, not absence of data |
| `UNKNOWN` | not yet searched/crawled, or crawl yielded no usable evidence | never a snippet-derived guess |

The two things that must stay separate: **presence** ("has a site: how sure?") is global
truth and lives here; **evaluation** ("is that web presence any good *for my targeting*?")
is per-org and lives in Layer D. This layer never scores site *quality*.

Per-candidate identity (below) is what backs the verdict; the concrete deltas vs current code:

### 3.1 Evidence ledger, persisted

Replace the additive `confidence` in `resolve_company_extract` with a typed ledger per
candidate — persisted on `company_web_extract` as `evidence` JSONB:

```
{ dimension, direction: +/-, strength: decisive|strong|medium|weak, value }
  uid_matches_zefix   + decisive   CHE-123.456.789   (checksum already validated on extract)
  domain_is_name      + strong     muster.ch == "Muster AG"
  address_zip_city    + medium
  phone_matches_reg   + medium     (new signal — cross-check registry phone if available)
  purpose_sim         + weak       0.71              (already computed, ML worker)
  name_in_title       + weak
  -is_marketplace / -is_parked / -uid_of_other_entity
```

The hand-tuned weights become the ledger's features. A deterministic weighted combine turns
the ledger into `(identity_probability, identity_category)`. GBM later (ledger IS its
feature vector) — serialize to `app/services/url_confidence_model.joblib`, fall back to
deterministic combine if absent.

### 3.2 Categorical outcomes replace the confidence ladder

New `identity_category` column on `company_web_extract`:
`MATCH_UID | MATCH_STRONG | MATCH_WEAK | AMBIGUOUS | RELATED_ENTITY | MISMATCH | NO_SITE | UNKNOWN`.

- `compute_verdict` / `_extract_tier` in `website_status.py` rewritten to emit categories,
  **not** silent max-pick. `AMBIGUOUS` (≥2 candidates with comparable strong evidence) →
  **auto-pick** via independent tiebreak (own-socials link → UID-bearing → registry-phone/
  address → exact-name domain); record the tiebreak reason, keep the runner-up for re-decide.
- **Pre-crawl snippet score → crawl ordering ONLY.** No-crawl companies become `UNKNOWN`,
  never a snippet-derived "likely". `classify_search_results` demoted to a crawl prioritizer;
  it stops writing any persisted score.

### 3.3 UID beyond binary

Checksum is already validated at extraction (`_extract_uid`). Add at *comparison* time:
- **Absence ≠ mismatch** (already modelled: `uid_matches_zefix` tri-state) — keep explicit.
- **Related-entity awareness** — on a present-but-mismatching UID, look it up in the SOGC
  person/entity + takeover graph already built; parent/subsidiary → `RELATED_ENTITY` (not a
  flat penalty). Reuses the `persons`/entity resolution pipeline.

### 3.4 Re-decide job (no re-crawl)

Persist ledger + category + probability per candidate. A `redecide_identity` job re-runs the
combine step on stored evidence (same pattern as the existing `reextract` loop) — cheap,
enables model swap and threshold tuning without re-crawling.

---

## 4. Layer C — Content facts feed ML + profile

The point of "grab as much relevant data" is that it must **link back**:

- **NOGA** — feed `about_text` + `services_struct` titles + `service_keywords` into the
  NOGA classifier's text (language-matched). Directly fixes the roadmap pain of thin-`zweck`
  companies (e.g. IDs 366482/367779) where the website is the only real signal.
- **AI scoring / summary** — cleaned per-type text bounds tokens for the optional Haiku
  layer (§6); `services_struct` + `about_text` become the AI prompt context instead of raw
  purpose alone.
- **Clustering / keywords** — `service_keywords` already exist; route them through the same
  stopword/boilerplate discovery already in `stopword_discovery.py`.
- **People graph** — `persons_struct` (name+role+source page) resolves against SOGC signers
  (roadmap "Web extract — persons → People graph").

All Layer C data is **global** on `company_web_extract` / `company_web_pages` — extracted
once, shared across orgs. Nothing here is org-scoped.

---

## 5. Layer D — Fit / scoring (folds in `scoring-multitenancy-rework.md`)

Coupled per the user decision. That doc stays the authority; the web-specific consequences:

- The **per-org `web_score` is a fit function** over the global presence verdict (Layer B) +
  Content features (Layer C), weighted by *that org's* targeting. It answers "how well does
  this company's web presence match what I'm looking for", e.g.:
  - image-light sites (low `image_count` across pages) — a proxy for non-marketing/industrial;
  - presence of **specific service claims** (`services_struct` title/keyword match to an
    org-defined term);
  - page breadth / media richness / has-contact-form / languages offered;
  - presence verdict itself (an org may only want `HAS_SITE`, or deliberately hunt
    `NO_SITE`/`SOCIAL_ONLY` companies as a modernization lead signal).
  These become weighted features in the org/user `scoring_*` config, computed cheaply by
  `rescore_scope` into `company_score` — **not** a single global number.
- `web_score` is **retired from `companies`** and from the global `combined_score`.
- Identity (`identity_category`, probability) + presence verdict + Content stay global; only
  the fit evaluation is scoped. The same crawl feeds every org; each org weighs it differently.
- Sequencing: land Layer B's `identity_probability` first so `compute_combined_score` can
  read it during transition, then cut `web_score` when the score tables go live (avoid
  splitting the score-column work twice — see the ROADMAP "Company table normalization" note).

---

## 6. Optional LLM enrichment (gated, deferred within this program)

On top of deterministic Layer C: a Claude Haiku pass over cleaned `about_text` +
`services_struct` → company description + service summary + category hint. **Tier/credit-
gated** (reuse `claude_classify` gating + Batch-API pattern), run only on cleaned text to
bound tokens, never ungated. Writes to `org_company_ai.ai_data` (org-shared) per the tenancy
doc. This is the roadmap "Web extract — LLM enrichment layer".

---

## 7. Data model changes (summary)

**`company_web_pages`** (+): `discovered_via`, `crawled` (bool), `priority` (int).
**`company_web_extract`** (+): `evidence` JSONB, `identity_category` (str), `identity_probability`
(float), `persons_struct` JSONB, `services_struct` JSONB, `about_text` (text). Keep `confidence`
during transition (read-only once ledger lands), keep `persons`/`service_keywords`.
**New tables** (from tenancy doc): `company_score`, `org_company_ai`.
Retire on `companies`: `web_score`, `flex_score`, `combined_score`, `ai_score`, `website_url`
(`website_url` moves to being derived from the winning identity extract).

## 8. Credit / tier gating (per CLAUDE.md frontend-wiring + billing rule)

- Crawl depth: `crawl_max_pages` scales with tier (free = homepage+impressum only; paid =
  full priority set + on-demand page crawl).
- On-demand "crawl this page" from the profile → `web_search`/crawl credit action.
- LLM enrichment (§6) → existing `immediate_llm`/`batch_llm` gating.
- Web results already flagged in ROADMAP as *not gated* — this rework is where that gate lands.

## 9. Holistic presentation (the "holistic picture")

Rework the company profile **Website** tab into one linked view sourced from the layers:

- **Identity card** — category badge (MATCH_UID ✓ / AMBIGUOUS / …), probability, and the
  evidence ledger as human-readable rows ("UID ✓, address ✓, name only in body"). Replaces
  the opaque score. Runner-up candidates listed with promote/discard (extends existing
  `WebsitePanel` "All URL candidates").
- **Site overview** — the page inventory: what pages exist, which were crawled, per-page
  language + word/media counts; "crawl this page" action for uncrawled ones.
- **Content** — description/about, services (structured), team/persons (with roles + source
  page), contacts (emails/phones/socials), firmographics (address/languages/UID).
- **Provenance** — every field shows which page it came from.

## 10. Unified phased roadmap

Non-breaking, sequenced so the tenancy coupling isn't done twice.

1. **Page inventory + taxonomy** (Layer A.1–A.2) — persist full inventory, extend page-type
   classifier. No behaviour change to scoring. Frontend: site-overview card (read-only).
2. **Structured content extraction** (Layer A.4 + C) — `persons_struct`, `services_struct`,
   `about_text`; wire into NOGA/keywords. Frontend: content sections on profile.
3. **Evidence ledger** (Layer B.1) — emit + persist ledger inside `resolve_company_extract`
   (restructure, features already computed). Keep old confidence live in parallel.
4. **Categorical verdict + auto-pick + UID/related-entity** (B.2–B.3) — rewrite
   `compute_verdict`; demote snippet scoring to ordering; `UNKNOWN` for no-crawl. Frontend:
   identity card. `redecide_identity` job (B.4).
5. **Score tables** (Layer D, from tenancy doc phases 1–4) — `company_score` +
   `org_company_ai`; `rescore_scope`; read cutover. Retire `web_score` from `combined_score`.
6. **Config + gating + LLM layer** (D config, §8, §6) — per-user scoring overrides UI, crawl-
   depth tier gating, optional Haiku enrichment. Cleanup: drop legacy score columns.

Each phase ships independently and is wired to the frontend (job trigger + status + display)
per the CLAUDE.md frontend rule.

## 11. Open decisions (flag before building the relevant phase)

- **Crawl budget default** — raise flat 5→8, or go fully priority-driven with no hard cap for
  paid tiers? (affects cost per crawl run at 700k scale).
- **`AMBIGUOUS` review flag** — auto-pick always, or also leave a soft admin-QA flag even when
  auto-picked? (identity doc §9 left this open).
- **Registry-phone signal source** — Zefix detail vs UID register vs directory data (identity
  doc §9).
- **`persons_struct` GDPR posture** — team-page names are published data, but confirm display/
  export policy given the roadmap's people-finder GDPR caution before surfacing broadly.
- **Sitemap crawl of uncrawled inventory pages** — on-demand only, or a background low-priority
  sweep for paid orgs?
