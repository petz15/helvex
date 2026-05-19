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
    peak_code: str | None = None   # best-match code before constrained descent
    peak_label: str | None = None  # label for peak_code

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
# Core hierarchical classification (v2: global search → peak → descent)
# ---------------------------------------------------------------------------

# Confidence threshold below which embedding result is discarded and token fallback runs
_EMB_MIN_CONFIDENCE: float = 0.30


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


def _classify_v2_with_embedding(
    db: Session,
    idx: _NogaIndex,
    stripped_purpose: str,
    lang: str,
) -> NogaClassification | None:
    """Global embedding search → peak → constrained descent.

    Returns None when no candidate reaches _EMB_MIN_CONFIDENCE (triggers token fallback).
    Embed text: stripped purpose only.
    """
    import numpy as np

    query_vec = _embed_query(stripped_purpose)
    if query_vec is None:
        return None

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
        return None

    candidate_codes = [r.noga_code for r in rows]
    excl_sims = _excludes_cosine_penalty(db, candidate_codes, query_vec, lang)

    # Find peak: highest penalized similarity
    best_penalized = 0.0
    peak_row = None
    for r in rows:
        sim = float(r.similarity)
        excl = excl_sims.get(r.noga_code, 0.0)
        penalized = sim * (1.0 - _EXCL_COSINE_WEIGHT * excl)
        if penalized > best_penalized:
            best_penalized = penalized
            peak_row = r

    if best_penalized < _EMB_MIN_CONFIDENCE or peak_row is None:
        return None

    peak_code = peak_row.noga_code
    peak_level_no = int(peak_row.level_no)
    peak_meta = idx.code_meta.get(peak_code, {})
    peak_label = _get_label(peak_meta, lang)
    peak_sim = float(peak_row.similarity)

    # Build ancestor chain (root → peak) for level_results above the peak
    parent_map = _load_parent_map()
    chain: list[str] = []
    cur = peak_code
    visited: set[str] = set()
    while cur and cur not in visited:
        chain.append(cur)
        visited.add(cur)
        cur = parent_map.get(cur, "")
    chain.reverse()  # now root (L1) → peak

    level_results: list[LevelResult] = []
    for code in chain:
        meta = idx.code_meta.get(code, {})
        label = _get_label(meta, lang)
        level_results.append(LevelResult(code=code, label=label, confidence=peak_sim))

    # Constrained descent from peak+1 to L5
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
        winner_label = _get_label(winner_meta, lang)
        level_results.append(LevelResult(code=winner_code, label=winner_label, confidence=float(winner_sim)))
        current_parent_codes = [winner_code]

    if not level_results:
        return None

    final = level_results[-1]
    return NogaClassification(
        code=final.code,
        label=final.label,
        level="Art",
        confidence=final.confidence,
        level_results=tuple(level_results),
        peak_code=peak_code,
        peak_label=peak_label,
    )


def _classify_v2_token_fallback(
    idx: _NogaIndex,
    company_name: str | None,
    stripped_purpose: str,
    lang: str,
) -> NogaClassification | None:
    """Token-only L1→L5 descent. Used when embedding confidence is below threshold."""
    texts: list[str] = []
    if company_name:
        texts.append(company_name)
    if stripped_purpose:
        texts.append(stripped_purpose)
    tokens = _tokens_from_texts(texts)
    if not tokens:
        return None

    token_scores: dict[str, float] = {}
    for tok in tokens:
        for code in idx.token_to_codes.get(tok, set()):
            token_scores[code] = token_scores.get(code, 0.0) + 1.0
    if not token_scores:
        return None

    max_tok = max(token_scores.values())
    norm_token: dict[str, float] = {c: s / max_tok for c, s in token_scores.items()}
    total_tok = sum(token_scores.values())

    level_results: list[LevelResult] = []
    current_parent_codes: list[str] | None = None

    for level_no in range(1, 6):
        candidates: dict[str, float] = {}
        for code, score in norm_token.items():
            meta = idx.code_meta.get(code, {})
            if meta.get("level_no") != level_no:
                continue
            if current_parent_codes and meta.get("parentCode") not in current_parent_codes:
                continue
            candidates[code] = score

        if not candidates:
            if level_results:
                prev_code = level_results[-1].code
                for code, meta in idx.code_meta.items():
                    if meta.get("parentCode") == prev_code and meta.get("level_no") == level_no:
                        label = _get_label(meta, lang)
                        level_results.append(LevelResult(code=code, label=label, confidence=0.0))
                        current_parent_codes = [code]
                        break
                else:
                    return None
            else:
                return None
            continue

        winner = max(candidates, key=lambda c: candidates[c])
        meta = idx.code_meta.get(winner, {})
        label = _get_label(meta, lang)
        confidence = token_scores.get(winner, 0.0) / total_tok if total_tok > 0 else 0.0
        level_results.append(LevelResult(code=winner, label=label, confidence=confidence))
        current_parent_codes = [winner]

    if not level_results:
        return None

    final = level_results[-1]
    return NogaClassification(
        code=final.code,
        label=final.label,
        level="Art",
        confidence=final.confidence,
        level_results=tuple(level_results),
    )


def classify_company_noga(db: Session, company: Company) -> NogaClassification | None:
    """V2 NOGA classifier: global embedding search → peak → constrained descent.

    Embed text: stripped purpose only (no purpose_keywords, no tfidf_cluster).
    Token-only fallback runs when no embedding candidate reaches _EMB_MIN_CONFIDENCE.
    """
    from app.services.language_detection import detect_purpose_language

    lang = (
        company.purpose_language
        or detect_purpose_language(company.purpose)
        or _DEFAULT_LANG
    )

    boilerplate_patterns = crud.get_active_boilerplate_patterns(db)
    stripped_purpose = _strip_purpose_boilerplate(company.purpose or "", boilerplate_patterns)

    idx = _load_noga_index()

    if stripped_purpose and _has_noga_embeddings(db):
        result = _classify_v2_with_embedding(db, idx, stripped_purpose, lang)
        if result is not None:
            return result
        logger.debug(
            "Embedding confidence below threshold for company %s, falling back to token classifier",
            company.uid,
        )

    return _classify_v2_token_fallback(idx, company.name, stripped_purpose, lang)


# ---------------------------------------------------------------------------
# V2: global multi-level search → peak → constrained descent (experimental)
# ---------------------------------------------------------------------------

# No depth bonus — the excludes penalty already filters noisy L5 codes.
# A depth bonus large enough to matter (≥0.04) inverts genuinely better L5 matches.
_DEPTH_BONUS_PER_LEVEL: float = 0.0


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

    embed_text = stripped_purpose

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
        noga_peak_code=parent.noga_peak_code,
        noga_peak_label=parent.noga_peak_label,
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
        noga_peak_code=None,
        noga_peak_label=None,
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
        noga_peak_code=result.peak_code,
        noga_peak_label=result.peak_label,
    )
