"""Rate limiting helpers.

Thin wrapper around `check_public_rate_limit` from `app.auth` that raises
HTTP 429 directly, eliminating the if/raise boilerplate at each call site.
"""

from __future__ import annotations

from fastapi import HTTPException, Request, status

from app.auth import check_public_rate_limit, get_client_ip
from app.models.user import User


# ---------------------------------------------------------------------------
# Tier-based rate limit configs: (window_seconds, max_calls)
# Each entry is per-action, per-user, per-window.
# ---------------------------------------------------------------------------

_TIER_JOB_LIMITS: dict[str, dict[str, tuple[int, int]]] = {
    # tier → action → (window_seconds, max_calls)
    "free": {
        "claude_classify": (900, 2),   # 2 per 15 min
        "csv_export":      (900, 1),   # 1 per 15 min
        "web_search":      (600, 5),   # 5 per 10 min
        "_default":        (600, 3),
    },
    "simple": {
        "claude_classify": (600, 5),
        "csv_export":      (600, 3),
        "web_search":      (600, 15),
        "_default":        (600, 5),
    },
    "explorer": {
        "claude_classify": (600, 15),
        "csv_export":      (600, 5),
        "web_search":      (600, 30),
        "_default":        (600, 10),
    },
    # researcher / strategist / custom share the same permissive limits
    "_paid": {
        "claude_classify": (600, 20),
        "csv_export":      (600, 5),
        "web_search":      (600, 30),
        "_default":        (300, 10),
    },
}


def _get_tier_limits(tier: str, action: str) -> tuple[int, int]:
    """Return (window_seconds, max_calls) for a tier+action combination."""
    bucket = tier if tier in _TIER_JOB_LIMITS else "_paid"
    tier_map = _TIER_JOB_LIMITS[bucket]
    return tier_map.get(action, tier_map["_default"])


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
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=msg,
            headers={"Retry-After": str(window)},
        )


def check_job_rate_limit(
    request: Request,
    current_user: User,
    action: str,
    *,
    window: int | None = None,
    max_calls: int | None = None,
    org_tier: str | None = None,
) -> None:
    """Rate-limit authenticated job-triggering endpoints by user ID.

    Automatically applies stricter limits for the free tier when *org_tier* is
    provided. Caller-supplied *window* / *max_calls* override the tier defaults.
    Superadmins are never limited.
    """
    if current_user.is_superadmin:
        return

    tier = org_tier or "free"
    default_window, default_max = _get_tier_limits(tier, action)
    resolved_window = window if window is not None else default_window
    resolved_max = max_calls if max_calls is not None else default_max

    check_rate_limit(
        f"user_{current_user.id}",
        f"job_rl:{action}",
        window=resolved_window,
        max_calls=resolved_max,
        detail=f"Too many requests. Maximum {resolved_max} '{action}' calls per {resolved_window // 60} minutes.",
    )
