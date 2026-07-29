"""Semantic (embedding-similarity) boilerplate stripping for DE/FR purpose text.

Company purpose texts mix a substantive first part with a generic "ancillary
powers" tail (branch offices, real estate, financing/guarantees). The regex-based
_strip_purpose_boilerplate() (app/services/ml/noga.py) only catches tails that
recur as an exact/near-exact sentence — it misses paraphrased boilerplate and,
if used as a bare structural rule ("truncate everything after kann/peut"), is
too aggressive and destroys real content for companies whose ancillary-powers
clause is worded slightly differently (utilities, foundations, niche trades).

This module embeds the trigger sentence ("kann"/"peut") plus the next couple of
sentences against a handful of known-generic exemplar sentences (same
multilingual model used for NOGA), and cuts at the first sentence that scores
above SIMILARITY_THRESHOLD. Validated via scripts/validate_boilerplate_similarity.py
against the full DE corpus and targeted checks on known false positives.

Only DE and FR are covered (SEMANTIC_LANGS) — other languages fall back to the
existing regex method, unchanged.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.models.company import Company

SEMANTIC_LANGS = {"de", "fr"}

# Cosine similarity cutoff for "boilerplate-like". Both compared vectors are
# L2-normalized, so this is a plain dot product. Validated on a 3000-company DE
# sample (p10=0.810, p50=0.932 among rows that DID cross this threshold) and
# confirmed against 6 known false positives (all scored well below threshold and
# were correctly left untouched).
SIMILARITY_THRESHOLD = 0.72

# Trigger sentence + next couple, per the observed pattern of "immediately or
# within the next couple of sentences".
WINDOW_SIZE = 3

TRIGGER_PATTERNS: dict[str, re.Pattern] = {
    "de": re.compile(r"\bkann\b", re.IGNORECASE),
    "fr": re.compile(r"\b(?:peut|peuvent)\b", re.IGNORECASE),
}

# Highest-confidence generic ancillary-powers sentences, validated manually
# during the reverse-sweep analysis. Paraphrases of these (different wording,
# same content) score high similarity too -- that's the whole point of using
# embeddings instead of exact-string frequency.
EXEMPLARS: dict[str, list[tuple[str, str]]] = {
    "de": [
        ("branch/subsidiary",
         "Die Gesellschaft kann Zweigniederlassungen und Tochtergesellschaften im In- und Ausland "
         "errichten und sich an anderen Unternehmen im In- und Ausland beteiligen sowie alle "
         "Geschäfte tätigen, die direkt oder indirekt mit ihrem Zweck in Zusammenhang stehen."),
        ("real estate",
         "Die Gesellschaft kann im In- und Ausland Grundeigentum erwerben, belasten, veräussern "
         "und verwalten."),
        ("financing/guarantees",
         "Sie kann auch Finanzierungen für eigene oder fremde Rechnung vornehmen sowie Garantien "
         "und Bürgschaften für Tochtergesellschaften und Dritte eingehen."),
    ],
    "fr": [
        ("branch/subsidiary",
         "La société peut créer des succursales en Suisse et à l'étranger, participer à d'autres "
         "entreprises en Suisse et à l'étranger, acquérir des entreprises visant un but identique "
         "ou analogue, ou fusionner avec de telles entreprises, faire toutes opérations et conclure "
         "tous contrats propres à développer et à étendre son but ou s'y rapportant directement ou "
         "indirectement."),
        ("financing/guarantees",
         "La société peut accorder des prêts ou des garanties à des associés ou des tiers, si cela "
         "favorise ses intérêts."),
    ],
}

# Batch size for the strip_purpose_semantic backfill job. Keyset-paginated
# (never OFFSET) to stay safe on the 700k-row companies table -- a single
# unbatched query blows past Postgres's statement_timeout.
BATCH_SIZE = 500

_exemplar_vecs_cache: dict[str, Any] = {}


def _get_exemplar_vecs(lang: str):
    """Lazily embed and cache the exemplar sentences for one language."""
    if lang not in _exemplar_vecs_cache:
        from app.services.ml.embeddings import embed_texts
        texts = [t for _, t in EXEMPLARS[lang]]
        _exemplar_vecs_cache[lang] = embed_texts(texts)
    return _exemplar_vecs_cache[lang]


def find_trigger_window(sentences: list[str], trigger_re: re.Pattern) -> tuple[int, list[str]] | None:
    """Return (trigger_idx, window_sentences) for the first trigger match outside
    sentence 1 (0-based index >= 1), or None if the trigger never appears there."""
    for i, s in enumerate(sentences):
        if i == 0:
            continue
        if trigger_re.search(s):
            return i, sentences[i: i + WINDOW_SIZE]
    return None


def compute_purpose_clean_batch(
    companies: list["Company"],
    boilerplate_patterns,
) -> dict[int, str]:
    """Return {company_id: cleaned_purpose_text} for a batch of companies.

    DE/FR: semantic trigger-window similarity check, batched into a single
    embed_texts() call across the WHOLE input batch for efficiency (mirrors
    scripts/validate_boilerplate_similarity.py). Companies in other languages,
    or DE/FR companies with no trigger match outside sentence 1, fall back to
    the existing regex-based _strip_purpose_boilerplate — no model call needed.
    Companies with no purpose text are skipped (absent from the returned dict).
    """
    from app.services.ml.boilerplate_analysis import _split_sentences
    from app.services.ml.embeddings import embed_texts
    from app.services.ml.noga import _strip_purpose_boilerplate

    results: dict[int, str] = {}
    # (company, sentences, trigger_idx, window)
    semantic_candidates: list[tuple[Any, list[str], int, list[str]]] = []
    regex_fallback: list[Any] = []

    for company in companies:
        raw = (company.purpose or "").strip()
        if not raw:
            continue
        lang = company.purpose_language
        if lang in SEMANTIC_LANGS:
            sentences = _split_sentences(raw)
            found = find_trigger_window(sentences, TRIGGER_PATTERNS[lang])
            if found:
                trigger_idx, window = found
                semantic_candidates.append((company, sentences, trigger_idx, window))
                continue
        regex_fallback.append(company)

    if semantic_candidates:
        flat_sentences: list[str] = []
        window_sizes: list[int] = []
        for _, _, _, window in semantic_candidates:
            flat_sentences.extend(window)
            window_sizes.append(len(window))

        sent_vecs = embed_texts(flat_sentences)

        ptr = 0
        for (company, sentences, trigger_idx, window), n in zip(semantic_candidates, window_sizes):
            exemplar_vecs = _get_exemplar_vecs(company.purpose_language)
            window_vecs = sent_vecs[ptr: ptr + n]
            ptr += n

            sims = window_vecs @ exemplar_vecs.T  # both L2-normalized -> cosine similarity
            max_sims = sims.max(axis=1)

            cutoff_offset = next(
                (i for i, s in enumerate(max_sims) if s >= SIMILARITY_THRESHOLD), None
            )
            if cutoff_offset is not None:
                cutoff_idx = trigger_idx + cutoff_offset
                kept = " ".join(sentences[:cutoff_idx]).strip()
                results[company.id] = kept or company.purpose
            else:
                # No sentence in the window scored as boilerplate -- nothing to cut.
                results[company.id] = company.purpose

    for company in regex_fallback:
        raw = (company.purpose or "").strip()
        stripped = _strip_purpose_boilerplate(raw, boilerplate_patterns)
        results[company.id] = stripped or raw

    return results


def get_purpose_clean(company: "Company", boilerplate_patterns) -> str:
    """Single-company convenience wrapper — prefers the precomputed column.

    Returns company.purpose_clean directly if already populated (the common
    case once strip_purpose_semantic has run). Otherwise computes it live via
    compute_purpose_clean_batch([company], ...) — used as a fallback for
    companies not yet processed by the backfill job.
    """
    if company.purpose_clean:
        return company.purpose_clean
    if not (company.purpose or "").strip():
        return ""
    return compute_purpose_clean_batch([company], boilerplate_patterns).get(
        company.id, company.purpose or ""
    )


def strip_purpose_semantic_batch(
    db: "Session",
    *,
    batch_size: int = BATCH_SIZE,
    resume_from: int = 0,
    only_missing: bool = True,
    progress_cb: Any = None,
) -> dict[str, Any]:
    """Backfill job: precompute and store purpose_clean for all companies.

    Keyset-paginated (Company.id > last_id) to stay safe on a 700k-row table —
    a single unbatched query blows past Postgres's statement_timeout.
    """
    from app import crud
    from app.models.company import Company

    stats: dict[str, Any] = {"selected": 0, "updated": 0, "skipped_no_purpose": 0, "errors": []}
    boilerplate_patterns = crud.get_active_boilerplate_patterns(db)

    query = db.query(Company).filter(Company.purpose.isnot(None))
    if only_missing:
        query = query.filter(Company.purpose_clean.is_(None))
    stats["selected"] = query.with_entities(Company.id).count()

    last_id = resume_from
    processed = 0

    while True:
        batch = (
            query.filter(Company.id > last_id)
            .order_by(Company.id.asc())
            .limit(batch_size)
            .all()
        )
        if not batch:
            break

        try:
            cleaned = compute_purpose_clean_batch(batch, boilerplate_patterns)
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc)
            for company in batch:
                text = cleaned.get(company.id)
                if text is None:
                    stats["skipped_no_purpose"] += 1
                    continue
                company.purpose_clean = text
                company.purpose_clean_computed_at = now
                stats["updated"] += 1
            db.commit()
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            stats["errors"].append(str(exc))

        last_id = batch[-1].id
        processed += len(batch)
        if progress_cb:
            progress_cb(processed, stats["selected"], stats)

    return stats
