"""Authentication: signed cookies (browser UI) + JWT Bearer tokens (API clients).

Cookie sessions use itsdangerous (unchanged from before).
JWT Bearer tokens use PyJWT with HS256, same SECRET_KEY.

Routes that need the current user should depend on `get_current_user`.
The auth_gate middleware enforces authentication globally; the dependency
is for routes that need to *act as* the user (audit logging, quota, etc.).
"""

import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Annotated
from urllib.parse import quote

from fastapi import Cookie, Depends, HTTPException, Request, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
import jwt
from sqlalchemy.orm import Session

from app import crud
from app.config import settings
from app.database import get_db
from app.models.user import User

COOKIE_NAME = "session"
_SESSION_MAX_AGE = 8 * 3600  # 8 hours
_SALT = "session-v1"
_EMAIL_VERIFY_SALT = "email-verify-v1"
_EMAIL_VERIFY_MAX_AGE = 24 * 3600  # 24 hours
_PASSWORD_RESET_SALT = "password-reset-v1"
_PASSWORD_RESET_MAX_AGE = 1 * 3600  # 1 hour

# Tier hierarchy — higher index = higher tier
_TIER_ORDER = ["free", "pro", "team", "enterprise"]

# ---------------------------------------------------------------------------
# Cookie-based sessions (browser UI)
# ---------------------------------------------------------------------------

def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.secret_key, salt=_SALT)


def create_session_cookie(user_id: int) -> str:
    return _serializer().dumps(user_id)


def decode_session_cookie(token: str) -> int | None:
    try:
        user_id = _serializer().loads(token, max_age=_SESSION_MAX_AGE)
        return int(user_id)
    except (SignatureExpired, BadSignature, ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# JWT Bearer tokens (API clients)
# ---------------------------------------------------------------------------

_JWT_ALGORITHM = "HS256"
_JWT_EXPIRE_SECONDS = 8 * 3600  # 8 hours


def create_access_token(user_id: int, expires_delta: timedelta | None = None) -> str:
    expire = datetime.now(tz=timezone.utc) + (
        expires_delta or timedelta(seconds=_JWT_EXPIRE_SECONDS)
    )
    payload = {"sub": str(user_id), "exp": expire}
    return jwt.encode(payload, settings.secret_key, algorithm=_JWT_ALGORITHM)


def decode_access_token(token: str) -> int | None:
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[_JWT_ALGORITHM])
        sub = payload.get("sub")
        return int(sub) if sub is not None else None
    except (jwt.InvalidTokenError, ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Shared helper — extract user_id from either auth mechanism
# ---------------------------------------------------------------------------

def _user_id_from_request(request: Request) -> int | None:
    token = request.cookies.get(COOKIE_NAME)
    if token:
        uid = decode_session_cookie(token)
        if uid is not None:
            return uid
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return decode_access_token(auth_header[7:])
    return None


# ---------------------------------------------------------------------------
# FastAPI dependency
# ---------------------------------------------------------------------------

def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
) -> User:
    """Dependency that returns the authenticated User or raises 401."""
    user_id = _user_id_from_request(request)
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user = crud.get_user(db, user_id)
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


# ---------------------------------------------------------------------------
# Email verification tokens (signed, no DB storage needed)
# ---------------------------------------------------------------------------

def create_verification_token(user_id: int) -> str:
    return URLSafeTimedSerializer(settings.secret_key, salt=_EMAIL_VERIFY_SALT).dumps(user_id)


def create_password_reset_token(user_id: int) -> str:
    return URLSafeTimedSerializer(settings.secret_key, salt=_PASSWORD_RESET_SALT).dumps(user_id)


def decode_password_reset_token(token: str) -> int | None:
    try:
        user_id = URLSafeTimedSerializer(settings.secret_key, salt=_PASSWORD_RESET_SALT).loads(
            token, max_age=_PASSWORD_RESET_MAX_AGE
        )
        return int(user_id)
    except (SignatureExpired, BadSignature, ValueError, TypeError):
        return None


def decode_verification_token(token: str) -> int | None:
    try:
        user_id = URLSafeTimedSerializer(settings.secret_key, salt=_EMAIL_VERIFY_SALT).loads(
            token, max_age=_EMAIL_VERIFY_MAX_AGE
        )
        return int(user_id)
    except (SignatureExpired, BadSignature, ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Tier + email-verified dependencies
# ---------------------------------------------------------------------------

def require_verified_email(user: User = Depends(get_current_user)) -> User:
    """Dependency — raises 403 if the user's email is not verified."""
    if not user.email_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Email address not verified. Please check your inbox.",
        )
    return user


def require_tier(min_tier: str):
    """Dependency factory — raises 403 if user's tier is below *min_tier*.

    Usage::

        @router.get("/premium")
        def premium_route(user: User = Depends(require_tier("pro"))):
            ...
    """
    def _check(user: User = Depends(get_current_user)) -> User:
        user_level = _TIER_ORDER.index(user.tier) if user.tier in _TIER_ORDER else 0
        required_level = _TIER_ORDER.index(min_tier) if min_tier in _TIER_ORDER else 0
        if user.is_superadmin:
            return user
        if user_level < required_level:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"This feature requires the '{min_tier}' tier or above.",
            )
        return user
    return _check


# ---------------------------------------------------------------------------
# Login rate limiting (in-memory; replaced by Redis in Phase 2)
# ---------------------------------------------------------------------------

_login_attempts: dict[str, list[float]] = defaultdict(list)
_RATE_WINDOW = 900   # 15 minutes
_RATE_MAX = 10       # failed attempts per window per IP

# General request rate limiter — counts every call regardless of outcome
_request_counts: dict[str, list[float]] = defaultdict(list)


def _trim_attempts(ip: str) -> list[float]:
    now = time.monotonic()
    attempts = [t for t in _login_attempts[ip] if now - t < _RATE_WINDOW]
    _login_attempts[ip] = attempts
    return attempts


def is_login_allowed(ip: str) -> bool:
    """Return True if this IP can attempt another login."""
    return len(_trim_attempts(ip)) < _RATE_MAX


def record_login_failure(ip: str) -> None:
    """Record one failed login attempt for this IP."""
    _trim_attempts(ip)
    _login_attempts[ip].append(time.monotonic())


def check_login_rate_limit(ip: str) -> bool:
    """Backward-compatible helper used by older code paths."""
    if not is_login_allowed(ip):
        return False
    record_login_failure(ip)
    return True


def check_public_rate_limit(ip: str, action: str, *, window: float = 900, max_requests: int = 5) -> bool:
    """Rate-limit a public endpoint by IP + action label.

    Counts every call (success or failure). Returns False when the IP exceeds
    *max_requests* within *window* seconds for this action.
    """
    bucket = f"{action}:{ip}"
    now = time.monotonic()
    calls = [t for t in _request_counts[bucket] if now - t < window]
    if len(calls) >= max_requests:
        _request_counts[bucket] = calls  # keep trimmed
        return False
    calls.append(now)
    _request_counts[bucket] = calls
    return True


# ---------------------------------------------------------------------------
# Legacy require_login dependency (kept for backward compat)
# ---------------------------------------------------------------------------

def require_login(
    request: Request,
    session: Annotated[str | None, Cookie(alias=COOKIE_NAME)] = None,
    db: Session = Depends(get_db),
) -> User:
    if session:
        user_id = decode_session_cookie(session)
        if user_id is not None:
            user = crud.get_user(db, user_id)
            if user and user.is_active:
                return user

    next_url = quote(str(request.url.path), safe="")
    raise HTTPException(
        status_code=302,
        headers={"Location": f"/login?next={next_url}"},
    )
