# Multilingual Analysis Architecture — Strategy

> Decision record / strategy doc. No implementation steps — this exists so future features
> inherit the right cross-lingual pattern instead of re-inventing language handling.

## Context

Swiss companies file their Zefix purpose in DE/FR/IT (occasionally EN/RM). Today the
platform's analysis quality is **fragmented by language**: a user working in one language
effectively only gets first-class analysis on companies in that language. This already
hurts keyword search, clustering, and cluster/keyword labels, and will extend to every new
text source (SOGC history, simap tenders, website extraction) and every "complex query"
feature to come (LLM Q&A, cross-org filters, aggregate analytics).

## Key finding: there are two language problems, not one

The codebase already solves half the problem and leaves the other half broken. Conflating
them leads to the wrong fix (e.g. "translate everything on the fly").

| Operation class | Cross-lingual today? | Mechanism | Verdict |
|---|---|---|---|
| **Semantic** — vector search, NOGA classification | ✅ Works | `paraphrase-multilingual-mpnet-base-v2` shared embedding space ([app/services/embeddings.py](../app/services/embeddings.py)); per-language NOGA embeddings with DE fallback ([app/services/noga.py](../app/services/noga.py)) | Keep. Don't translate for this. |
| **LLM scoring** — Claude Haiku ([app/services/claude_classify.py](../app/services/claude_classify.py)) | ⚠️ Mostly works | Claude reads DE/FR/IT natively; English system prompt is fine; `ai_category` already English (canonical) | Works, but benefits from a canonical input for consistency. |
| **Lexical / structured** — `ILIKE` keyword search ([app/crud/company.py](../app/crud/company.py)), TF-IDF + clustering ([app/services/cluster_pipeline.py](../app/services/cluster_pipeline.py)), keyword/cluster labels | ❌ Broken | Substring match ("Bäckerei" ≠ "boulangerie"); spaCy **`de_core_news_md` German-only lemmatizer**; German-biased labels | **This is the real fragmentation.** |

The observation "a German speaker can only analyze German companies" is concretely the
**lexical/structured layer**: exact-match search, filters, and the TF-IDF/cluster labels
that are German-biased and skip FR/IT companies.

## Decision: hybrid, picking the right tool per operation class

Two complementary cross-lingual mechanisms, chosen by operation type — **not** a single
global translation pass.

### 1. Semantic operations → shared multilingual embedding space (already in place)
Keep the multilingual embedding model as the backbone for vector search and NOGA. These
are meaning-based and fuzzy; translating corpus text for them adds cost and a quality-loss
hop while a French query already lands near German content. **No change in principle.**

### 2. Lexical & structured operations → preprocessed **canonical (English) text**, stored alongside native
Translate each company's text **once at ingestion** into a canonical English field, keep
the **native text too** (dual model). Run keyword extraction, clustering, full-text
(`tsvector`), and LLM input on the **canonical** copy; keep native for display and
native-language search.

Why preprocessed (not on-the-fly) for the corpus:
- The heavy operations (clustering, keyword extraction, LLM scoring, future full-text
  index) are **batch jobs over all ~700k rows**. They need durable, queryable, indexable
  canonical text — you cannot build a `tsvector` index or a TF-IDF corpus on text that
  only exists transiently at query time.
- Translate-once amortizes across every re-run, every search, every analysis. On-the-fly
  re-translates the same 700k purposes repeatedly — strictly worse at this scale and
  against the project's batching mandate.
- Source text is near-static (purpose changes only on SOGC mutation), so staleness is a
  non-issue with a "translate on change" hook.

Why **dual** (native + EN):
- **Native** preserves exact-match search in the user's own language and faithful display.
- **English canonical** unlocks one language-correct pipeline for lexical/cluster/LLM ops
  and removes the German-only lemmatizer bias (switch clustering to spaCy `en_core_web_*`
  over the canonical field).
- English pivot: richest NLP/full-text tooling, neutral across DE/FR/IT, aligns with the
  already-English `ai_category`.

