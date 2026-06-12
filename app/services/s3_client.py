"""Thin boto3 wrapper for S3-compatible object storage.
For CSV exports and for storing trained model artifacts (TF-IDF vectorizers, K-Means models, etc.).
"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Process-scoped flag set by check_crawl_s3_connectivity() when the crawl
# bucket is unreachable.  Prevents per-page upload hangs from blocking crawl jobs.
_crawl_uploads_disabled: bool = False


def check_crawl_s3_connectivity() -> bool:
    """Probe the crawl bucket; disable uploads for this process if unreachable.

    Returns True if the bucket is reachable, False if disabled.
    Called once at crawl-job start so individual pages don't stall.
    """
    global _crawl_uploads_disabled
    if not is_crawl_bucket_configured():
        return False
    try:
        bucket = _crawl_bucket()
        _client().head_bucket(Bucket=bucket)
        _crawl_uploads_disabled = False
        return True
    except Exception as exc:  # noqa: BLE001
        _crawl_uploads_disabled = True
        logger.warning("Crawl S3 bucket unreachable — HTML uploads disabled for this run: %s", exc)
        return False


def _client():
    import boto3
    from botocore.config import Config
    from app.config import settings
    return boto3.client(
        "s3",
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        endpoint_url=settings.s3_endpoint_url or None,
        config=Config(connect_timeout=10, read_timeout=30, retries={"max_attempts": 2}),
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


# ---------------------------------------------------------------------------
# Crawl HTML helpers (raw page HTML from web crawler)
# ---------------------------------------------------------------------------

def _crawl_bucket() -> str:
    """Return the crawl bucket name, falling back to the models bucket if unset."""
    from app.config import settings
    return settings.s3_bucket_crawl or settings.s3_bucket_models


def is_crawl_bucket_configured() -> bool:
    from app.config import settings
    return bool(settings.s3_access_key and settings.s3_secret_key and _crawl_bucket())


def crawl_s3_key(company_id: int, page_type: str) -> str:
    """Canonical S3 key for a crawled page. page_type: homepage|impressum|privacy|other."""
    return f"crawl/{company_id}/{page_type}.html"


def upload_crawl_html(html_bytes: bytes, s3_key: str) -> None:
    if _crawl_uploads_disabled:
        return
    import io
    bucket = _crawl_bucket()
    _client().upload_fileobj(io.BytesIO(html_bytes), bucket, s3_key)
    logger.debug("Uploaded crawl HTML → s3://%s/%s (%d bytes)", bucket, s3_key, len(html_bytes))


def download_crawl_html(s3_key: str) -> bytes:
    import io
    buf = io.BytesIO()
    _client().download_fileobj(_crawl_bucket(), s3_key, buf)
    return buf.getvalue()
