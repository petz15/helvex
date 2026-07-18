# Web Identity & Scoring Rework — Design Reference

> Status: **approved design, not yet implemented** (2026-07-17).
> Companion to [`scoring-multitenancy-rework.md`](scoring-multitenancy-rework.md).
> Goal: stop the website pipeline from emitting "false signals". Split the single
> overloaded `web_score`/`confidence` scalar into **identity** (is this the company's
> site?) and **content** (what's on it) — and move **fit** (does it matter to this org?)
> out to the per-org scoring layer entirely.

---

## 1. Root cause

Today `web_score` / the additive `confidence` in `resolve_company_extract`
(`crawler_extract.py:842-882`) answers three orthogonal questions with one number:

1. **Identity** — is this URL really theirs?
2. **Content** — how good/rich is the site?
3. **Fit** — how relevant is this company to *me* (fed into `combined_score`)?

Collapsing them means a mediocre-everything 0.62 is indistinguishable from a
great-address-no-UID 0.62. False-signal sources specifically:

- **Pre-crawl snippet scoring leaks into the verdict.** `score_result` (`scoring.py:449`)
  scores Serper snippets by name/location/keyword overlap; `classify_search_results`
  (`website_status.py:85-131`) turns that into a verdict + `web_score` fallback. A snippet
  match is weak evidence but currently drives outcomes.
- **Silent max-pick hides ambiguity.** `compute_verdict` (`website_status.py:200-225`) takes
  the highest-confidence candidate per tier — two plausible domains get laundered into
  "likely" with no signal that the pick was a coin-flip.
- **UID compare is crude.** Binary exact-string match; no checksum validation, no
  related-entity awareness (a parent/subsidiary UID on the page is treated as "wrong site"),
  absence handled but conflated in the additive blend.

## 2. Principle — split identity / content / fit

| Question | Output type | Scope | Home |
|---|---|---|---|
| **Identity** | probability + category + evidence ledger | global (truth) | this pipeline |
| **Content** | structured facts | global (extract once) | `company_web_extract` / `company_web_page` |
| **Fit** | score | per-org | scoring rework layer — **not here** |

Web scoring's job shrinks to **decide identity + emit rich facts**. "How good is this site
for me" leaves the module and becomes a per-org fit signal (see the scoring rework).

## 3. Identity decision

### 3.1 Evidence ledger (not an additive formula)

Each candidate accumulates typed signals, persisted — not pre-summed:

```
(dimension, direction, strength, value)
  uid_checksum_valid  + strong     CHE-123.456.789
  uid_matches_zefix   + decisive
  domain_is_name      + strong     muster.ch == "Muster AG"
  address_zip_city    + medium
  phone_matches_reg   + medium     (new — cross-check registry phone)
  purpose_sim         + weak       0.71   (already computed)
  name_in_title       + weak
  -is_marketplace / -is_parked / -uid_of_other_entity   (negatives)
```

The hand-tuned weights in `resolve_company_extract` become the ledger's **features**.

### 3.2 Categorical outcomes (surface ambiguity, don't hide it)

Replace the confidence-threshold ladder with categories keyed on *which evidence fired*:

- `MATCH_UID` — UID verified, near-certain
- `MATCH_STRONG` — domain + name + address agree, no UID
- `MATCH_WEAK` — thin agreement / sparse site
- `AMBIGUOUS` — ≥2 candidates with comparable strong evidence
- `RELATED_ENTITY` — UID/identity points at a parent/subsidiary, not a wrong site
- `MISMATCH` — evidence points at a different company
- `NO_SITE` — social / directory / none
- `UNKNOWN` — crawl yielded no usable evidence (**not** a snippet-derived "likely")

### 3.3 AMBIGUOUS → auto-pick (decision locked)

When ≥2 candidates tie on strong evidence, **auto-pick** using independent tiebreak
evidence rather than queueing for review:
1. Prefer the domain the company's own verified socials link to.
2. Then UID-bearing candidate over non-UID.
3. Then registry-phone / address agreement.
4. Then domain-is-exact-name over partial.
Record the tiebreak reason in the ledger; keep the runner-up so a later re-decide can flip it.

### 3.4 Pre-crawl score = crawl ordering ONLY

`score_result`'s snippet score may order which candidate to crawl first. It must **not**
contribute to the identity probability or any persisted score. If the crawl yields no
evidence → `UNKNOWN`, never a snippet-derived verdict. `classify_search_results` is
demoted to a crawl-queue prioritizer.

### 3.5 UID beyond string compare

- **Validate the CHE checksum** before comparing — rejects OCR/typo garbage.
- **Absence ≠ mismatch** — only a *present, mismatching* UID is negative (keep, make explicit).
- **Related-entity awareness** — on mismatch, look the found UID up in the SOGC/relationship
  graph; a parent/subsidiary UID → `RELATED_ENTITY`, not the flat 0.35 penalty
  (`crawler_extract.py:855-862`).
- **Corroborate with other IDs** — registered phone, address, later VAT/WHOIS org. Several
  independent weak IDs beat one binary UID.

## 4. Staging — ledger-first, model later (recommended)

1. **Ledger + explicit weighted combine + categories** — deterministic, auditable, ships
   without labels. Directly attacks the "false signals I can't reason about" complaint.
   Encodes the interaction rules (related-entity, auto-pick tiebreak) explicitly.
2. **Trained classifier (logistic / GBM)** — drop-in replacement for the *combine* step once
   labels accumulate. The ledger is its feature vector; categorical outcomes are passive
   labels (`MATCH_UID` ≈ positive, UID-`MISMATCH` ≈ negative). Folds in the ROADMAP item
   "Web extract — URL confidence: trained logistic regression / GBM model". Serialize to
   `app/services/url_confidence_model.joblib`; fall back to the deterministic ledger if absent.

Rationale: the model only adds value in the weak/ambiguous band, and building the ledger is
a prerequisite for it — so ledger-first is not a detour. Starting with a black box while the
problem is unexplainable false signals is the wrong order.

## 5. `web_score` retirement (decision locked)

`web_score` is **retired as a relevance input** — the identity probability answers "is this
real", and site *quality* becomes a per-org fit signal in the scoring rework. Drop `web_score`
from `combined_score` (dovetails with the scoring rework, which already re-homes scores).
Keep the identity probability + category on the extract for display/explainability. Revisit
only if a concrete non-relevance use appears.

## 6. Pipeline

```
search → candidates → crawl (ordered by cheap snippet score, zero scoring weight)
  → extract facts (global; already rich: uid/address/phone/socials/kw/images/video)
  → build evidence ledger per candidate
  → deterministic combine (→ GBM later) → (probability, category, ledger)
  → AMBIGUOUS auto-picked via independent tiebreak; persist ledger + category
        ↓ facts (global)              ↓ identity (global)
   org scoring layer computes "fit" per org   ← scoring rework, not here
```

## 7. Persistence & re-decide

Persist the ledger + probability + category per candidate (extend `company_web_extract`).
Buys: UI explainability ("matched: UID ✓, address ✓; name only in body"), GBM training
labels, and a `re-decide` job that re-runs the combine step on stored evidence — no re-crawl
(same pattern as the existing `reextract` loop).

## 8. Migration phases

1. **Ledger extraction** — emit the typed evidence ledger inside `resolve_company_extract`
   (features already computed; this is a restructure, not new crawling). Persist it.
2. **Categorical verdict** — replace the confidence ladder in `compute_verdict` /
   `_extract_tier` with the category set; implement AMBIGUOUS auto-pick + RELATED_ENTITY.
3. **Cut pre-crawl scoring** — demote `classify_search_results` to crawl ordering; crawl-less
   companies become `UNKNOWN`.
4. **UID hardening** — checksum validation + related-entity lookup + registry-phone signal.
5. **Retire `web_score`** — drop from `combined_score` (coordinated with scoring rework).
6. **GBM** — train on accumulated labels; swap the combine step behind the joblib fallback.

## 9. Open items

- Registry-phone signal source (Zefix detail carries phone? else UID register / directory data).
- `RELATED_ENTITY` graph source — reuse SOGC person/entity + takeover edges already built.
- Whether `AMBIGUOUS` should still leave a soft review flag for admin QA even when auto-picked.
