"""Rate limiting helpers.

Thin wrapper around `check_public_rate_limit` from `app.auth` that raises
HTTP 429 directly, eliminating the if/raise boilerplate at each call site.
"""

from __future__ import annotations

from fastapi import HTTPException, Request, status

from app.auth import check_public_rate_limit, get_client_ip
from app.models.user import User


def check_rate_limit(
    key: str,
    action: str,
    *,
    window: int = 300,
    max_calls: int = 10,
    detail: str | None = None,
) -> None:
    """Raise HTTP 429 if *key*+*action* exceeds *max_calls* in *window* seconds.

    Uses an in-process sliding window. Precise for single-pod deployments;
    provides best-effort protection in multi-pod setups.
    """
    if not check_public_rate_limit(key, action, window=window, max_requests=max_calls):
        msg = detail or f"Too many requests. Maximum {max_calls} calls per {window // 60} minutes."
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=msg)


def check_job_rate_limit(
    request: Request,
    current_user: User,
    action: str,
    *,
    window: int = 300,
    max_calls: int = 10,
) -> None:
    """Rate-limit authenticated job-triggering endpoints by user ID.

    Superadmins are never limited.
    """
    if current_user.is_superadmin:
        return
    check_rate_limit(
        f"user_{current_user.id}",
        f"job_rl:{action}",
        window=window,
        max_calls=max_calls,
        detail=f"Too many requests. Maximum {max_calls} '{action}' calls per {window // 60} minutes.",
    )
