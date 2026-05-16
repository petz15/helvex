"""Authentication: signed cookies (browser UI) + JWT Bearer tokens (API clients).

Cookie sessions use itsdangerous (unchanged from before).
JWT Bearer tokens use PyJWT with HS256, same SECRET_KEY.

Routes that need the current user should depend on `get_current_user`.
The auth_gate middleware enforces authentication globally; the dependency
is for routes that need to *act as* the user (audit logging, quota, etc.).
"""

import ipaddress
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Annotated
from urllib.parse import quote

from fastapi import Cookie, Depends, HTTPException, Request, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
import jwt
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import Session

from app import crud
from app.config import settings
from app.database import get_db
from app.models.user import User

COOKIE_NAME = "session"
_SESSION_MAX_AGE = 8 * 3600  # 8 hours
_SALT = "session-v1"
_EMAIL_VERIFY_SALT = "email-verify-v1"
_EMAIL_VERIFY_MAX_AGE = 1 * 3600  # 1 hour
_PASSWORD_RESET_SALT = "password-reset-v1"
_PASSWORD_RESET_MAX_AGE = 1 * 3600  # 1 hour
_INVITE_SALT = "org-invite-v1"
_INVITE_MAX_AGE = 7 * 24 * 3600  # 7 days
_EMAIL_CHANGE_SALT = "email-change-v1"
_EMAIL_CHANGE_MAX_AGE = 1 * 3600  # 1 hour

# Tier hierarchy — higher index = higher tier.
# Canonical names post-migration-0039; legacy names are mapped in tiers.py.
_TIER_ORDER = ["free", "simple", "explorer", "researcher", "strategist", "custom"]

# Backward-compat map for rows not yet data-migrated.
_TIER_LEGACY = {"pro": "simple", "team": "explorer", "enterprise": "strategist",
                "starter": "simple", "professional": "explorer"}

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
    now = datetime.now(tz=timezone.utc)
    expire = now + (expires_delta or timedelta(seconds=_JWT_EXPIRE_SECONDS))
    payload = {"sub": str(user_id), "exp": expire, "iat": now}
    return jwt.encode(payload, settings.secret_key, algorithm=_JWT_ALGORITHM)


