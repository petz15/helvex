"""Automated boilerplate and stopword discovery pipeline.

Four phases, designed to run after every TF-IDF clustering job:

  Phase 1 — IDF-based term frequency analysis (free)
    Load the fitted TF-IDF vectorizer from S3. Terms with IDF below a threshold
    appear in >40% of documents and are boilerplate candidates. Written to
    tfidf_stopwords table as enabled=False (suggested, not yet active).

  Phase 2 — Sentence-level hash deduplication (free, multilingual)
    Split purpose texts by sentence boundary. Hash each normalised sentence and
    count occurrences. Sentences appearing in >300 companies are statutory
    boilerplate across any language (frequency is language-agnostic). Written to
    the boilerplate_patterns table as enabled=False. Because _strip_purpose_boilerplate()
    already reads from crud.get_active_boilerplate_patterns(db), approved patterns
    take effect automatically on the next classification run.

  Phase 3 — Cross-cluster stopword auto-staging (free)
    Re-routes analyze_cross_cluster_terms() output: terms appearing in labels of
    >60% of clusters are strong boilerplate candidates, written to tfidf_stopwords.

  Phase 4 — Optional Claude Haiku review (one call, credit-consuming)
    Collect up to 50 newly staged stopwords + 20 boilerplate phrases.
    Single Claude Haiku call. Claude detects the language of each term and
    classifies as always_boilerplate | sometimes_meaningful | keep_as_signal.
    Response grouped by language: {"de": [...], "fr": [...], "it": [...]}.
    always_boilerplate → auto-approved (enabled=True).
    Gated behind use_ai=False parameter.
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
from typing import Any

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_SENTENCE_SPLIT = re.compile(r"(?<=\.)\s+(?=[A-ZÄÖÜÀÂÉÈÊËÏÎÔÙÛÇ])")
_BOILERPLATE_MIN_MATCH_COUNT = 300
_IDF_BOILERPLATE_THRESHOLD = 0.92  # log(1/0.40) ≈ 0.92; terms in >40% of docs
_CROSS_CLUSTER_LABEL_FREQ_THRESHOLD = 0.60
_REVIEW_MAX_STOPWORDS = 50
_REVIEW_MAX_BOILERPLATE = 20


def _normalise_sentence(s: str) -> str:
    return re.sub(r"\s+", " ", s.lower().strip())


def discover_stopwords(
    db: Session,
    *,
    use_ai: bool = False,
    anthropic_api_key: str | None = None,
    progress_cb=None,
) -> dict[str, Any]:
    """Run the full 4-phase discovery pipeline.

    Returns a summary dict with counts per phase.
    """
    stats: dict[str, Any] = {
        "phase1_stopwords_staged": 0,
        "phase2_boilerplate_staged": 0,
        "phase3_stopwords_staged": 0,
        "phase4_auto_approved": 0,
        "errors": [],
    }

    if progress_cb:
        progress_cb(0, 4, stats)

    try:
        stats["phase1_stopwords_staged"] = _phase1_idf_analysis(db)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Phase 1 (IDF analysis) failed: %s", exc)
        stats["errors"].append(f"phase1:{exc}")

    if progress_cb:
        progress_cb(1, 4, stats)

    try:
        stats["phase2_boilerplate_staged"] = _phase2_sentence_dedup(db)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Phase 2 (sentence dedup) failed: %s", exc)
        stats["errors"].append(f"phase2:{exc}")

    if progress_cb:
        progress_cb(2, 4, stats)

    try:
        stats["phase3_stopwords_staged"] = _phase3_cross_cluster(db)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Phase 3 (cross-cluster) failed: %s", exc)
        stats["errors"].append(f"phase3:{exc}")

    if progress_cb:
        progress_cb(3, 4, stats)

    if use_ai:
        try:
            stats["phase4_auto_approved"] = _phase4_claude_review(db, api_key=anthropic_api_key)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Phase 4 (Claude review) failed: %s", exc)
            stats["errors"].append(f"phase4:{exc}")

    if progress_cb:
        progress_cb(4, 4, stats)

    return stats


# ── Phase 1 ───────────────────────────────────────────────────────────────────

def _phase1_idf_analysis(db: Session) -> int:
    """Load S3 vectorizer, find low-IDF terms, stage them as stopword candidates."""
    from app.services import s3_client

    if not s3_client.is_models_bucket_configured():
        logger.info("Phase 1: S3 not configured, skipping")
        return 0

    try:
        import io
        import json
        import numpy as np
        from sklearn.feature_extraction.text import TfidfVectorizer

        vocab_bytes = s3_client.download_model_bytes("models/tfidf_vocabulary.json")
        idf_bytes = s3_client.download_model_bytes("models/tfidf_idf.npy")
        vocab: dict[str, int] = json.loads(vocab_bytes.decode("utf-8"))
        idf_array = np.load(io.BytesIO(idf_bytes), allow_pickle=False)

        vectorizer = TfidfVectorizer()
        vectorizer.vocabulary_ = vocab
        vectorizer.idf_ = idf_array
    except Exception as exc:
        logger.info("Phase 1: could not load vectorizer from S3 (%s), skipping", exc)
        return 0

    feature_names = vectorizer.get_feature_names_out()
    idf_values = vectorizer.idf_

    candidates = []
    for term, idf in zip(feature_names, idf_values):
        if len(term) < 3 or term.isdigit():
            continue
        if idf < _IDF_BOILERPLATE_THRESHOLD:
            # Approximate doc frequency from IDF: df ≈ n / exp(idf - 1) - 1
            candidates.append(term)

    if not candidates:
        return 0

    from app import crud
    written = 0
    for term in candidates:
        try:
            crud.upsert_tfidf_stopword(db, term=term, enabled=False, source="idf_analysis")
            written += 1
        except Exception:
            logger.warning("Phase 1: failed to upsert stopword %r", term, exc_info=True)
    db.commit()
    logger.info("Phase 1: staged %d low-IDF stopword candidates", written)
    return written


# ── Phase 2 ───────────────────────────────────────────────────────────────────

def _phase2_sentence_dedup(db: Session) -> int:
    """Hash-dedup purpose sentences across all companies; stage frequent ones as boilerplate."""
    from collections import Counter
    from app.models.company import Company

    # Stream purpose texts in chunks to avoid loading all into memory
    sentence_counts: Counter = Counter()
    sentence_text: dict[str, str] = {}  # hash → original sentence

    offset = 0
    chunk = 5000
    while True:
        rows = (
            db.query(Company.purpose)
            .filter(Company.purpose.isnot(None))
            .order_by(Company.id)
            .offset(offset)
            .limit(chunk)
            .all()
        )
        if not rows:
            break
        for (purpose,) in rows:
            for sentence in _SENTENCE_SPLIT.split(purpose):
                s = sentence.strip()
                if len(s) < 30:
                    continue
                norm = _normalise_sentence(s)
                h = hashlib.md5(norm.encode("utf-8")).hexdigest()
                sentence_counts[h] += 1
                if h not in sentence_text:
                    sentence_text[h] = s
        offset += chunk

    boilerplate_hashes = [h for h, cnt in sentence_counts.items() if cnt >= _BOILERPLATE_MIN_MATCH_COUNT]
    if not boilerplate_hashes:
        return 0

    from app import crud
    written = 0
    for h in boilerplate_hashes:
        sentence = sentence_text[h]
        count = sentence_counts[h]
        # Escape for regex: use re.escape so the full sentence becomes a pattern
        pattern = re.escape(sentence[:200])  # cap pattern length
        try:
            crud.upsert_boilerplate_pattern(
                db,
                pattern=pattern,
                description=f"Auto-discovered: appears in {count} companies",
                enabled=False,
                source="sentence_dedup",
            )
            written += 1
        except Exception:
            logger.warning("Phase 2: failed to upsert boilerplate pattern", exc_info=True)
    db.commit()
    logger.info("Phase 2: staged %d boilerplate sentence patterns (threshold: %d companies)", written, _BOILERPLATE_MIN_MATCH_COUNT)
    return written


# ── Phase 3 ───────────────────────────────────────────────────────────────────

def _phase3_cross_cluster(db: Session) -> int:
    """Find terms in >60% of cluster labels and stage them as stopword candidates."""
    from collections import Counter
    from sqlalchemy import func
    from app.models.company import Company

    rows = (
        db.query(Company.tfidf_cluster, func.count(Company.id).label("cnt"))
        .filter(Company.tfidf_cluster.isnot(None))
        .filter(Company.tfidf_cluster != "Undefined")
        .group_by(Company.tfidf_cluster)
        .all()
    )

    # Collect unique cluster labels
    all_labels: set[str] = set()
    for full_value, _ in rows:
        for label in full_value.split("|"):
            label = label.strip()
            if label:
                all_labels.add(label)

    n_labels = len(all_labels)
    if n_labels == 0:
        return 0

    term_counter: Counter = Counter()
    for label in all_labels:
        for term in label.split(","):
            term = term.strip()
            if term and len(term) >= 3:
                term_counter[term] += 1

    threshold = _CROSS_CLUSTER_LABEL_FREQ_THRESHOLD * n_labels
    candidates = [term for term, count in term_counter.items() if count >= threshold]

    if not candidates:
        return 0

    from app import crud
    written = 0
    for term in candidates:
        try:
            crud.upsert_tfidf_stopword(db, term=term, enabled=False, source="cross_cluster")
            written += 1
        except Exception:
            logger.warning("Phase 3: failed to upsert stopword %r", term, exc_info=True)
    db.commit()
    logger.info("Phase 3: staged %d cross-cluster stopword candidates (label freq >= %.0f%%)", written, _CROSS_CLUSTER_LABEL_FREQ_THRESHOLD * 100)
    return written


# ── Phase 4 ───────────────────────────────────────────────────────────────────

_PHASE4_PROMPT = """\
You are reviewing candidate stopwords and boilerplate phrases found in Swiss commercial register (Zefix) company purpose texts. These texts are written in German, French, Italian, English, or Romansh.

