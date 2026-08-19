# RELATED_ENTITY: stop rejecting a subsidiary's own website

**Status: REJECTED (2026-08-19) — measured, not worth building.** Kept as the record of
why, so it is not re-proposed.

| measurement | result |
|---|---|
| MISMATCH extracts | 12,195 |
| …whose UID resolves to a company we hold | 11,733 (96%) |
| …related via `sogc_corporate_roles` (tier 1) | **99 (0.84%)** |
| …related via shared non-nominee officer (tier 2, ≤5 companies) | **542 (4.5%)** |

Both tiers together explain ~5%. The graph is precise but has almost no recall —
`sogc_corporate_roles` only captures entities appearing in SOGC *changes*, so a stable
group structure that has published nothing never appears.

**What the numbers actually show:** 96% of mismatches carry a UID belonging to a real
company, yet 95% have no relationship to the target. So the mismatch population is
dominated by **wrong websites** — SHAB-notice PDFs, directory pages and aggregators that
list some other company's UID — not by subsidiaries. The value is in candidate quality
(the non-page URL filter, the Content-Type check, the directory-domain blocklist), not in
a relationship graph.

**Adopted instead:** guard `reject_url_candidate` on domain-name evidence — see §9. It
needs no graph and covers subsidiaries, franchises, brand sites and every missing-graph
case at once.

*(Original scope below, unchanged, for the reasoning and the guardrails — several of
which apply to §9 too.)*

## Context

A subsidiary's website legitimately carries its **parent's** UID in the impressum.
Today that is scored as contradiction:

1. `resolve_company_extract` sets `uid_matches_zefix = False`
2. confidence is capped at **0.35** (`crawler_extract.py:1139-1145`)
3. `handle_web_extract` calls `reject_url_candidate(best.url_candidate_id)`
   **unconditionally** on `uid_matches_zefix is False` — permanently blacklisting the
   company's *correct* URL
4. `data["review_flag"] = "uid_mismatch_cross_ref"` and the URL is cross-attributed to
   the parent

So the more of the site we read, the worse the verdict gets. Observed on **TaxWare AG**
(CHE-182.187.419, Urtenen-Schönbühl): its impressum names the holding company, not
TaxWare. It currently scores 44% `deterministic+name_match` only *because* the impressum
was never crawled — the `find_subpage_links` footer bug hid it.

**This is why the two changes must ship together.** Fixing impressum discovery without
RELATED_ENTITY converts a population of weak-but-correct matches into confident-but-wrong
rejections. The `find_subpage_links` fix is already in the tree; this must land before it
reaches production for the subsidiary cohort.

