from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any
from sqlalchemy.orm import Session

from app import crud
from app.models.company import Company
from app.schemas.company import CompanyUpdate

logger = logging.getLogger(__name__)

_WORD_RE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9]{3,}")
_SENTENCE_SPLIT = re.compile(r"(?<=\.)\s+(?=[A-ZÄÖÜ])")

# Compact stopword set for classification token quality.
_NOGA_STOPWORDS: set[str] = {
    "die", "der", "das", "und", "oder", "mit", "von", "für", "des", "dem", "den",
    "ein", "eine", "einer", "eines", "sich", "auf", "zu", "ist", "sowie", "als",
    "auch", "nicht", "nach", "bei", "alle", "durch", "wird", "im", "an", "am",
    "company", "companies", "services", "general", "related", "activities",
    "gesellschaft", "gesellschaften", "zweck", "bezweckt", "dienstleistungen", "dienstleistung",
    "erbringung", "tätigkeit", "tätigkeiten", "handel", "waren", "art", "insbesondere",
}

# S3 keys for embedding artifacts (written by scripts/build_noga_embeddings.py)
_S3_EMBEDDINGS_KEY = "models/noga_embeddings.npy"
_S3_IDS_KEY = "models/noga_embedding_ids.json"

# Weights for the hybrid re-rank
_W_EMB: float = 0.6
_W_TOK: float = 0.4
_EMB_TOP_K: int = 50  # cosine candidates before token re-rank


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
            if not tok or tok in _NOGA_STOPWORDS:
                continue
            tokens.add(tok)
    return tokens


def _strip_purpose_boilerplate(text: str, patterns: list[re.Pattern]) -> str:
    if not text or len(text) < 40 or not patterns:
        return text

    sentences = _SENTENCE_SPLIT.split(text.strip())
    kept = [
        s for s in sentences
        if s.strip() and not any(pat.search(s) for pat in patterns)
    ]
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
# Embedding helpers (Phase 1a)
# ---------------------------------------------------------------------------

@dataclass
class _NogaEmbeddings:
    """Loaded embedding matrix + ordered code list."""
    matrix: Any  # np.ndarray, shape (N, D), normalized float32
    codes: list[str]


@lru_cache(maxsize=1)
def _load_noga_embeddings() -> _NogaEmbeddings | None:
    """Download embedding artifacts from S3 and return them, or None if unavailable."""
    try:
        from app.services import s3_client
        if not s3_client.is_models_bucket_configured():
            return None
        import numpy as np
        ids_bytes = s3_client.download_model_bytes(_S3_IDS_KEY)
        ids_payload = json.loads(ids_bytes.decode("utf-8"))
        codes: list[str] = ids_payload["codes"]
        shape = ids_payload["shape"]
        emb_bytes = s3_client.download_model_bytes(_S3_EMBEDDINGS_KEY)
        matrix = np.frombuffer(emb_bytes, dtype="float32").reshape(shape["rows"], shape["cols"])
        logger.info("NOGA embeddings loaded from S3: %s codes, shape %s", len(codes), matrix.shape)
        return _NogaEmbeddings(matrix=matrix, codes=codes)
    except Exception as exc:
        logger.warning("Could not load NOGA embeddings from S3 (%s); falling back to token-only.", exc)
        return None


