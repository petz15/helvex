# Deep Dive — Company List Filtering at 700k Rows (`app/crud/company.py`)

`_apply_filters()` (lines 82–318, 236 lines) is the single shared query
builder behind `list_companies`, `count_companies`, CSV export
(`run_csv_export`), and the saved-view alert sweep — 180 outbound graph
connections, the busiest function in the CRUD layer, because it's one long
sequential `if`-chain touching most `Company` columns. It takes ~40 keyword
filter parameters and returns a mutated SQLAlchemy `Query`.

## Index coverage — already audited recently, mostly good

Before writing this doc, the natural worry was "does every `ILIKE` filter
here have a matching index at 700k rows" (per `CLAUDE.md`'s scale rule).
Checked against `alembic/versions/` directly rather than assuming:

**Good news — this was already fixed.** Migration `0101_fix_and_add_performance_indexes`
(2026-06-16, the most recent index migration) is explicitly a follow-up audit
that found and fixed several stale/missing indexes from earlier column
renames, and added GIN trigram indexes for `old_names`, `noga_label`, and
fixed `ai_category`'s trigram index (which had silently gone stale after a
column rename in migration `0032` — the index still existed but under the
old `claude_category` name and stopped being usable). Current coverage for
the `ILIKE '%term%'` filters in `_apply_filters`:

| Column | ILIKE'd in `_apply_filters` | Index |
|---|---|---|
| `name`, `uid` | name/UID search | `ix_companies_name_trgm` (0090) |
| `old_names` | name search OR-branch | `ix_companies_old_names_trgm` (0101) |
| `tags` | positive + `exclude_tags` | `ix_companies_tags_trgm` (0025) |
| `ai_category` | positive + `exclude_ai_category` | `ix_companies_ai_category_trgm` (0101 fix) |
| `noga_label` | positive + `exclude_noga_label` | `ix_companies_noga_label_trgm` (0101) |

## The gap that's still real: `exclude_*` filters can't use these indexes

All the trigram indexes above only accelerate **positive** `ILIKE '%x%'`
matches. Every `exclude_*` filter in `_apply_filters` uses `.notilike(...)`
(`exclude_tags`, `exclude_ai_category`, `exclude_noga_label`, plus
`exclude_review_status`/`exclude_canton`/`exclude_contact_status` which use
plain not-equal on low-cardinality columns, which is fine). **A GIN trigram
index cannot serve a negated `LIKE`** — Postgres has no way to use the index
to return "everything that doesn't match a pattern" cheaply, so any query
using `exclude_tags`, `exclude_ai_category`, or `exclude_noga_label` falls
back to evaluating that condition as a row-by-row filter after whatever
other conditions narrow the set. On a 700k-row table, if an exclude filter
is the *only* or *most selective* filter in a given request, that's a
sequential-scan-shaped cost. This isn't hypothetical — the UI exposes all
three exclude filters directly in the filter bar per `ARCHITECTURE.md`.
**Worth confirming during review** whether these are common enough in
practice to matter, and if so, whether a materialized "denormalized
negative tag" column or a different query shape would help — trigram
indexes structurally can't.

## The junction-table dual-write path is still live in this function

`_apply_filters` branches on `HAS_JUNCTION_TABLES` (set at import time — a
`try/except ImportError` around importing `CompanyTfidfCluster`/
`CompanyPurposeKeyword`) for both `tfidf_cluster` and `purpose_keywords`
filters:

- If junction tables are importable: `query.join(CompanyTfidfCluster).filter(CompanyTfidfCluster.cluster.in_(terms))`
- If not: falls back to `.ilike()` pattern-matching against a delimited
  string column (`tfidf_cluster`, `purpose_keywords`) with hand-rolled
  boundary matching (`t`, `t|%`, `%|t`, `%|t|%` — checking a pipe-delimited
  value is present as a whole token, not a substring).

`ARCHITECTURE.md` already flags this as a known deferred item (§4.6,
"Junction table dual-write... deferred; requires full audit before removing
sync logic"). Practical implication for review: **if you're reviewing a
change to cluster or keyword filtering, you have to verify it in both
branches** — it's easy to fix a bug in the junction-table path and not
notice the string-matching fallback has the same bug, or vice versa. There's
no test fixture toggling `HAS_JUNCTION_TABLES` to force coverage of both
paths (per the knowledge-gaps scan, this whole function has no detected
test coverage).

## Structural note on `_apply_filters` itself

40 keyword parameters, one sequential `if`/`elif` per filter, no dict-driven
or builder-pattern abstraction. This is not necessarily wrong for a filter
builder (a data-driven table of "column → operator → param" would arguably
be harder to follow given how many filters have bespoke logic — UID
normalization, NOGA ancestor/descendant path matching via `noga_path`,
SHAB-type derived conditions), but it means **there's no structural
enforcement that a new filter gets an index**. The 0101 migration was a
manual audit that caught up on several filters added over time without
matching indexes. Whoever adds the next `ILIKE`-based filter to this
function should add the matching trigram index in the same PR — nothing
here will catch it if they don't, and the next audit might not happen for
months.
