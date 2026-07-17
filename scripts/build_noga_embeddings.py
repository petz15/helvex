"""Build and upload NOGA embeddings to S3.

Embeds each NOGA entry's German label using the shared multilingual embedding
service (DEFAULT_MODEL in app.services.ml.embeddings — currently
paraphrase-multilingual-mpnet-base-v2, 768-dim), then uploads two files to S3:
  - models/noga_embeddings.npy         — float32 array, shape (N, D)
  - models/noga_embedding_ids.json     — list of N NOGA codes matching row order

The model MUST match app.services.ml.embeddings.DEFAULT_MODEL because
app.services.ml.noga.classify_company_noga() embeds queries via that same service
and computes cosine similarity against this matrix.

Run once; re-run only if noga_lookup.json changes or DEFAULT_MODEL changes.

Usage:
    python scripts/build_noga_embeddings.py

Requires: S3_ACCESS_KEY, S3_SECRET_KEY, S3_ENDPOINT_URL, S3_BUCKET_MODELS set in .env
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Load .env before any app imports
from dotenv import load_dotenv
repo_root = Path(__file__).resolve().parents[1]
load_dotenv(repo_root / ".env")

# Allow running from repo root without installing the package.
sys.path.insert(0, str(repo_root))

import numpy as np

from app.services.platform import s3_client
from app.services.ml.embeddings import DEFAULT_MODEL, embed_texts

LOOKUP_PATH = Path(__file__).resolve().parents[1] / "noga_lookup.json"
S3_EMBEDDINGS_KEY = "models/noga_embeddings.npy"
S3_IDS_KEY = "models/noga_embedding_ids.json"


def _de_label(node: dict) -> str:
    name = node.get("name")
    if isinstance(name, dict):
        return name.get("de") or name.get("fr") or name.get("it") or name.get("en") or ""
    return str(name or "")


def main() -> None:
    if not s3_client.is_models_bucket_configured():
        print("ERROR: S3_BUCKET_MODELS is not configured. Set it in .env and retry.")
        sys.exit(1)

    print(f"Loading {LOOKUP_PATH} …")
    with LOOKUP_PATH.open("r", encoding="utf-8") as f:
        lookup: dict = json.load(f)

    codes = list(lookup.keys())
    labels = [_de_label(lookup[c]) for c in codes]

    print(f"  {len(codes)} NOGA entries loaded.")
    print(f"Encoding with shared embedding service (model: {DEFAULT_MODEL}) …")
    embeddings = embed_texts(labels, batch_size=128, show_progress=True)
    print(f"  Embeddings shape: {embeddings.shape}, dtype: {embeddings.dtype}")

    # Serialize
    emb_bytes = embeddings.astype("float32").tobytes()
    # Store shape header so the loader can reconstruct correctly
    shape_info = {"rows": embeddings.shape[0], "cols": embeddings.shape[1]}
    ids_payload = {"shape": shape_info, "codes": codes}
    ids_bytes = json.dumps(ids_payload, ensure_ascii=False).encode("utf-8")

    print(f"Uploading to S3 …")
    s3_client.upload_model_bytes(emb_bytes, S3_EMBEDDINGS_KEY)
    s3_client.upload_model_bytes(ids_bytes, S3_IDS_KEY)

    print("Done.")
    print(f"  s3 key: {S3_EMBEDDINGS_KEY}  ({len(emb_bytes):,} bytes)")
    print(f"  s3 key: {S3_IDS_KEY}  ({len(ids_bytes):,} bytes)")


if __name__ == "__main__":
    main()
