from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from app import crud
from app.models.company import Company
from app.schemas.company import CompanyUpdate

logger = logging.getLogger(__name__)

_WORD_RE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9]{3,}")
_SENTENCE_SPLIT = re.compile(r"(?<=\.)\s+(?=[A-ZÄÖÜ])")

# Hierarchical classification weights: rely more on embeddings (0.8), less on tokens (0.2)
_W_EMB: float = 0.8
_W_TOK: float = 0.2

# Per-level ANN search parameters
_LEVEL_TOP_K: int = 10

# Tie-breaking via lookahead: fire lookahead when top-2 candidates differ by ≤ this
_LOOKAHEAD_TIE_THRESHOLD: float = 0.05

# EXCLUDES penalty: cosine sim to EXCLUDES embedding weighted this much
_EXCL_COSINE_WEIGHT: float = 0.20

# Confidence threshold below which a result is considered low-confidence
NOGA_LOW_CONFIDENCE: float = 0.80

# Default language when detection returns None
_DEFAULT_LANG = "de"


@dataclass(frozen=True)
class LevelResult:
    """Classification result at one hierarchy level."""
    code: str
    label: str | None
    confidence: float  # raw cosine similarity to INCLUDES embedding


@dataclass(frozen=True)
class NogaClassification:
    """Complete hierarchical classification result (level 1 → 5)."""
    code: str  # final level-5 code (backward compat)
    label: str | None  # label of final code (backward compat)
    level: str | None  # level name of final code (backward compat)
    confidence: float  # level-5 confidence (backward compat)
    level_results: tuple[LevelResult, ...]  # one per level, L1 → L5

    @property
    def level_confidence_json(self) -> dict[str, float]:
        """Return per-level confidences as JSON for storage."""
        return {str(i + 1): r.confidence for i, r in enumerate(self.level_results)}


@dataclass(frozen=True)
class _NogaIndex:
    token_to_codes: dict[str, set[str]]
    code_meta: dict[str, dict[str, Any]]


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _normalize_text(value: str) -> str:
    return value.strip().lower()


def _collect_multilang_text(v: Any) -> list[str]:
    if isinstance(v, str):
        txt = v.strip()
        return [txt] if txt else []
    if isinstance(v, dict):
        out: list[str] = []
        for k in ("de", "fr", "it", "en", "rm"):
            raw = v.get(k)
            if isinstance(raw, str) and raw.strip():
                out.append(raw.strip())
        if out:
            return out
        for raw in v.values():
            if isinstance(raw, str) and raw.strip():
                out.append(raw.strip())
        return out
    return []


def _tokens_from_texts(texts: list[str]) -> set[str]:
    tokens: set[str] = set()
    for t in texts:
        for m in _WORD_RE.findall(t.lower()):
            tok = m.strip().lower()
            if tok:
                tokens.add(tok)
    return tokens


def _strip_purpose_boilerplate(text: str, patterns: list[re.Pattern]) -> str:
    if not text or len(text) < 40 or not patterns:
        return text
    sentences = _SENTENCE_SPLIT.split(text.strip())
    kept = [s for s in sentences if s.strip() and not any(pat.search(s) for pat in patterns)]
    result = " ".join(kept).strip()
    return result if result else text


def _extract_node_tokens(node: dict[str, Any]) -> set[str]:
    texts: list[str] = []
    texts.extend(_collect_multilang_text(node.get("name")))
    for ann in node.get("annotations") or []:
        if not isinstance(ann, dict):
            continue
        texts.extend(_collect_multilang_text(ann.get("text")))
    return _tokens_from_texts(texts)


@lru_cache(maxsize=1)
def _load_noga_index() -> _NogaIndex:
    lookup_path = _repo_root() / "noga_lookup.json"
    if not lookup_path.exists():
        raise FileNotFoundError(
            f"NOGA lookup not found at {lookup_path}. Run scripts/create_noga_json.py once first."
        )
    with lookup_path.open("r", encoding="utf-8") as f:
        payload = json.load(f)
    if not isinstance(payload, dict):
        raise ValueError("noga_lookup.json must be a dict mapping code -> node")

    token_to_codes: dict[str, set[str]] = {}
    code_meta: dict[str, dict[str, Any]] = {}

    for code, raw_node in payload.items():
        if not isinstance(raw_node, dict):
            continue
        node = dict(raw_node)
        node["code"] = str(code)
        code_meta[str(code)] = node
        for tok in _extract_node_tokens(node):
            token_to_codes.setdefault(tok, set()).add(str(code))

    return _NogaIndex(token_to_codes=token_to_codes, code_meta=code_meta)