def _embed_query(text: str):
    """Return a normalized float32 embedding vector for the given text, or None.

    Delegates to the shared multilingual embedding service so the same model
    is reused across NOGA classification, semantic search, and clustering.
    """
    try:
        from app.services.embeddings import embed_single
        vec = embed_single(text)
        import numpy as np
        if np.linalg.norm(vec) < 1e-6:
            return None
        return vec
    except Exception as exc:
        logger.warning("Embedding query failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Hierarchy path building (Phase 1b)
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _load_parent_map() -> dict[str, str]:
    """Return {child_code: parent_code} from noga_lookup.json."""
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

    # Collect ancestry bottom-up then reverse
    chain: list[str] = []
    cur = str(code)
    visited: set[str] = set()
    while cur and cur not in visited:
        chain.append(cur)
        visited.add(cur)
        cur = parent_map.get(cur, "")
    chain.reverse()  # root → leaf

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


def classify_company_noga(db: Session, company: Company) -> NogaClassification | None:
    tokens = _company_tokens(db, company)
    if not tokens:
        return None

    idx = _load_noga_index()

    # --- Token scores (always computed) ---
    token_scores: dict[str, float] = {}
    for tok in tokens:
        for code in idx.token_to_codes.get(tok, set()):
            token_scores[code] = token_scores.get(code, 0.0) + 1.0

    if not token_scores:
        return None

    # Normalise token scores to [0, 1]
    max_tok = max(token_scores.values())
    norm_token: dict[str, float] = {c: s / max_tok for c, s in token_scores.items()}

    # --- Embedding re-rank (Phase 1a) ---
    # Use full purpose text + keywords for richer embedding coverage.
    # Keywords alone miss context; purpose alone includes boilerplate.
    emb_data = _load_noga_embeddings()
    embed_text = " ".join(filter(None, [
        company.purpose or "",
        (company.purpose_keywords or "").replace(",", " "),
    ])).strip()
    if emb_data is not None and embed_text:
        query_vec = _embed_query(embed_text)
        if query_vec is not None:
            import numpy as np
            # Cosine similarity (matrix is already normalised, query is normalised)
            sims: np.ndarray = emb_data.matrix @ query_vec  # shape (N,)
            top_k_idx = np.argpartition(sims, -_EMB_TOP_K)[-_EMB_TOP_K:]

            # Hybrid score only over top-K embedding candidates
            hybrid_scores: dict[str, float] = {}
            for i in top_k_idx:
                c = emb_data.codes[i]
                emb_sim = float(sims[i])
                tok_sim = norm_token.get(c, 0.0)
                hybrid_scores[c] = _W_EMB * emb_sim + _W_TOK * tok_sim

            # For codes not in top-K, fall back to token-only score (scaled down)
            for c, s in norm_token.items():
                if c not in hybrid_scores:
                    hybrid_scores[c] = _W_TOK * s

            final_scores = hybrid_scores
            # noga_confidence = embedding similarity of best result
            best_code = max(
                final_scores,
                key=lambda c: (
                    final_scores[c],
                    len(c) == 6, len(c) == 4, len(c) == 3, len(c) == 2, len(c) == 1, c,
                ),
            )
            # Find embedding sim for best code
            try:
                best_emb_sim = float(emb_data.matrix[emb_data.codes.index(best_code)] @ query_vec)
            except (ValueError, IndexError):
                best_emb_sim = final_scores[best_code]
            confidence = max(0.0, min(1.0, best_emb_sim))
        else:
            # Embedding failed; fall through to token-only
            final_scores = norm_token
            best_code = max(
                final_scores,
                key=lambda c: (final_scores[c], len(c) == 6, len(c) == 4, len(c) == 3, len(c) == 2, len(c) == 1, c),
            )
            total = sum(token_scores.values())
            confidence = token_scores.get(best_code, 0.0) / total if total > 0 else 0.0
    else:
        # Token-only path (no embeddings available / no keywords)
        ranked = sorted(
            token_scores.items(),
            key=lambda kv: (kv[1], len(kv[0]) == 6, len(kv[0]) == 4, len(kv[0]) == 3, len(kv[0]) == 2, len(kv[0]) == 1, kv[0]),
            reverse=True,
        )
        best_code, best_score = ranked[0]
        total = sum(token_scores.values())
        confidence = float(best_score / total) if total > 0 else 0.0

    meta = idx.code_meta.get(best_code, {})
    name = meta.get("name")
    label = None
    if isinstance(name, dict):
        label = name.get("de") or name.get("fr") or name.get("it") or name.get("en")
    elif isinstance(name, str):
        label = name

    level = meta.get("level") if isinstance(meta.get("level"), str) else None
    return NogaClassification(code=best_code, label=label, level=level, confidence=confidence)


def apply_noga_classification(db: Session, company: Company) -> CompanyUpdate | None:
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