Your task:
1. Detect the language(s) of each candidate.
2. Classify each as one of:
   - "always_boilerplate": Generic legal/statutory text that conveys no business-specific information (e.g. "sowie alle damit zusammenhängenden Tätigkeiten", "société anonyme", "Zweck der Gesellschaft ist").
   - "sometimes_meaningful": Could be meaningful in some contexts but is often generic (e.g. "verwaltung", "handel", "services").
   - "keep_as_signal": Specific enough to carry business meaning — do NOT remove from indexing.

Return a JSON object grouped by language code. For example:
{
  "de": [
    {"term": "gesellschaft", "verdict": "always_boilerplate"},
    {"term": "software", "verdict": "keep_as_signal"}
  ],
  "fr": [
    {"term": "société", "verdict": "always_boilerplate"}
  ],
  "it": [
    {"term": "nonché", "verdict": "always_boilerplate"}
  ]
}

If a term is language-neutral or ambiguous, group it under the most likely language.
If you are unsure, default to "sometimes_meaningful".
Return ONLY the JSON object, no other text.

Candidates to review:
STOPWORDS: {stopwords}

BOILERPLATE_PHRASES: {boilerplate}
"""


def _phase4_claude_review(db: Session, *, api_key: str | None) -> int:
    """Single Claude Haiku call to classify and auto-approve boilerplate candidates."""
    import json
    from app import crud

    if not api_key:
        logger.info("Phase 4: no Anthropic API key, skipping")
        return 0

    # Collect newly staged (not yet enabled, not yet reviewed) candidates
    stopword_candidates = crud.list_tfidf_stopwords(db, enabled=False, limit=_REVIEW_MAX_STOPWORDS)
    boilerplate_candidates = crud.list_boilerplate_patterns(db, enabled=False, limit=_REVIEW_MAX_BOILERPLATE)

    if not stopword_candidates and not boilerplate_candidates:
        logger.info("Phase 4: no pending candidates to review")
        return 0

    stopwords_text = ", ".join(s.term for s in stopword_candidates) or "(none)"
    boilerplate_text = "; ".join(b.pattern[:80] for b in boilerplate_candidates) or "(none)"

    prompt = _PHASE4_PROMPT.format(stopwords=stopwords_text, boilerplate=boilerplate_text)

    try:
        from app.services.claude import claude_call, strip_fences
        raw_response, _ = claude_call("", prompt, api_key=api_key, max_tokens=2048, cache_system=False)
    except Exception as exc:
        raise RuntimeError(f"Claude API call failed: {exc}") from exc

    try:
        review_data: dict[str, list[dict]] = json.loads(strip_fences(raw_response))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Claude returned invalid JSON: {exc}\nResponse: {raw_response[:500]}") from exc

    # Build lookup maps
    sw_by_term = {s.term: s for s in stopword_candidates}
    bp_by_pattern_prefix = {b.pattern[:80]: b for b in boilerplate_candidates}

    auto_approved = 0
    for lang_code, items in review_data.items():
        for item in items:
            term = item.get("term", "")
            verdict = item.get("verdict", "")
            if not term or not verdict:
                continue

            if verdict == "always_boilerplate":
                # Auto-approve in stopwords table
                if term in sw_by_term:
                    crud.update_tfidf_stopword(db, sw_by_term[term].id, enabled=True, language=lang_code, llm_verdict=verdict)
                    auto_approved += 1
                # Auto-approve matching boilerplate patterns
                for prefix, bp in bp_by_pattern_prefix.items():
                    if term.lower() in prefix.lower():
                        crud.update_boilerplate_pattern(db, bp.id, enabled=True)
                        auto_approved += 1
            elif verdict == "keep_as_signal":
                # Remove from suggestions entirely
                if term in sw_by_term:
                    crud.delete_tfidf_stopword(db, sw_by_term[term].id)
            # sometimes_meaningful: leave as enabled=False for human review

    db.commit()
    logger.info("Phase 4: Claude review complete — %d candidates auto-approved", auto_approved)
    return auto_approved