@lru_cache(maxsize=1)
def _load_parent_map() -> dict[str, str]:
    lookup_path = _repo_root() / "noga_lookup.json"
    with lookup_path.open("r", encoding="utf-8") as f:
        payload: dict = json.load(f)
    return {
        str(code): str(node["parentCode"])
        for code, node in payload.items()
        if isinstance(node, dict) and "parentCode" in node
    }


def _build_noga_path(code: str) -> tuple[str, str]:
    """Walk noga_lookup.json from *code* up to root, return (codes_path, labels_path).

    Both are pipe-separated strings ordered from root (section) to leaf:
      codes_path:  "C|26|263|2630|263001"
      labels_path: "Verarbeitendes Gewerbe|Herstellung von ...|..."
    """
    idx = _load_noga_index()
    parent_map = _load_parent_map()

    chain: list[str] = []
    cur = str(code)
    visited: set[str] = set()
    while cur and cur not in visited:
        chain.append(cur)
        visited.add(cur)
        cur = parent_map.get(cur, "")
    chain.reverse()

    code_parts: list[str] = []
    label_parts: list[str] = []
    for c in chain:
        code_parts.append(c)
        meta = idx.code_meta.get(c, {})
        name = meta.get("name")
        if isinstance(name, dict):
            lbl = name.get("de") or name.get("fr") or name.get("it") or name.get("en") or c
        elif isinstance(name, str):
            lbl = name
        else:
            lbl = c
        label_parts.append(lbl)

    return "|".join(code_parts), "|".join(label_parts)


# ---------------------------------------------------------------------------
# pgvector similarity search
# ---------------------------------------------------------------------------

def _pgvector_search_level(
    db: Session,
    query_vec,
    lang: str,
    level_no: int,
    parent_codes: list[str] | None = None,
    top_k: int = _LEVEL_TOP_K,
) -> list[tuple[str, float]]:
    """Return [(noga_code, cosine_similarity), ...] for INCLUDES embeddings at one level.

    When parent_codes is given, restricts results to children of those parents.
    Falls back to 'de' embeddings if requested lang has no rows.
    """
    import numpy as np

    vec_list = query_vec.tolist() if isinstance(query_vec, np.ndarray) else list(query_vec)
    vec_str = "[" + ",".join(f"{x:.8f}" for x in vec_list) + "]"

    if parent_codes:
        rows = db.execute(sql_text("""
            WITH q AS (SELECT CAST(:vec AS vector) AS v)
            SELECT noga_code, 1 - (embedding <=> q.v) AS similarity
            FROM   noga_embeddings, q
            WHERE  lang     = :lang
              AND  level_no = :level_no
              AND  ann_type = 'includes'
              AND  parent_code = ANY(:parents)
            ORDER  BY embedding <=> q.v
            LIMIT  :k
        """), {
            "vec": vec_str, "lang": lang, "level_no": level_no,
            "parents": parent_codes, "k": top_k,
        }).fetchall()
    else:
        rows = db.execute(sql_text("""
            WITH q AS (SELECT CAST(:vec AS vector) AS v)
            SELECT noga_code, 1 - (embedding <=> q.v) AS similarity
            FROM   noga_embeddings, q
            WHERE  lang     = :lang
              AND  level_no = :level_no
              AND  ann_type = 'includes'
            ORDER  BY embedding <=> q.v
            LIMIT  :k
        """), {
            "vec": vec_str, "lang": lang, "level_no": level_no,
            "k": top_k,
        }).fetchall()

    if not rows and lang != _DEFAULT_LANG:
        return _pgvector_search_level(db, query_vec, _DEFAULT_LANG, level_no, parent_codes, top_k)

    return [(r.noga_code, float(r.similarity)) for r in rows]


