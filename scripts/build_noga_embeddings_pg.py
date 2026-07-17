"""Build and store hierarchical NOGA embeddings (all 5 levels, INCLUDES + EXCLUDES).

Reads noga_level_1.json through noga_level_5.json, builds separate embeddings for:
  - 'includes': code + name + INCLUDES text + INCLUDES_ALSO text (positive signal)
  - 'excludes': code + EXCLUDES text (negative signal, only if text exists)

Stores rows in noga_embeddings(noga_code, lang, level_no, parent_code, ann_type, embedding)
via UPSERT keyed on (noga_code, lang, ann_type).

Usage:
    python -m scripts.build_noga_embeddings_pg [--batch-size 256] [--dry-run]

Prerequisites:
    - alembic upgrade head (migration 0079 must have run)
    - DB connection configured in .env
    - sentence-transformers installed
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Callable

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

LANGS = ["de", "fr", "it", "en"]
REPO_ROOT = Path(__file__).resolve().parents[1]

# 5 level files, in hierarchy order
LEVEL_FILES = [
    (REPO_ROOT / "noga_level_1.json", 1),
    (REPO_ROOT / "noga_level_2.json", 2),
    (REPO_ROOT / "noga_level_3.json", 3),
    (REPO_ROOT / "noga_level_4.json", 4),
    (REPO_ROOT / "noga_level_5.json", 5),
]

# Annotation types used for INCLUDES embeddings
_POSITIVE_ANN_TYPES = {"INCLUDES", "INCLUDES_ALSO"}

# Cap text length for embedding (model context window is 512 tokens)
_MAX_TEXT_CHARS = 1500


def _build_includes_text(entry: dict, lang: str) -> str:
    """Concatenate code + name + INCLUDES + INCLUDES_ALSO annotations (positive signal)."""
    code = entry["code"]

    name_map = entry.get("name") or {}
    name = name_map.get(lang) or name_map.get("de") or ""

    ann_texts: list[str] = []
    for ann in entry.get("annotations") or []:
        if not isinstance(ann, dict):
            continue
        if ann.get("type") not in _POSITIVE_ANN_TYPES:
            continue
        text_map = ann.get("text") or {}
        candidate = text_map.get(lang) or text_map.get("de") or ""
        if candidate:
            ann_texts.append(candidate)

    parts = [p for p in [code, name] + ann_texts if p]
    text = " ".join(parts)
    return text[:_MAX_TEXT_CHARS]


def _build_excludes_text(entry: dict, lang: str) -> str | None:
    """Extract EXCLUDES annotation text (negative signal). Returns None if no EXCLUDES text."""
    code = entry["code"]

    for ann in entry.get("annotations") or []:
        if not isinstance(ann, dict):
            continue
        if ann.get("type") != "EXCLUDES":
            continue
        text_map = ann.get("text") or {}
        candidate = text_map.get(lang) or text_map.get("de") or ""
        if candidate:
            parts = [code, candidate]
            text = " ".join(parts)
            return text[:_MAX_TEXT_CHARS]

    return None


def _load_all_entries() -> list[dict]:
    """Load and merge all 5 level files."""
    all_entries = []
    for path, level_no in LEVEL_FILES:
        if not path.exists():
            logger.warning("NOGA level file not found: %s", path)
            continue
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            logger.error("Expected list in %s, got %s", path, type(data))
            continue
        for entry in data:
            entry["_level_no"] = level_no  # Add level number for later reference
        all_entries.extend(data)
    logger.info("Loaded %d entries across all %d levels", len(all_entries), len(LEVEL_FILES))
    return all_entries


def run(
    batch_size: int = 256,
    dry_run: bool = False,
    progress_cb: Callable[[int, int, dict], None] | None = None,
) -> None:
    """Build and upsert NOGA embeddings (INCLUDES + EXCLUDES) to PostgreSQL.

    Args:
        batch_size: Number of texts to embed in one batch (balances memory vs efficiency)
        dry_run: If True, skip embedding and DB write; just show samples
        progress_cb: Optional callback(done, total, stats) for job progress reporting
    """
    entries = _load_all_entries()
    if not entries:
        logger.error("No NOGA entries loaded")
        sys.exit(1)

    # Build (code, lang, level_no, parent_code, ann_type, text) tuples
    tuples: list[tuple[str, str, int, str | None, str, str]] = []

    for entry in entries:
        code = str(entry.get("code", "")).strip()
        level_no = entry.get("_level_no")
        parent_code = entry.get("parentCode")  # None for level-1
        if not code or level_no is None:
            continue

        # Always build INCLUDES embedding for every code
        for lang in LANGS:
            inc_text = _build_includes_text(entry, lang)
            if inc_text:
                tuples.append((code, lang, level_no, parent_code, "includes", inc_text))

        # Optionally build EXCLUDES embedding (only if text exists)
        for lang in LANGS:
            excl_text = _build_excludes_text(entry, lang)
            if excl_text:
                tuples.append((code, lang, level_no, parent_code, "excludes", excl_text))

    logger.info("Built %d embedding specs (code, lang, ann_type tuples)", len(tuples))

    if dry_run:
        logger.info("Dry run — skipping embedding and DB write")
        for code, lang, level_no, parent_code, ann_type, text in tuples[:5]:
            logger.info(
                "Sample [%s/%s/%s]: %s...",
                code, lang, ann_type, text[:100]
            )
        return

    # Lazy imports so script can be imported without GPU/DB deps
    import numpy as np
    from tqdm import tqdm

    from app.services.ml.embeddings import DEFAULT_MODEL, embed_texts

    # Extract just the texts in the same order as tuples
    texts = [t[-1] for t in tuples]
    logger.info(
        "Embedding %d texts with model %s (batch_size=%d)...",
        len(texts), DEFAULT_MODEL, batch_size
    )

    embeddings: np.ndarray = embed_texts(texts, batch_size=batch_size, show_progress=True)
    logger.info("Embedding complete, shape: %s", embeddings.shape)

    # Prepare upsert parameters
    from sqlalchemy import text as sql_text

    from app.database import SessionLocal

    _UPSERT = sql_text("""
        INSERT INTO noga_embeddings (noga_code, lang, level_no, parent_code, ann_type, embedding)
        VALUES (:code, :lang, :level_no, :parent_code, :ann_type, CAST(:vec AS vector))
        ON CONFLICT (noga_code, lang, ann_type)
        DO UPDATE SET
            embedding   = EXCLUDED.embedding,
            level_no    = EXCLUDED.level_no,
            parent_code = EXCLUDED.parent_code
    """)

    params = [
        {
            "code": code,
            "lang": lang,
            "level_no": level_no,
            "parent_code": parent_code,
            "ann_type": ann_type,
            "vec": "[" + ",".join(f"{x:.8f}" for x in embeddings[i].tolist()) + "]",
        }
        for i, (code, lang, level_no, parent_code, ann_type, _) in enumerate(tuples)
    ]

    # Write to DB in chunks
    _CHUNK = 500
    upserted = 0
    errors = 0

    with SessionLocal() as db:
        for start in tqdm(range(0, len(params), _CHUNK), desc="Upserting"):
            chunk = params[start : start + _CHUNK]
            try:
                db.execute(_UPSERT, chunk)
                db.commit()
                upserted += len(chunk)
                if progress_cb:
                    progress_cb(upserted, len(params), {"chunk": start // _CHUNK})
            except Exception as exc:
                db.rollback()
                logger.error("Chunk %d–%d failed: %s", start, start + len(chunk), exc)
                errors += len(chunk)

    logger.info("Done. Upserted: %d, Errors: %d", upserted, errors)
    if errors:
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build hierarchical NOGA pgvector embeddings")
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run(batch_size=args.batch_size, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
