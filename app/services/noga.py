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

# Hybrid re-rank weights: embedding similarity vs token overlap
_W_EMB: float = 0.6
_W_TOK: float = 0.4

# Number of nearest neighbours to fetch from pgvector before re-ranking
_EMB_TOP_K: int = 50

# Confidence threshold below which a result is considered low-confidence.
# Companies with noga_confidence < this value are candidates for API re-run.
NOGA_LOW_CONFIDENCE: float = 0.80

# Default language when detection returns None (Switzerland is majority German)
_DEFAULT_LANG = "de"


@dataclass(frozen=True)
class NogaClassification:
    code: str
    label: str | None
    level: str | None
    confidence: float


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


# ---------------------------------------------------------------------------
# Hierarchy path building
# ---------------------------------------------------------------------------

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

def _pgvector_search(
    db: Session,
    query_vec,
    lang: str,
    top_k: int = _EMB_TOP_K,
) -> list[tuple[str, float]]:
    """Return [(noga_code, cosine_similarity), ...] ordered by similarity desc.

    Falls back to 'de' embeddings when the requested lang has no rows (e.g.
    the table is not yet fully populated for that language).
    """
    import numpy as np

    vec_list = query_vec.tolist() if isinstance(query_vec, np.ndarray) else list(query_vec)
    vec_str = "[" + ",".join(f"{x:.8f}" for x in vec_list) + "]"

    rows = db.execute(sql_text("""
        WITH q AS (SELECT CAST(:vec AS vector) AS v)
        SELECT noga_code, 1 - (embedding <=> q.v) AS similarity
        FROM   noga_embeddings, q
        WHERE  lang = :lang
        ORDER  BY embedding <=> q.v
        LIMIT  :k
    """), {"vec": vec_str, "lang": lang, "k": top_k}).fetchall()

    if not rows and lang != _DEFAULT_LANG:
        return _pgvector_search(db, query_vec, _DEFAULT_LANG, top_k)

    return [(r.noga_code, float(r.similarity)) for r in rows]


_noga_embeddings_present: bool | None = None


def _has_noga_embeddings(db: Session) -> bool:
    global _noga_embeddings_present
    if _noga_embeddings_present:  # only skip the DB check once confirmed present
        return True
    try:
        row = db.execute(sql_text("SELECT 1 FROM noga_embeddings LIMIT 1")).fetchone()
        _noga_embeddings_present = row is not None
    except Exception:
        _noga_embeddings_present = False
    return bool(_noga_embeddings_present)


# ---------------------------------------------------------------------------
# Core classification
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


def _pick_best_code(scores: dict[str, float]) -> str:
    """Return the highest-scoring code, preferring leaf codes (len 6 > 4 > 3 > 2 > 1)."""
    return max(
        scores,
        key=lambda c: (
            scores[c],
            len(c) == 6, len(c) == 4, len(c) == 3, len(c) == 2, len(c) == 1, c,
        ),
    )


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


def classify_company_noga(db: Session, company: Company) -> NogaClassification | None:
    # --- Language detection ---
    from app.services.language_detection import detect_purpose_language
    lang = (
        company.purpose_language
        or detect_purpose_language(company.purpose)
        or _DEFAULT_LANG
    )

    # --- Token scores (always computed, used for hybrid re-rank) ---
    tokens = _company_tokens(db, company)
    if not tokens:
        return None

    idx = _load_noga_index()

    token_scores: dict[str, float] = {}
    for tok in tokens:
        for code in idx.token_to_codes.get(tok, set()):
            token_scores[code] = token_scores.get(code, 0.0) + 1.0

    max_tok = max(token_scores.values()) if token_scores else 1.0
    norm_token: dict[str, float] = {c: s / max_tok for c, s in token_scores.items()}

    # --- Embedding re-rank via pgvector ---
    embed_text = " ".join(filter(None, [
        company.purpose or "",
        (company.purpose_keywords or "").replace(",", " "),
    ])).strip()

    best_code: str
    confidence: float

    use_embeddings = bool(embed_text) and _has_noga_embeddings(db)

    # If there is no token overlap with the NOGA taxonomy AND embeddings are
    # unavailable, we genuinely cannot classify this company.
    if not token_scores and not use_embeddings:
        return None

    if use_embeddings:
        query_vec = _embed_query(embed_text)
        if query_vec is not None:
            emb_results = _pgvector_search(db, query_vec, lang)

            # Hybrid re-rank: weighted sum of embedding sim + token overlap.
            # When token_scores is empty this degrades gracefully to pure-embedding.
            hybrid: dict[str, float] = {}
            for code, sim in emb_results:
                hybrid[code] = _W_EMB * sim + _W_TOK * norm_token.get(code, 0.0)
            # Include token-only candidates not in embedding top-K
            for code, tok_score in norm_token.items():
                if code not in hybrid:
                    hybrid[code] = _W_TOK * tok_score

            if not hybrid:
                return None

            best_code = _pick_best_code(hybrid)

            # Confidence = raw cosine similarity of winning code from pgvector
            best_sim = next((s for c, s in emb_results if c == best_code), None)
            if best_sim is None:
                # Winning code came from token-only path; use token proportion
                total = sum(token_scores.values())
                best_sim = token_scores.get(best_code, 0.0) / total if total > 0 else 0.0
            confidence = max(0.0, min(1.0, best_sim))
        else:
            # Embedding failed; token-only fallback
            if not token_scores:
                return None
            best_code = _pick_best_code(norm_token)
            total = sum(token_scores.values())
            confidence = token_scores.get(best_code, 0.0) / total if total > 0 else 0.0
    else:
        # No embeddings available (table empty) — token-only
        best_code = _pick_best_code(token_scores)
        total = sum(token_scores.values())
        confidence = float(token_scores.get(best_code, 0.0) / total) if total > 0 else 0.0

    meta = idx.code_meta.get(best_code, {})
    name = meta.get("name")
    label = None
    if isinstance(name, dict):
        label = (
            name.get(lang)
            or name.get("de")
            or name.get("fr")
            or name.get("it")
            or name.get("en")
        )
    elif isinstance(name, str):
        label = name

    level = meta.get("level") if isinstance(meta.get("level"), str) else None
    return NogaClassification(code=best_code, label=label, level=level, confidence=confidence)


# ---------------------------------------------------------------------------
# Branch office helpers
# ---------------------------------------------------------------------------

_BRANCH_KEYWORDS = ("zweigniederlassung", "succursale", "filiale di")


def is_branch_office(company: Company) -> bool:
    name_lower = (company.name or "").lower()
    purpose_lower = (company.purpose or "").lower()
    return any(k in name_lower or k in purpose_lower for k in _BRANCH_KEYWORDS)


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
        noga_classified_at=datetime.now(tz=timezone.utc),
        noga_path=noga_path or None,
        noga_path_labels=noga_path_labels or None,
    )