### 3. User query → on-the-fly translation (the one place runtime translation belongs)
Translate the **single query string** to English at request time (tiny, cacheable), then
run lexical/full-text search against the canonical field. For semantic search, embed the
query directly (multilingual model already crosses languages — no translation needed).

### 4. Translation engine → local NMT on the ML worker
A local offline model (MarianMT/Opus-MT dedicated `de/fr/it→en`, or NLLB-200-distilled as
a single model), lazy-loaded with the **same pattern as [app/services/embeddings.py](../app/services/embeddings.py)**.
Consistent with the project's offline-first stance (offline geocoding, local embeddings),
no per-row API cost, no quota. Runs in the existing batch-job framework with the standard
chunked pattern. Source language is already known from `Company.purpose_language`
([app/models/company.py](../app/models/company.py)).

**Approx timing** (short purpose texts, Opus-MT class):
- Per company: CPU unbatched ~50–200 ms; CPU batched ~5–20 ms; GPU batched ~1–3 ms.
- Full 700k backfill (one-time): CPU single worker ~40 min–2 hrs; a few parallel pods
  ~12–25 min; GPU ~3–6 min. Comparable to a full re-embedding/re-clustering pass.
- Ongoing (new/changed rows) and query-time: negligible (ms, model warm).
- NLLB-200-distilled is ~2–4× slower on CPU than per-pair Opus-MT but simpler to operate.
- Longer sources (SOGC/website paragraphs) scale ~linearly with token count.

## Decision surface / UX — how users navigate this

Principle: **make it invisible.** The canonical layer exists so users don't choose a
language per query. Split "the decision" into three layers that live in different places:

1. **Policy defaults — set once, baked in, never shown.**
   - Canonical pivot = English.
   - Lexical search default = **cross-lingual**: query translated to English and matched
     against the canonical field ("Bäckerei" also finds "boulangerie"). This is the whole
     point of the project, so it's the default, not an option.
   - Semantic search = always cross-lingual (embeddings).

2. **User setting — one profile-level choice.**
   - **Display/interface language** (DE/FR/IT/EN): standard i18n; drives the language of
     cluster labels, `ai_category`, and AI answers. Set once, not per query.

3. **Optional per-search override — off by default, for the minority who need exactness.**
   - A single "match exact native text only" toggle for power users wanting exact native
     legal terms without translation fuzz. Everyone else never touches it.

**How to pick the defaults instead of asking users:** optimize for the dominant workflow —
"all relevant Swiss companies regardless of filing language, shown in my language." That
dictates cross-lingual ON, present in UI language, native-only as opt-in. You're choosing
the right default for ~95% and leaving a small escape hatch, not deciding per user.

**Trust rule:** display **native** text faithfully (never show machine-translated purpose
as if original), but match/cluster/score against the **canonical** copy under the hood.
When translated text is shown (e.g. a translated SOGC snippet), mark it machine-translated.
The dual model is what keeps display honest while making search smart.

## How this extends to future surfaces

- **SOGC history / simap / website extraction**: same dual treatment at ingestion — store
  native, translate to the canonical English field, then all downstream lexical/cluster/
  LLM/full-text analysis reuses the one canonical pipeline. New sources plug in without new
  language logic.
- **Complex queries (LLM Q&A, cross-org analytics, aggregate filters)**: operate over the
  canonical field so one query works across all companies regardless of original language;
  translate the user's query string on the fly; use embeddings for semantic parts.

## Explicitly rejected

- **On-the-fly translation of the corpus** — breaks batch jobs and full-text indexing,
  re-pays translation cost on every access, violates the 700k batching mandate.
- **Translate-only, drop native** — loses faithful display and exact native-language search.
- **Paid translation API** — unnecessary external dependency/quota/cost given a local NMT
  model fits existing infra and source language is already detected.

## Open items to resolve before implementation

- Canonical field granularity: one `*_en` per text source (purpose, sogc, web) vs a single
  merged canonical document per company.
- NMT model choice (Opus-MT per-pair vs single NLLB-200); validate FR/IT→EN quality on
  Swiss legal-purpose text (domain terms matter).
- Whether clustering/keywords switch fully to the canonical field or run dual.
- Re-translation trigger wiring on SOGC purpose mutation.