def _excludes_cosine_penalty(
    db: Session,
    candidate_codes: list[str],
    query_vec,
    lang: str,
) -> dict[str, float]:
    """Query EXCLUDES embeddings for candidates, return cosine similarity dict.

    Returns only codes that have EXCLUDES rows (missing = 0.0 penalty).
    """
    import numpy as np

    if not candidate_codes:
        return {}

    vec_list = query_vec.tolist() if isinstance(query_vec, np.ndarray) else list(query_vec)
    vec_str = "[" + ",".join(f"{x:.8f}" for x in vec_list) + "]"

    rows = db.execute(sql_text("""
        WITH q AS (SELECT CAST(:vec AS vector) AS v)
        SELECT noga_code, 1 - (embedding <=> q.v) AS excl_sim
        FROM   noga_embeddings, q
        WHERE  noga_code = ANY(:codes)
          AND  lang = :lang
          AND  ann_type = 'excludes'
    """), {
        "vec": vec_str, "lang": lang, "codes": candidate_codes,
    }).fetchall()

    return {r.noga_code: float(r.excl_sim) for r in rows}


_noga_embeddings_present: bool | None = None


def _has_noga_embeddings(db: Session) -> bool:
    global _noga_embeddings_present
    if _noga_embeddings_present:
        return True
    try:
        row = db.execute(sql_text("SELECT 1 FROM noga_embeddings LIMIT 1")).fetchone()
        _noga_embeddings_present = row is not None
    except Exception:
        _noga_embeddings_present = False
    return bool(_noga_embeddings_present)


# ---------------------------------------------------------------------------
# Core hierarchical classification
# ---------------------------------------------------------------------------

def _company_tokens(db: Session, company: Company) -> set[str]:
    texts: list[str] = []
    boilerplate_patterns = crud.get_active_boilerplate_patterns(db)
    if company.name:
        texts.append(company.name)
    if company.purpose:
        texts.append(_strip_purpose_boilerplate(company.purpose, boilerplate_patterns))
    if company.purpose_keywords:
        texts.append(company.purpose_keywords.replace(",", " "))
    if company.tfidf_cluster:
        texts.append(company.tfidf_cluster.replace("|", " ").replace(",", " "))
    return _tokens_from_texts(texts)


def _embed_query(text: str):
    """Return a normalized float32 embedding vector, or None on failure."""
    try:
        from app.services.embeddings import embed_single
        import numpy as np
        vec = embed_single(text)
        if np.linalg.norm(vec) < 1e-6:
            return None
        return vec
    except Exception as exc:
        logger.warning("Embedding query failed: %s", exc)
        return None


def _get_label(meta: dict[str, Any], lang: str) -> str | None:
    """Extract label in requested language, with fallbacks."""
    name = meta.get("name")
    if isinstance(name, dict):
        return (
            name.get(lang)
            or name.get("de")
            or name.get("fr")
            or name.get("it")
            or name.get("en")
        )
    elif isinstance(name, str):
        return name
    return None


