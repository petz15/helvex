"""Thin boto3 wrapper for S3-compatible object storage.

Used exclusively for async CSV export files.  Configure via env vars:
  S3_ACCESS_KEY, S3_SECRET_KEY, S3_ENDPOINT_URL, S3_BUCKET, S3_EXPORT_PREFIX
"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def _client():
    import boto3
    from app.config import settings
    return boto3.client(
        "s3",
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        endpoint_url=settings.s3_endpoint_url or None,
    )


def is_configured() -> bool:
    from app.config import settings
    return bool(settings.s3_access_key and settings.s3_secret_key and settings.s3_bucket_exports)


def upload_file(local_path: str | Path, s3_key: str) -> None:
    """Upload a local file to the exports bucket at the given key."""
    from app.config import settings
    client = _client()
    client.upload_file(str(local_path), settings.s3_bucket_exports, s3_key)
    logger.info("Uploaded %s → s3://%s/%s", local_path, settings.s3_bucket_exports, s3_key)


def delete_object(s3_key: str) -> None:
    """Delete an object from the exports bucket. Silently ignores errors."""
    from app.config import settings
    try:
        _client().delete_object(Bucket=settings.s3_bucket_exports, Key=s3_key)
    except Exception:  # noqa: BLE001
        pass


def generate_presigned_url(s3_key: str, expires_in: int = 7 * 24 * 3600) -> str:
    """Return a presigned GET URL valid for `expires_in` seconds (default 7 days)."""
    from app.config import settings
    return _client().generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.s3_bucket_exports, "Key": s3_key},
        ExpiresIn=expires_in,
    )


def export_s3_key(user_id: int) -> str:
    """Canonical S3 key for a user's CSV export (one per user, overwrites on re-run)."""
    return f"{user_id}/export.csv"


# ---------------------------------------------------------------------------
# Model artifact helpers (NOGA embeddings, TF-IDF vectorizers, K-Means, etc.)
# ---------------------------------------------------------------------------

def is_models_bucket_configured() -> bool:
    from app.config import settings
    return bool(settings.s3_access_key and settings.s3_secret_key and settings.s3_bucket_models)


def upload_model_bytes(data: bytes, s3_key: str) -> None:
    """Upload raw bytes to the models bucket at the given key."""
    import io
    from app.config import settings
    client = _client()
    client.upload_fileobj(io.BytesIO(data), settings.s3_bucket_models, s3_key)
    logger.info("Uploaded model artifact → s3://%s/%s (%d bytes)", settings.s3_bucket_models, s3_key, len(data))


def download_model_bytes(s3_key: str) -> bytes:
    """Download raw bytes from the models bucket. Raises if key doesn't exist."""
    import io
    from app.config import settings
    buf = io.BytesIO()
    _client().download_fileobj(settings.s3_bucket_models, s3_key, buf)
    return buf.getvalue()