def decode_access_token(token: str) -> int | None:
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[_JWT_ALGORITHM])
        sub = payload.get("sub")
        return int(sub) if sub is not None else None
    except (jwt.InvalidTokenError, ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Shared helpers — extract user_id (and issued_at for revocation checks)
# ---------------------------------------------------------------------------

def _auth_info_from_request(request: Request) -> tuple[int, datetime | None] | None:
    """Returns (user_id, issued_at) from cookie or JWT. issued_at may be None."""
    token = request.cookies.get(COOKIE_NAME)
    if token:
        try:
            user_id, ts = _serializer().loads(
                token, max_age=_SESSION_MAX_AGE, return_timestamp=True
            )
            return (int(user_id), ts)
        except (SignatureExpired, BadSignature, ValueError, TypeError):
            pass

    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        try:
            payload = jwt.decode(
                auth_header[7:], settings.secret_key, algorithms=[_JWT_ALGORITHM]
            )
            sub = payload.get("sub")
            if sub is None:
                return None
            iat = payload.get("iat")
            issued_at = datetime.fromtimestamp(iat, tz=timezone.utc) if iat else None
            return (int(sub), issued_at)
        except (jwt.InvalidTokenError, ValueError, TypeError):
            pass

    return None


def _user_id_from_request(request: Request) -> int | None:
    info = _auth_info_from_request(request)
    return info[0] if info else None


# ---------------------------------------------------------------------------
# FastAPI dependency
# ---------------------------------------------------------------------------

_user_cache: dict[int, tuple[User, float]] = {}
_user_cache_lock = Lock()
_USER_CACHE_TTL = 30.0  # seconds


def _get_cached_user(db: Session, user_id: int) -> User | None:
    now = time.monotonic()
    with _user_cache_lock:
        entry = _user_cache.get(user_id)
        if entry and now - entry[1] < _USER_CACHE_TTL:
            # Only merge(load=False) when the cached object is clean; if it
            # became dirty (attribute set while detached), fall through to
            # re-fetch so we never hit the InvalidRequestError.
            if not sa_inspect(entry[0]).modified:
                return db.merge(entry[0], load=False)
    user = crud.get_user(db, user_id)
    if user:
        # Expunge before caching: merge() returns a new in-session instance,
        # so handler modifications won't dirty the cached detached copy.
        db.expunge(user)
        with _user_cache_lock:
            _user_cache[user_id] = (user, now)
        return db.merge(user, load=False)
    return None


def invalidate_user_cache(user_id: int) -> None:
    with _user_cache_lock:
        _user_cache.pop(user_id, None)


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
) -> User:
    """Dependency that returns the authenticated User or raises 401."""
    auth_info = _auth_info_from_request(request)
    if auth_info is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user_id, issued_at = auth_info
    user = _get_cached_user(db, user_id)
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
            headers={"WWW-Authenticate": "Bearer"},
        )
    # Reject tokens issued before the user explicitly logged out
    if (
        user.logged_out_at is not None
        and issued_at is not None
        and issued_at <= user.logged_out_at
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired. Please log in again.",
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
# Org invite tokens — encode (org_id, email), 7-day expiry
# ---------------------------------------------------------------------------

def create_invite_token(org_id: int, email: str, role: str = "viewer") -> str:
    return URLSafeTimedSerializer(settings.secret_key, salt=_INVITE_SALT).dumps(
        (org_id, email, role)
    )


def decode_invite_token(token: str) -> tuple[int, str, str] | None:
    """Return (org_id, email, role) or None if the token is invalid/expired."""
    try:
        data = URLSafeTimedSerializer(settings.secret_key, salt=_INVITE_SALT).loads(
            token, max_age=_INVITE_MAX_AGE
        )
        # Tokens created before 0041 only have (org_id, email) — default role to viewer.
        if len(data) == 2:
            org_id, email = data
            role = "viewer"
        else:
            org_id, email, role = data
        return (int(org_id), str(email), str(role))
    except (SignatureExpired, BadSignature, ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Email change tokens — encode (user_id, new_email), 1-hour expiry
# ---------------------------------------------------------------------------

def create_email_change_token(user_id: int, new_email: str) -> str:
    return URLSafeTimedSerializer(settings.secret_key, salt=_EMAIL_CHANGE_SALT).dumps((user_id, new_email))


def decode_email_change_token(token: str) -> tuple[int, str] | None:
    try:
        data = URLSafeTimedSerializer(settings.secret_key, salt=_EMAIL_CHANGE_SALT).loads(
            token, max_age=_EMAIL_CHANGE_MAX_AGE
        )
        user_id, new_email = data
        return (int(user_id), str(new_email))
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


# ---------------------------------------------------------------------------
# Rate limiting — in-process sliding window (per pod)
# ---------------------------------------------------------------------------

_RATE_WINDOW = 900   # 15 minutes
_RATE_MAX = 10       # failed login attempts per window per IP

# In-memory fallback (used when Redis is unavailable)
_login_attempts: dict[str, list[float]] = defaultdict(list)
_email_attempts: dict[str, list[float]] = defaultdict(list)
_request_counts: dict[str, list[float]] = defaultdict(list)


def _is_private_ip(ip: str) -> bool:
    try:
        parsed = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return bool(parsed.is_private or parsed.is_loopback or parsed.is_link_local)


def _is_ip_literal(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def get_client_ip(request: Request) -> str:
    """Best-effort client IP extraction.

    In Kubernetes/Ingress setups, `request.client.host` is often the reverse proxy
    (e.g. Traefik) which would make all users share a rate-limit bucket.

    We prefer `X-Forwarded-For` / `X-Real-Ip` only when the direct peer is a
    private/loopback address (i.e. likely a trusted proxy hop).
    """
    peer_ip = request.client.host if request.client else "unknown"
    xff = request.headers.get("x-forwarded-for", "")
    # Trust forwarded headers when the direct peer is a private/loopback address
    # (typical reverse proxy), or when it's not an IP literal at all (e.g. Starlette
    # TestClient uses 'testclient').
    trust_forwarded = peer_ip != "unknown" and (_is_private_ip(peer_ip) or not _is_ip_literal(peer_ip))
    if xff and trust_forwarded:
        # XFF format: client, proxy1, proxy2 ...
        first = xff.split(",", 1)[0].strip()
        if first:
            return first
    xri = request.headers.get("x-real-ip", "").strip()
    if xri and trust_forwarded:
        return xri
    return peer_ip


def is_login_allowed(ip: str) -> bool:
    """Return True if this IP can attempt another login."""
    now = time.monotonic()
    attempts = [t for t in _login_attempts[ip] if now - t < _RATE_WINDOW]
    _login_attempts[ip] = attempts
    return len(attempts) < _RATE_MAX


def record_login_failure(ip: str) -> None:
    """Record one failed login attempt for this IP."""
    now = time.monotonic()
    attempts = [t for t in _login_attempts[ip] if now - t < _RATE_WINDOW]
    attempts.append(now)
    _login_attempts[ip] = attempts


def is_email_login_allowed(email: str) -> bool:
    """Return True if this email account has not hit the per-email lockout threshold."""
    now = time.monotonic()
    key = email.lower()
    attempts = [t for t in _email_attempts[key] if now - t < _RATE_WINDOW]
    _email_attempts[key] = attempts
    return len(attempts) < _RATE_MAX


def record_email_login_failure(email: str) -> None:
    """Record one failed login attempt against this email address."""
    now = time.monotonic()
    key = email.lower()
    attempts = [t for t in _email_attempts[key] if now - t < _RATE_WINDOW]
    attempts.append(now)
    _email_attempts[key] = attempts


def check_login_rate_limit(ip: str) -> bool:
    """Backward-compatible helper used by older code paths."""
    if not is_login_allowed(ip):
        return False
    record_login_failure(ip)
    return True


def check_public_rate_limit(ip: str, action: str, *, window: float = 900, max_requests: int = 5) -> bool:
    """Rate-limit a public endpoint by IP + action label.

    Uses an in-process sliding window (per pod). Precise for single-pod deployments;
    provides best-effort protection in multi-pod setups.
    """
    bucket = f"{action}:{ip}"
    now = time.monotonic()
    calls = [t for t in _request_counts[bucket] if now - t < window]
    if len(calls) >= max_requests:
        _request_counts[bucket] = calls
        return False
    calls.append(now)
    _request_counts[bucket] = calls
    return True


# ---------------------------------------------------------------------------
# Superadmin dependency
# ---------------------------------------------------------------------------

def require_superadmin(user: User = Depends(get_current_user)) -> User:
    """Dependency — raises 403 unless the user is a superadmin."""
    if not user.is_superadmin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Superadmin access required",
        )
    return user


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