The code already anticipates the case — `crawler_extract.py:1141` reads *"Address and name
can partially recover (e.g. a subsidiary page showing the parent's UID)"* — and
`website_status.py:54-56` documents `RELATED_ENTITY` as *"needs a parent/subsidiary graph
lookup — not yet built"*. This builds that lookup.

## 1. Measure first — this gates the whole thing

Upper-bounds the win before any logic is written. **Do not implement before running these.**

```sql
-- a) Of the MISMATCH extracts, how many carry a UID we can resolve to a company we hold?
SELECT COUNT(*) AS mismatches,
       COUNT(c2.id) AS uid_resolves_to_known_company
  FROM company_web_extract e
  LEFT JOIN companies c2 ON c2.uid = e.uid
 WHERE e.uid_matches_zefix IS FALSE;

-- b) Of those, how many are provably related via the SOGC corporate-role graph?
SELECT COUNT(DISTINCT e.company_id) AS related_pairs
  FROM company_web_extract e
  JOIN companies c2 ON c2.uid = e.uid
  JOIN sogc_corporate_roles r
    ON (r.entity_che = e.uid            AND r.company_id = e.company_id)
    OR (r.entity_che = (SELECT uid FROM companies WHERE id = e.company_id)
                                        AND r.company_id = c2.id)
 WHERE e.uid_matches_zefix IS FALSE;

-- c) Nominee density: how many companies does ONE person sit in? Sizes the cap
--    tier 2 needs. A fat tail here means shared-officer proves nothing on its own.
SELECT COUNT(*) FILTER (WHERE n = 1)            AS in_1_company,
       COUNT(*) FILTER (WHERE n BETWEEN 2 AND 5)  AS in_2_to_5,
       COUNT(*) FILTER (WHERE n BETWEEN 6 AND 20) AS in_6_to_20,
       COUNT(*) FILTER (WHERE n > 20)             AS in_over_20,
       MAX(n)                                     AS max_for_one_person
  FROM (SELECT person_entity_id, COUNT(DISTINCT company_id) AS n
          FROM sogc_person_appearances
         WHERE COALESCE(is_current, TRUE) AND company_id IS NOT NULL
         GROUP BY 1) t;
```

**Decision rule:** if (b) is a meaningful share of (a), build tier 1 only and stop. If (b)
is near zero, the corporate-role graph is too sparse and the design needs rethinking —
do **not** silently fall back to weaker signals, because with `same_address` gone and tier 2
capped there is nothing credible left to fall back to.

## 2. Where the check goes

`resolve_company_extract` is **pure** (no DB) and must stay that way — it is the only
unit-testable part of the identity path. So the graph lookup happens in
`handle_web_extract`, *after* resolve returns, and only when
`data["uid_matches_zefix"] is False and data["uid"]` — i.e. on ~10% of companies, so one
extra query on a minority path rather than a batch join for everyone.

New CRUD in `app/crud/crawler.py`:

```python
def find_entity_relation(db, company_id: int, other_uid: str) -> str | None:
    """'corporate_role' | 'shared_officer' | 'same_address' | 'name_containment' | None"""
```

Checked in that order, returning the **strongest** hit. Bidirectional: the target may be
the parent or the child.

| tier | source | strength | notes |
|---|---|---|---|
| `corporate_role` | `sogc_corporate_roles` (`entity_che` ↔ `company_id`, both indexed, filter `is_current`) | decisive | parent/subsidiary/shareholder, straight from SOGC |
| `shared_officer` | `sogc_person_appearances` joined on `person_entity_id`, `is_current`, both `company_id`s | strong | a shared board member is real but weaker — small firms share nominees |
| ~~`same_address`~~ | — | **DROPPED** | see below |
| `name_containment` | normalised token containment ("TaxWare AG" ⊂ "TaxWare Holding AG") | weak | reuse `_name_tokens`; **never sufficient alone** |

**`same_address` is dropped, not demoted.** `companies` holds only `address_zip` and
`address_city` — there is **no street column**. So the signal cannot distinguish "same
building" from "same village", and many Swiss companies are domiciled at a shared
office/flexoffice or at their Treuhand's address. It is noise, not weak evidence.

**Tier 2 needs a nominee cap.** A Swiss Treuhänder sits on dozens of unrelated boards *and*
domiciles those companies at their own office, so "shared officer" is the normal pattern
for unrelated small companies. A shared officer may only count when that
`person_entity_id` appears in **few** companies (threshold set from §1 Q4); above it the
person is a nominee and proves nothing. Without this cap tier 2 would link every company a
fiduciary administers.

**Rule: a RELATED_ENTITY verdict requires tier 1, or tier 2 under the nominee cap.**
Name containment may only *corroborate*, never establish.

## 3. Scoring change

Add `RELATED_ENTITY = "RELATED_ENTITY"` to `website_status.py` alongside the other
identity categories, and a company-level tier so `compute_verdict` maps it sensibly.

When a relation is confirmed:

- `identity_category = "RELATED_ENTITY"` (instead of `MISMATCH`)
- confidence recomputed on a **new branch** between the `uid_matches is True` and
  `is False` cases — a group-owned site is genuine evidence, just not proof *this* legal
  entity owns the domain. Proposed ceiling **0.70**, i.e. above `MATCH_WEAK` but below
  `MATCH_STRONG`, so it never masquerades as a verified UID match:
  `min(0.70, 0.30 + 0.25*addr_score + 0.20*zone_name_conf + 0.10*base)`
- `method = "deterministic+related_entity"`
- **skip `reject_url_candidate`** — this is the bug that costs a correct URL
- keep the cross-attribution to the parent (a genuinely useful link) but change the
  review flag to `related_entity` so it is not triaged as an error
- record the relation tier in the evidence ledger via `_build_evidence_ledger`, so a human
  reviewing the extract sees *why* it was accepted

Company-level: treat `RELATED_ENTITY` as `CONFIRMED` in `_extract_tier` **only** when tier
1 (`corporate_role`) backed it; `LIKELY` for tier 2. Never `VERIFIED` — that is reserved
for a UID that actually matches.

## 4. Guardrails

- `find_entity_relation` must be **read-only** and must not create the relation it is
  testing for.
- Cache per `(company_id, other_uid)` for the batch — the same parent recurs across a
  group's subsidiaries within one 200-company batch.
- Tier 2 must require `is_current` on both appearances; a director who left in 2014 is not
  a relationship — and must apply the nominee cap from §2.
- Guard against self-match: `other_uid == companies.uid` for the same id is not a relation.
- No new table, no migration. All four signals read existing indexed columns.

## 5. Files

- `app/crud/crawler.py` — new `find_entity_relation`
- `app/services/enrichment/crawler_extract.py` — new confidence branch,
  `RELATED_ENTITY` in the category mapping, evidence-ledger entry. Takes the relation as a
  **parameter**, stays DB-free.
- `app/services/jobs/job_handlers/web_crawl.py` — `handle_web_extract`: lookup on mismatch,
  the `reject_url_candidate` guard, `review_flag` change, `stats["related_entity"]`
- `app/services/enrichment/website_status.py` — constant + `_extract_tier` tiering
- `frontend/src/components/website-panel.tsx`, `dashboard/company-table.tsx`,
  `frontend/messages/{de,en,fr,it}.json` — badge + label (pattern: the `unreachable` badge
  added 2026-08-04)

## 6. Tests

`tests/test_related_entity.py`, following the `db`-fixture + `_candidate`/`_state` style of
`test_identity_resolution.py`:

- corporate-role relation found in **both** directions (target is parent; target is child)
- `is_current = false` role does **not** count
- shared officer via `person_entity_id` counts; a resigned officer does not
- same address **alone** does NOT produce RELATED_ENTITY (the key false-positive test)
- name containment **alone** does NOT either
- a genuine unrelated mismatch still yields `MISMATCH`, still rejects the candidate, still
  caps at 0.35 — the existing behaviour must not regress
- **`reject_url_candidate` is not called** for a related entity (the actual bug)
- confidence lands in (0.35, 0.70] — above weak, never at UID-verified level
- `compute_verdict` maps tier-1 RELATED_ENTITY to `confirmed`, tier-2 to `likely`, never
  `verified`

Plus a regression in `test_crawler_multi_uid.py`: an impressum carrying only the parent's
UID, with a corporate-role row present, yields `RELATED_ENTITY` and a live candidate.

## 7. Rollout

1. Run §1. If (b) is negligible, stop and reconsider.
2. Ship tier 1 only (`corporate_role`) — decisive, cheapest, lowest false-positive risk.
3. Re-extract (free, HTML already in S3) and measure the MISMATCH → RELATED_ENTITY shift
   against the 10,417 baseline.
4. Only then consider tier 2, measuring separately — a shared officer is materially weaker
   evidence and deserves its own before/after.

**Sequencing:** must land before the `find_subpage_links` footer fix reaches the subsidiary
cohort in production, or that fix will convert weak-but-correct matches into confident
rejections.

## 8. Explicitly out of scope

- Discovering the group structure itself — this only *consults* `sogc_corporate_roles`.
- `AMBIGUOUS` (multiple plausible candidates), the other unbuilt category in
  `website_status.py:54-56`. Separate problem.
- Changing what `web_score` does with a RELATED_ENTITY verdict; it follows `confidence`
  as today.


## 9. Adopted alternative — guard the rejection on domain evidence

The harmful behaviour was never the confidence cap; it is `handle_web_extract` calling
`reject_url_candidate` **unconditionally** on `uid_matches_zefix is False`, permanently
blacklisting a company's correct URL.

Guard it on evidence already computed: **do not reject when the domain SLD contains all of
the company's distinctive name tokens.** `_zone_weighted_name_ratio` already treats the
domain zone as near-proof (0.65 weight) precisely because a registered domain is hard to
fake. `taxware.ch` contains "taxware"; that site is not the wrong company, whatever UID its
impressum prints.

- New pure helper in `crawler_extract.py`, e.g.
  `domain_matches_company_name(site_url, company_name) -> bool`, reusing `_name_tokens`
  and `_GENERIC_NAME_TOKENS` so legal forms ("AG", "GmbH") and filler tokens don't count.
- Require **all** distinctive tokens present, not a partial ratio — conservative on
  purpose, so it rescues subsidiaries without also rescuing the SHAB-PDF mismatches it
  should still reject.
- Keep the 0.35 confidence cap and the MISMATCH category: we genuinely could not verify.
  Only the destructive rejection is skipped, and `stats["mismatch_kept"]` counts it.
- Note `zone_name_conf` is **not** persisted on `company_web_extract` (only `confidence`,
  `identity_category`, `identity_probability`, `evidence`), so the handler recomputes the
  domain check rather than reading a stored score.

Tests must include a genuine wrong-website case — a SHAB-notice PDF on `sshv.ch` extracted
for an unrelated company — asserting it is **still** rejected.