def classify_company_noga(db: Session, company: Company) -> NogaClassification | None:
    """Hierarchical level-by-level NOGA classification (L1 → L2 → L3 → L4 → L5).

    Descends through the hierarchy using:
      1. Embedding similarity (primary, 80% weight)
      2. Token overlap (secondary, 20% weight)
      3. EXCLUDES cosine penalty (negative signal)
      4. Lookahead tie-breaking (when top-2 candidates are close)
    """
    from app.services.language_detection import detect_purpose_language

    # --- Language detection ---
    lang = (
        company.purpose_language
        or detect_purpose_language(company.purpose)
        or _DEFAULT_LANG
    )

    # --- Token scores (always computed) ---
    tokens = _company_tokens(db, company)
    if not tokens:
        logger.debug("No tokens extracted for company %s (UID: %s), skipping NOGA classification",
                    company.name, company.uid)
        return None

    idx = _load_noga_index()

    token_scores: dict[str, float] = {}
    for tok in tokens:
        for code in idx.token_to_codes.get(tok, set()):
            token_scores[code] = token_scores.get(code, 0.0) + 1.0

    max_tok = max(token_scores.values()) if token_scores else 1.0
    norm_token: dict[str, float] = {c: s / max_tok for c, s in token_scores.items()}

    # --- Embedding setup ---
    boilerplate_patterns = crud.get_active_boilerplate_patterns(db)
    stripped_purpose = _strip_purpose_boilerplate(company.purpose or "", boilerplate_patterns)
    embed_text = " ".join(filter(None, [
        stripped_purpose,
        (company.purpose_keywords or "").replace(",", " "),
    ])).strip()

    use_embeddings = bool(embed_text) and _has_noga_embeddings(db)
    query_vec = None
    if use_embeddings:
        query_vec = _embed_query(embed_text)
        use_embeddings = query_vec is not None

    if not token_scores and not use_embeddings:
        logger.debug("No token overlap and no embeddings for company %s (UID: %s), cannot classify",
                     company.name, company.uid)
        return None

    # --- Hierarchical descent L1 → L5 ---
    level_results: list[LevelResult] = []
    current_parent_codes: list[str] | None = None

    for level_no in range(1, 6):
        # --- Get INCLUDES candidates at this level ---
        emb_candidates: list[tuple[str, float]] = []
        if use_embeddings:
            emb_candidates = _pgvector_search_level(
                db, query_vec, lang, level_no,
                parent_codes=current_parent_codes,
                top_k=_LEVEL_TOP_K,
            )

        # --- Compute hybrid scores ---
        hybrid: dict[str, float] = {}

        # Score embedding results
        for code, sim in emb_candidates:
            hybrid[code] = _W_EMB * sim + _W_TOK * norm_token.get(code, 0.0)

        # Add token-only candidates filtered to this level
        for code, tok_score in norm_token.items():
            meta = idx.code_meta.get(code, {})
            if meta.get("level_no") != level_no:
                continue
            if current_parent_codes and meta.get("parentCode") not in current_parent_codes:
                continue
            if code not in hybrid:
                hybrid[code] = _W_TOK * tok_score

        # --- Apply EXCLUDES penalty ---
        if use_embeddings and hybrid:
            excl_sims = _excludes_cosine_penalty(
                db, list(hybrid.keys()), query_vec, lang
            )
            for code, excl_sim in excl_sims.items():
                hybrid[code] = hybrid[code] * (1.0 - _EXCL_COSINE_WEIGHT * excl_sim)

        # --- Fallback if no candidates ---
        if not hybrid:
            logger.debug("No candidates at level %d for company %s, using fallback", level_no, company.uid)
            # Use first child of previous winner from noga_lookup (confidence=0.0)
            if level_results:
                prev_code = level_results[-1].code
                for code, meta in idx.code_meta.items():
                    if meta.get("parentCode") == prev_code and meta.get("level_no") == level_no:
                        label = _get_label(meta, lang)
                        level_results.append(LevelResult(code=code, label=label, confidence=0.0))
                        current_parent_codes = [code]
                        break
                else:
                    # No children found — shouldn't happen but be safe
                    return None
            else:
                # Level 1 and no candidates — cannot classify
                return None
            continue

        # --- Select winner ---
        candidates_sorted = sorted(hybrid.items(), key=lambda x: x[1], reverse=True)
        winner = candidates_sorted[0][0]
        winner_score = candidates_sorted[0][1]

        # --- Lookahead tie-breaking (levels 1–4 only) ---
        if level_no < 5 and len(candidates_sorted) >= 2:
            runner_up = candidates_sorted[1][0]
            runner_up_score = candidates_sorted[1][1]
            if (winner_score - runner_up_score) <= _LOOKAHEAD_TIE_THRESHOLD:
                # Query children of both at level_no+1
                tied = [winner, runner_up]
                child_results = _pgvector_search_level(
                    db, query_vec, lang, level_no + 1,
                    parent_codes=tied,
                    top_k=_LEVEL_TOP_K,
                )

                # Score each tied parent by best child cosine sim
                best_child: dict[str, float] = {}
                for child_code, child_sim in child_results:
                    meta = idx.code_meta.get(child_code, {})
                    p = meta.get("parentCode", "")
                    best_child[p] = max(best_child.get(p, 0.0), child_sim)

                # Lookahead boost (50% weight to tiebreak, not override)
                for parent_code in tied:
                    child_boost = best_child.get(parent_code, 0.0)
                    hybrid[parent_code] = hybrid[parent_code] + 0.5 * child_boost

                # Re-select winner
                winner = max(tied, key=lambda c: hybrid[c])
                winner_score = hybrid[winner]

        # --- Store result at this level ---
        meta = idx.code_meta.get(winner, {})
        label = _get_label(meta, lang)

        # Confidence = raw cosine similarity to INCLUDES (or token proportion if token-only)
        raw_sim = next((s for c, s in emb_candidates if c == winner), None)
        if raw_sim is None:
            # Token-only path
            total = sum(token_scores.values())
            raw_sim = token_scores.get(winner, 0.0) / total if total > 0 else 0.0

        confidence = max(0.0, min(1.0, raw_sim))
        level_results.append(LevelResult(code=winner, label=label, confidence=confidence))
        current_parent_codes = [winner]

    # --- Return final result ---
    final = level_results[-1]  # level-5 result
    return NogaClassification(
        code=final.code,
        label=final.label,
        level="Art",  # Always level-5
        confidence=final.confidence,
        level_results=tuple(level_results),
    )


