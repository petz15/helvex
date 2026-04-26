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
            if tok:
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
    """Download embedding artifacts from S3 and return them, or None if unavailable.

    Refuses to return a matrix whose dimension does not match the query model's
    dimension; a stale artifact built with a different model would otherwise
    produce silently invalid cosine similarities.
    """
    try:
        from app.services import s3_client
        if not s3_client.is_models_bucket_configured():
            return None
        import numpy as np
        from app.services.embeddings import DEFAULT_MODEL, _embedding_dim
        ids_bytes = s3_client.download_model_bytes(_S3_IDS_KEY)
        ids_payload = json.loads(ids_bytes.decode("utf-8"))
        codes: list[str] = ids_payload["codes"]
        shape = ids_payload["shape"]
        emb_bytes = s3_client.download_model_bytes(_S3_EMBEDDINGS_KEY)
        matrix = np.frombuffer(emb_bytes, dtype="float32").reshape(shape["rows"], shape["cols"])

        expected_dim = _embedding_dim(DEFAULT_MODEL)
        if matrix.shape[1] != expected_dim:
            logger.error(
                "NOGA embeddings dim mismatch: artifact has %d, query model %s expects %d. "
                "Rebuild via scripts/build_noga_embeddings.py. Falling back to token-only.",
                matrix.shape[1], DEFAULT_MODEL, expected_dim,
            )
            return None

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


# Below this threshold the classification is rendered as "low confidence" in
# the UI (muted style). It is no longer a hard cutoff — the best guess plus
# its confidence is always stored so users can see something rather than a
# silent NULL.
_NOGA_LOW_CONFIDENCE: float = 0.50


def _pick_best_code(
    scores: dict[str, float],
    section_filter: str | None = None,
) -> str:
    """Return the highest-scoring code, preferring leaf codes (len 6 > 4 > 3 > 2 > 1).

    If section_filter is provided, only consider codes whose noga_path starts with that section.
    """
    candidates = scores
    if section_filter:
        idx = _load_noga_index()
        parent_map = _load_parent_map()
        filtered: dict[str, float] = {}
        for code, score in scores.items():
            # Walk ancestry to find section letter
            cur = code
            visited: set[str] = set()
            while cur and cur not in visited:
                visited.add(cur)
                parent = parent_map.get(cur, "")
                if not parent:
                    break
                cur = parent
            section = list(visited)[-1] if visited else code
            if section.upper() == section_filter.upper():
                filtered[code] = score
        if filtered:
            candidates = filtered

    return max(
        candidates,
        key=lambda c: (
            candidates[c],
            len(c) == 6, len(c) == 4, len(c) == 3, len(c) == 2, len(c) == 1, c,
        ),
    )


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

    # --- Embedding re-rank with 2-stage classification ---
    emb_data = _load_noga_embeddings()
    embed_text = " ".join(filter(None, [
        company.purpose or "",
        (company.purpose_keywords or "").replace(",", " "),
    ])).strip()

    best_code: str
    confidence: float

    if emb_data is not None and embed_text:
        query_vec = _embed_query(embed_text)
        if query_vec is not None:
            import numpy as np
            sims: np.ndarray = emb_data.matrix @ query_vec  # shape (N,)
            top_k_idx = np.argpartition(sims, -_EMB_TOP_K)[-_EMB_TOP_K:]

            # --- Stage 1: vote for NOGA section (1-letter code) from top-K ---
            parent_map = _load_parent_map()
            section_votes: dict[str, float] = {}
            for i in top_k_idx:
                code = emb_data.codes[i]
                emb_sim = float(sims[i])
                # Walk to root to find section
                cur = code
                visited: set[str] = set()
                while cur and cur not in visited:
                    visited.add(cur)
                    parent = parent_map.get(cur, "")
                    if not parent:
                        break
                    cur = parent
                section = cur if (not parent_map.get(cur, "")) else code
                # The section is the root-level ancestor
                root = code
                prev = code
                c2 = code
                v2: set[str] = set()
                while c2 and c2 not in v2:
                    v2.add(c2)
                    p2 = parent_map.get(c2, "")
                    if not p2:
                        root = c2
                        break
                    prev = c2
                    c2 = p2
                section_votes[root] = section_votes.get(root, 0.0) + emb_sim

            best_section = max(section_votes, key=lambda s: section_votes[s]) if section_votes else None

            # --- Stage 2: re-rank within winning section ---
            hybrid_scores: dict[str, float] = {}
            for i in top_k_idx:
                c = emb_data.codes[i]
                emb_sim = float(sims[i])
                tok_sim = norm_token.get(c, 0.0)
                hybrid_scores[c] = _W_EMB * emb_sim + _W_TOK * tok_sim

            for c, s in norm_token.items():
                if c not in hybrid_scores:
                    hybrid_scores[c] = _W_TOK * s

            # Try best code within winning section first, fall back to global best
            if best_section:
                section_hybrid: dict[str, float] = {}
                for code, score in hybrid_scores.items():
                    cur = code
                    v3: set[str] = set()
                    root3 = code
                    while cur and cur not in v3:
                        v3.add(cur)
                        p3 = parent_map.get(cur, "")
                        if not p3:
                            root3 = cur
                            break
                        cur = p3
                    if root3 == best_section:
                        section_hybrid[code] = score
                final_scores = section_hybrid if section_hybrid else hybrid_scores
            else:
                final_scores = hybrid_scores

            best_code = _pick_best_code(final_scores)
            try:
                best_emb_sim = float(emb_data.matrix[emb_data.codes.index(best_code)] @ query_vec)
            except (ValueError, IndexError):
                best_emb_sim = final_scores[best_code]
            confidence = max(0.0, min(1.0, best_emb_sim))
        else:
            # Embedding failed; token-only
            best_code = _pick_best_code(norm_token)
            total = sum(token_scores.values())
            confidence = token_scores.get(best_code, 0.0) / total if total > 0 else 0.0
    else:
        # No embeddings / no text
        best_code = _pick_best_code(token_scores)
        total = sum(token_scores.values())
        confidence = float(token_scores.get(best_code, 0.0) / total) if total > 0 else 0.0

    # Always return the best guess; the UI renders below-threshold results as
    # "low confidence" rather than hiding them.
    meta = idx.code_meta.get(best_code, {})
    name = meta.get("name")
    label = None
    if isinstance(name, dict):
        label = name.get("de") or name.get("fr") or name.get("it") or name.get("en")
    elif isinstance(name, str):
        label = name

    level = meta.get("level") if isinstance(meta.get("level"), str) else None
    return NogaClassification(code=best_code, label=label, level=level, confidence=confidence)


_BRANCH_KEYWORDS = ("zweigniederlassung", "succursale", "filiale di")


def is_branch_office(company: Company) -> bool:
    """Detect Swiss branch offices (Zweigniederlassung / succursale / filiale).

    Branch entries replicate the parent's purpose text but get assigned NOGA
    based on it, which gives the *right* code only by accident. We treat them
    as a special case so they inherit from the parent or remain unclassified
    rather than producing a misleading classification.
    """
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
    """Return an update that wipes any stale NOGA fields."""
    return CompanyUpdate(
        noga_code=None,
        noga_label=None,
        noga_level=None,
        noga_confidence=None,
        noga_classified_at=datetime.now(tz=timezone.utc),
        noga_path=None,
        noga_path_labels=None,
    )


def apply_noga_classification(db: Session, company: Company) -> CompanyUpdate | None:
    # Branches: inherit parent's NOGA when possible, otherwise clear stale data
    # rather than producing a wrong classification from boilerplate purpose text.
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
