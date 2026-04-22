"""Multilingual sentence embedding service.

Uses paraphrase-multilingual-mpnet-base-v2 (or a configurable model) to produce
768-dim normalized embeddings for Swiss company purpose texts in DE/FR/IT/EN.

This module is the shared backbone for:
  - NOGA classification (nearest-neighbour over embedded NOGA descriptions)
  - Semantic search across clusters/categories/NOGA
  - BERTopic-style clustering (embedding → UMAP → HDBSCAN)
  - Incremental cluster assignment for newly imported companies

Design principles:
  - Lazy model loading: first call triggers download (~420 MB), cached for lifetime
  - Batch-aware: encode() processes texts in chunks to respect GPU/CPU memory
  - Normalized: all embeddings returned as L2-normalized float32 (dot product = cosine sim)
  - Stateless: no DB dependencies; callers supply raw strings
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Default model: 768-dim multilingual, handles DE/FR/IT/EN natively.
# Smaller alternative: paraphrase-multilingual-MiniLM-L12-v2 (384-dim, faster)
DEFAULT_MODEL = "paraphrase-multilingual-mpnet-base-v2"


def _prepare_torch_runtime_env() -> None:
    """Set deterministic torch cache paths for minimal containers.

    Some runtime images run as an arbitrary UID without a passwd entry.
    In that case, torch may fail while resolving cache directories via
    getpass/pwd. Predefining cache paths avoids that code path.
    """
    cache_root = Path(os.getenv("HELVEX_TORCH_CACHE_DIR", "/tmp/helvex-torch-cache"))
    inductor_dir = cache_root / "inductor"
    hf_home = cache_root / "hf"
    for p in (cache_root, inductor_dir, hf_home):
        p.mkdir(parents=True, exist_ok=True)

    os.environ.setdefault("TORCHINDUCTOR_CACHE_DIR", str(inductor_dir))
    os.environ.setdefault("TRITON_CACHE_DIR", str(cache_root / "triton"))
    os.environ.setdefault("HF_HOME", str(hf_home))
    os.environ.setdefault("XDG_CACHE_HOME", str(cache_root))
    os.environ.setdefault("USER", "helvex")
    os.environ.setdefault("LOGNAME", os.environ["USER"])


@lru_cache(maxsize=1)
def _get_model(model_name: str = DEFAULT_MODEL):
    """Load and cache the sentence-transformers model (lazy, one download)."""
    try:
        _prepare_torch_runtime_env()
        from sentence_transformers import SentenceTransformer
        logger.info("Loading sentence-transformers model: %s", model_name)
        model = SentenceTransformer(model_name)
        logger.info("Model loaded: %s", model_name)
        return model
    except ImportError as exc:
        raise ImportError(
            "sentence-transformers is required. Install with: pip install sentence-transformers"
        ) from exc


def embed_texts(
    texts: list[str],
    *,
    model_name: str = DEFAULT_MODEL,
    batch_size: int = 256,
    show_progress: bool = False,
) -> np.ndarray:
    """Embed a list of texts and return a normalized float32 matrix of shape (N, D).

    Empty strings are embedded as zero vectors (excluded from nearest-neighbour search
    by callers using dot-product threshold > 0).
    """
    if not texts:
        return np.zeros((0, _embedding_dim(model_name)), dtype=np.float32)

    model = _get_model(model_name)
    embeddings = model.encode(
        texts,
        batch_size=batch_size,
        normalize_embeddings=True,
        show_progress_bar=show_progress,
        convert_to_numpy=True,
    )
    return embeddings.astype(np.float32)


def embed_single(text: str, *, model_name: str = DEFAULT_MODEL) -> np.ndarray:
    """Embed a single string; return shape (D,) float32."""
    if not text or not text.strip():
        return np.zeros(_embedding_dim(model_name), dtype=np.float32)
    result = embed_texts([text], model_name=model_name)
    return result[0]


def _embedding_dim(model_name: str) -> int:
    """Return the embedding dimension without loading the full model."""
    known = {
        "paraphrase-multilingual-mpnet-base-v2": 768,
        "paraphrase-multilingual-MiniLM-L12-v2": 384,
        "intfloat/multilingual-e5-base": 768,
        "intfloat/multilingual-e5-large": 1024,
    }
    return known.get(model_name, 768)


def build_company_text(company) -> str:
    """Build a single representative text for embedding from a company ORM object.

    Combines purpose text with purpose_keywords for best coverage. The keywords
    are already clean (lemmatized, boilerplate-stripped), so they anchor the
    embedding toward the business activity rather than legal phrasing.
    """
    parts: list[str] = []
    if company.purpose:
        parts.append(company.purpose.strip())
    if company.purpose_keywords:
        kws = company.purpose_keywords.replace(",", " ").strip()
        if kws and kws not in (parts[0] if parts else ""):
            parts.append(kws)
    return " ".join(parts)


def nearest_neighbours(
    query_vec: np.ndarray,
    index_matrix: np.ndarray,
    *,
    top_k: int = 10,
) -> list[tuple[int, float]]:
    """Return top-k (index, cosine_similarity) pairs from a normalized index matrix.

    Both query_vec and index_matrix must be L2-normalized.
    """
    sims: np.ndarray = index_matrix @ query_vec  # shape (N,)
    if top_k >= len(sims):
        order = np.argsort(sims)[::-1]
    else:
        part = np.argpartition(sims, -top_k)[-top_k:]
        order = part[np.argsort(sims[part])[::-1]]
    return [(int(i), float(sims[i])) for i in order]