# ---------------------------------------------------------------------------
# V2: global multi-level search → peak → constrained descent (experimental)
# ---------------------------------------------------------------------------

# Shallower levels get a small bonus so an L2 code at 0.68 beats an L4 code at 0.69
_DEPTH_BONUS_PER_LEVEL: float = 0.02


def classify_company_noga_v2(db: Session, company: Company) -> dict:
    """Experimental hybrid classifier.

    Instead of descending top-down from L1, searches all levels simultaneously,
    finds the code with the highest adjusted similarity (the 'peak'), then does
    a constrained descent from the peak to L5. Returns both the peak result and
    the derived leaf — without writing anything to the DB.
    """
    import numpy as np
    from app.services.language_detection import detect_purpose_language

    boilerplate_patterns = crud.get_active_boilerplate_patterns(db)
    stripped_purpose = _strip_purpose_boilerplate(company.purpose or "", boilerplate_patterns)
    lang = company.purpose_language or detect_purpose_language(company.purpose) or _DEFAULT_LANG

    embed_text = " ".join(filter(None, [
        stripped_purpose,
        (company.purpose_keywords or "").replace(",", " "),
    ])).strip()

    if not embed_text:
        return {"error": "No text to embed", "lang": lang}
    if not _has_noga_embeddings(db):
        return {"error": "No NOGA embeddings in database", "lang": lang}

    query_vec = _embed_query(embed_text)
    if query_vec is None:
        return {"error": "Embedding computation failed", "lang": lang}

    vec_list = query_vec.tolist() if isinstance(query_vec, np.ndarray) else list(query_vec)
    vec_str = "[" + ",".join(f"{x:.8f}" for x in vec_list) + "]"

    def _global_search(l: str) -> list:
        return db.execute(sql_text("""
            WITH q AS (SELECT CAST(:vec AS vector) AS v)
            SELECT noga_code, level_no, 1 - (embedding <=> q.v) AS similarity
            FROM   noga_embeddings, q
            WHERE  lang = :lang AND ann_type = 'includes'
            ORDER  BY embedding <=> q.v
            LIMIT  50
        """), {"vec": vec_str, "lang": l}).fetchall()

    rows = _global_search(lang)
    if not rows and lang != _DEFAULT_LANG:
        rows = _global_search(_DEFAULT_LANG)

    if not rows:
        return {"error": "No results from global search", "lang": lang}

    # Apply excludes penalty
    candidate_codes = [r.noga_code for r in rows]
    excl_sims = _excludes_cosine_penalty(db, candidate_codes, query_vec, lang)

    # Score: penalized similarity + shallow-level depth bonus
    idx = _load_noga_index()
    scored: list[dict] = []
    for r in rows:
        sim = float(r.similarity)
        excl = excl_sims.get(r.noga_code, 0.0)
        penalized = sim * (1.0 - _EXCL_COSINE_WEIGHT * excl)
        depth_bonus = (5 - int(r.level_no)) * _DEPTH_BONUS_PER_LEVEL
        meta = idx.code_meta.get(r.noga_code, {})
        scored.append({
            "code": r.noga_code,
            "label": _get_label(meta, lang),
            "level_no": int(r.level_no),
            "raw_sim": round(sim, 4),
            "excl_sim": round(excl, 4) if excl > 0 else None,
            "penalized_sim": round(penalized, 4),
            "depth_bonus": round(depth_bonus, 4),
            "adjusted_score": round(penalized + depth_bonus, 4),
            "is_peak": False,
        })

    scored.sort(key=lambda x: x["adjusted_score"], reverse=True)
    scored[0]["is_peak"] = True
    peak = scored[0]
    peak_code = peak["code"]
    peak_level_no = peak["level_no"]
    peak_path, peak_path_labels = _build_noga_path(peak_code)

    # Constrained descent from peak level+1 down to L5
    descent_levels: list[dict] = []
    current_parent_codes = [peak_code]

    for level_no in range(peak_level_no + 1, 6):
        level_rows = _pgvector_search_level(
            db, query_vec, lang, level_no,
            parent_codes=current_parent_codes,
            top_k=_LEVEL_TOP_K,
        )
        if not level_rows:
            break
        winner_code, winner_sim = level_rows[0]
        winner_meta = idx.code_meta.get(winner_code, {})
        descent_levels.append({
            "level_no": level_no,
            "code": winner_code,
            "label": _get_label(winner_meta, lang),
            "sim": round(float(winner_sim), 4),
            "top_candidates": [
                {
                    "code": c,
                    "label": _get_label(idx.code_meta.get(c, {}), lang),
                    "sim": round(float(s), 4),
                    "is_winner": c == winner_code,
                }
                for c, s in level_rows[:5]
            ],
        })
        current_parent_codes = [winner_code]

    if descent_levels:
        leaf_code = descent_levels[-1]["code"]
        leaf_label = descent_levels[-1]["label"]
        leaf_sim = descent_levels[-1]["sim"]
    else:
        leaf_code, leaf_label, leaf_sim = peak_code, peak["label"], peak["raw_sim"]

    leaf_path, leaf_path_labels = _build_noga_path(leaf_code)

    return {
        "lang": lang,
        "embed_text": embed_text,
        "depth_bonus_per_level": _DEPTH_BONUS_PER_LEVEL,
        "global_top_candidates": scored[:20],
        "peak_code": peak_code,
        "peak_level_no": peak_level_no,
        "peak_label": peak["label"],
        "peak_raw_sim": peak["raw_sim"],
        "peak_penalized_sim": peak["penalized_sim"],
        "peak_adjusted_score": peak["adjusted_score"],
        "peak_path": peak_path,
        "peak_path_labels": peak_path_labels,
        "descent_levels": descent_levels,
        "leaf_code": leaf_code,
        "leaf_label": leaf_label,
        "leaf_sim": leaf_sim,
        "leaf_path": leaf_path,
        "leaf_path_labels": leaf_path_labels,
    }


