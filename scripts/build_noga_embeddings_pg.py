"""One-time script: embed all NOGA level-5 categories and store in PostgreSQL.

Reads noga_level_5_clean.json, builds one embedding per (code, lang) pair
using the same multilingual sentence-transformers model used for classification,
and UPSERTs rows into the noga_embeddings table.

Usage:
    python -m scripts.build_noga_embeddings_pg [--batch-size 256] [--dry-run]

Prerequisites:
    - alembic upgrade head  (migration 0069 must have run)
    - DB connection configured in .env
    - sentence-transformers installed (pip install sentence-transformers)
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

LANGS = ["de", "fr", "it", "en"]
REPO_ROOT = Path(__file__).resolve().parents[1]
NOGA_FILE = REPO_ROOT / "noga_level_5_clean.json"

# Cap text length — annotations can be very long; the model context window
# is 512 tokens, so truncating at 1500 chars is safe and avoids wasted compute.
_MAX_TEXT_CHARS = 1500


def _build_noga_text(entry: dict, lang: str) -> str:
    """Concatenate code + name + first INCLUDES annotation for the given language.

    Falls back to 'de' for any missing language, then to the code alone.
    """
    code = entry["code"]

    name_map = entry.get("name") or {}
    name = name_map.get(lang) or name_map.get("de") or ""

    annotation = ""
    for ann in entry.get("annotations") or []:
        if not isinstance(ann, dict):
            continue
        text_map = ann.get("text") or {}
        candidate = text_map.get(lang) or text_map.get("de") or ""
        if candidate:
            annotation = candidate
            break

    parts = [p for p in [code, name, annotation] if p]
    text = " ".join(parts)
    return text[:_MAX_TEXT_CHARS]


def _load_entries() -> list[dict]:
    with NOGA_FILE.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError(f"{NOGA_FILE} must be a JSON array")
    logger.info("Loaded %d NOGA level-5 entries", len(data))
    return data


def run(batch_size: int = 256, dry_run: bool = False) -> None:
    entries = _load_entries()

    # Build (code, lang, text) triples
    triples: list[tuple[str, str, str]] = []
    for entry in entries:
        code = str(entry.get("code", "")).strip()
        if not code:
            continue
        for lang in LANGS:
            text = _build_noga_text(entry, lang)
            if text:
                triples.append((code, lang, text))

    logger.info("Built %d (code, lang) pairs to embed", len(triples))

    if dry_run:
        logger.info("Dry run — skipping embedding and DB write")
        for code, lang, text in triples[:3]:
            logger.info("  Sample [%s/%s]: %s...", code, lang, text[:120])
        return

    # Lazy imports so the script can be imported without GPU/DB available
    import numpy as np
    from tqdm import tqdm

    from app.services.embeddings import DEFAULT_MODEL, embed_texts

    texts = [t for _, _, t in triples]
    logger.info("Embedding %d texts with model %s (batch_size=%d)...", len(texts), DEFAULT_MODEL, batch_size)

    # Embed in one batched call — embed_texts handles chunking internally
    embeddings: np.ndarray = embed_texts(texts, batch_size=batch_size, show_progress=True)
    logger.info("Embedding complete, shape: %s", embeddings.shape)

    # Write to DB in chunks to avoid very large transactions
    from sqlalchemy import text as sql_text

    from app.database import SessionLocal

    _UPSERT = sql_text("""
        INSERT INTO noga_embeddings (noga_code, lang, embedding)
        VALUES (:code, :lang, CAST(:vec AS vector))
        ON CONFLICT (noga_code, lang)
        DO UPDATE SET embedding = EXCLUDED.embedding
    """)
    _CHUNK = 500

    params = [
        {
            "code": code,
            "lang": lang,
            "vec": "[" + ",".join(f"{x:.8f}" for x in embeddings[i].tolist()) + "]",
        }
        for i, (code, lang, _) in enumerate(triples)
    ]

    upserted = 0
    errors = 0

    with SessionLocal() as db:
        for start in tqdm(range(0, len(params), _CHUNK), desc="Upserting"):
            chunk = params[start : start + _CHUNK]
            try:
                db.execute(_UPSERT, chunk)
                db.commit()
                upserted += len(chunk)
            except Exception as exc:
                db.rollback()
                logger.error("Chunk %d–%d failed: %s", start, start + len(chunk), exc)
                errors += len(chunk)

    logger.info("Done. Upserted: %d, Errors: %d", upserted, errors)
    if errors:
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build NOGA pgvector embeddings")
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run(batch_size=args.batch_size, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