# ---------------------------------------------------------------------------
# Explain / trace (debug only)
# ---------------------------------------------------------------------------

def classify_company_noga_explain(db: Session, company: Company) -> dict:
    """Re-run NOGA classification with full intermediate trace for debugging.

    Mirrors classify_company_noga exactly but captures all intermediate scores
    so callers can understand why a particular code was chosen at each level.
    """
    from app.services.language_detection import detect_purpose_language

    boilerplate_patterns = crud.get_active_boilerplate_patterns(db)
    stripped_purpose = _strip_purpose_boilerplate(company.purpose or "", boilerplate_patterns)

    lang = (
        company.purpose_language
        or detect_purpose_language(company.purpose)
        or _DEFAULT_LANG
    )

    tokens = _company_tokens(db, company)

    embed_text = " ".join(filter(None, [
        stripped_purpose,
        (company.purpose_keywords or "").replace(",", " "),
    ])).strip()

    embeddings_ok = bool(embed_text) and _has_noga_embeddings(db)
    query_vec = None
    if embeddings_ok:
        query_vec = _embed_query(embed_text)
        embeddings_ok = query_vec is not None

    trace: dict = {
        "purpose_original": company.purpose,
        "purpose_stripped": stripped_purpose if stripped_purpose != (company.purpose or "") else None,
        "boilerplate_patterns_active": len(boilerplate_patterns),
        "detected_language": lang,
        "tokens": sorted(tokens),
        "embed_text": embed_text or None,
        "embeddings_available": embeddings_ok,
        "levels": [],
        "final_code": None,
        "final_label": None,
        "final_confidence": None,
    }

    if not tokens:
        return trace

    idx = _load_noga_index()

    token_scores: dict[str, float] = {}
    for tok in tokens:
        for code in idx.token_to_codes.get(tok, set()):
            token_scores[code] = token_scores.get(code, 0.0) + 1.0

    max_tok = max(token_scores.values()) if token_scores else 1.0
    norm_token: dict[str, float] = {c: s / max_tok for c, s in token_scores.items()}

    if not token_scores and not embeddings_ok:
        return trace

    current_parent_codes: list[str] | None = None
    level_results: list[LevelResult] = []

    for level_no in range(1, 6):
        level_trace: dict = {
            "level_no": level_no,
            "parent_codes": current_parent_codes,
            "lookahead_applied": False,
            "fallback_used": False,
            "candidates": [],
            "winner_code": None,
            "winner_label": None,
            "confidence": None,
        }

        emb_candidates: list[tuple[str, float]] = []
        if embeddings_ok:
            emb_candidates = _pgvector_search_level(
                db, query_vec, lang, level_no,
                parent_codes=current_parent_codes,
                top_k=_LEVEL_TOP_K,
            )
        emb_sim_map: dict[str, float] = dict(emb_candidates)

        hybrid: dict[str, float] = {}
        for code, sim in emb_candidates:
            hybrid[code] = _W_EMB * sim + _W_TOK * norm_token.get(code, 0.0)
        for code, tok_score in norm_token.items():
            meta = idx.code_meta.get(code, {})
            if meta.get("level_no") != level_no:
                continue
            if current_parent_codes and meta.get("parentCode") not in current_parent_codes:
                continue
            if code not in hybrid:
                hybrid[code] = _W_TOK * tok_score

        excl_sims: dict[str, float] = {}
        if embeddings_ok and hybrid:
            excl_sims = _excludes_cosine_penalty(db, list(hybrid.keys()), query_vec, lang)
            for code, excl_sim in excl_sims.items():
                hybrid[code] = hybrid[code] * (1.0 - _EXCL_COSINE_WEIGHT * excl_sim)

        if not hybrid:
            level_trace["fallback_used"] = True
            if level_results:
                prev_code = level_results[-1].code
                for code, meta in idx.code_meta.items():
                    if meta.get("parentCode") == prev_code and meta.get("level_no") == level_no:
                        label = _get_label(meta, lang)
                        level_results.append(LevelResult(code=code, label=label, confidence=0.0))
                        current_parent_codes = [code]
                        level_trace["winner_code"] = code
                        level_trace["winner_label"] = label
                        level_trace["confidence"] = 0.0
                        break
            trace["levels"].append(level_trace)
            if not level_results or len(level_results) < level_no:
                break
            continue

        candidates_sorted = sorted(hybrid.items(), key=lambda x: x[1], reverse=True)
        winner = candidates_sorted[0][0]
        winner_score = candidates_sorted[0][1]

        if embeddings_ok and level_no < 5 and len(candidates_sorted) >= 2:
            runner_up = candidates_sorted[1][0]
            runner_up_score = candidates_sorted[1][1]
            if (winner_score - runner_up_score) <= _LOOKAHEAD_TIE_THRESHOLD:
                level_trace["lookahead_applied"] = True
                tied = [winner, runner_up]
                child_results = _pgvector_search_level(
                    db, query_vec, lang, level_no + 1,
                    parent_codes=tied,
                    top_k=_LEVEL_TOP_K,
                )
                best_child: dict[str, float] = {}
                for child_code, child_sim in child_results:
                    p = idx.code_meta.get(child_code, {}).get("parentCode", "")
                    best_child[p] = max(best_child.get(p, 0.0), child_sim)
                for parent_code in tied:
                    hybrid[parent_code] = hybrid[parent_code] + 0.5 * best_child.get(parent_code, 0.0)
                winner = max(tied, key=lambda c: hybrid[c])
                winner_score = hybrid[winner]
                candidates_sorted = sorted(hybrid.items(), key=lambda x: x[1], reverse=True)

        for code, final_score in candidates_sorted[:10]:
            c_meta = idx.code_meta.get(code, {})
            level_trace["candidates"].append({
                "code": code,
                "label": _get_label(c_meta, lang),
                "embedding_sim": round(emb_sim_map[code], 4) if code in emb_sim_map else None,
                "token_score_normalized": round(norm_token[code], 4) if code in norm_token else None,
                "excludes_sim": round(excl_sims[code], 4) if code in excl_sims else None,
                "hybrid_score_final": round(final_score, 4),
                "is_winner": code == winner,
            })

        meta = idx.code_meta.get(winner, {})
        label = _get_label(meta, lang)
        raw_sim = emb_sim_map.get(winner)
        if raw_sim is None:
            total = sum(token_scores.values())
            raw_sim = token_scores.get(winner, 0.0) / total if total > 0 else 0.0
        confidence = max(0.0, min(1.0, raw_sim))

        level_results.append(LevelResult(code=winner, label=label, confidence=confidence))
        current_parent_codes = [winner]
        level_trace["winner_code"] = winner
        level_trace["winner_label"] = label
        level_trace["confidence"] = round(confidence, 4)
        trace["levels"].append(level_trace)

    if level_results:
        final = level_results[-1]
        trace["final_code"] = final.code
        trace["final_label"] = final.label
        trace["final_confidence"] = round(final.confidence, 4)

    return trace


# ---------------------------------------------------------------------------
# Branch office helpers
# ---------------------------------------------------------------------------

_BRANCH_KEYWORDS = ("zweigniederlassung", "succursale", "filiale di")
_BRANCH_LEGAL_FORM_UIDS = frozenset(("0108", "0111"))


def is_branch_office(company: Company) -> bool:
    if company.legal_form_uid in _BRANCH_LEGAL_FORM_UIDS:
        return True
    name_lower = (company.name or "").lower()
    return any(k in name_lower for k in _BRANCH_KEYWORDS)


def _parent_uid_from_head_offices(company: Company) -> str | None:
    raw = company.head_offices
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return None
    candidates: list[Any] = []
    if isinstance(payload, list):
        candidates = payload
    elif isinstance(payload, dict):
        candidates = [payload]
    for entry in candidates:
        if not isinstance(entry, dict):
            continue
        uid = entry.get("uid") or entry.get("UID") or entry.get("uid_full")
        if isinstance(uid, str) and uid.strip():
            return uid.strip()
    return None


def _inherit_noga_from_parent(db: Session, company: Company) -> CompanyUpdate | None:
    parent_uid = _parent_uid_from_head_offices(company)
    if not parent_uid:
        return None
    parent = crud.get_company_by_uid(db, parent_uid)
    if parent is None or not parent.noga_code:
        return None
    return CompanyUpdate(
        noga_code=parent.noga_code,
        noga_label=parent.noga_label,
        noga_level=parent.noga_level,
        noga_confidence=parent.noga_confidence,
        noga_level_confidence=parent.noga_level_confidence,
        noga_classified_at=datetime.now(tz=timezone.utc),
        noga_path=parent.noga_path,
        noga_path_labels=parent.noga_path_labels,
    )


def _clear_noga() -> CompanyUpdate:
    return CompanyUpdate(
        noga_code=None,
        noga_label=None,
        noga_level=None,
        noga_confidence=None,
        noga_level_confidence=None,
        noga_classified_at=datetime.now(tz=timezone.utc),
        noga_path=None,
        noga_path_labels=None,
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def apply_noga_classification(db: Session, company: Company) -> CompanyUpdate | None:
    if is_branch_office(company):
        inherited = _inherit_noga_from_parent(db, company)
        if inherited is not None:
            return inherited
        return _clear_noga() if company.noga_code else None

    result = classify_company_noga(db, company)
    if not result:
        return None

    noga_path, noga_path_labels = _build_noga_path(result.code)

    return CompanyUpdate(
        noga_code=result.code,
        noga_label=result.label,
        noga_level=result.level,
        noga_confidence=result.confidence,
        noga_level_confidence=result.level_confidence_json,
        noga_classified_at=datetime.now(tz=timezone.utc),
        noga_path=noga_path or None,
        noga_path_labels=noga_path_labels or None,
    )
