"""Authentication: signed cookies (browser UI) + JWT Bearer tokens (API clients).

Cookie sessions use itsdangerous (unchanged from before).
JWT Bearer tokens use python-jose with HS256, same SECRET_KEY.

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
from jose import JWTError, jwt as jose_jwt
from sqlalchemy.orm import Session

from app import crud
from app.config import settings
from app.database import get_db
from app.models.user import User

COOKIE_NAME = "session"
_SESSION_MAX_AGE = 8 * 3600  # 8 hours
_SALT = "session-v1"

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
    return jose_jwt.encode(payload, settings.secret_key, algorithm=_JWT_ALGORITHM)


def decode_access_token(token: str) -> int | None:
    try:
        payload = jose_jwt.decode(token, settings.secret_key, algorithms=[_JWT_ALGORITHM])
        sub = payload.get("sub")
        return int(sub) if sub is not None else None
    except (JWTError, ValueError, TypeError):
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
# Login rate limiting (in-memory; replaced by Redis in Phase 2)
# ---------------------------------------------------------------------------

_login_attempts: dict[str, list[float]] = defaultdict(list)
_RATE_WINDOW = 900   # 15 minutes
_RATE_MAX = 10       # failed attempts per window per IP


def check_login_rate_limit(ip: str) -> bool:
    """Return True if the request is allowed, False if rate-limited.

    Only call this on *failed* login attempts — successful logins do not
    consume rate-limit budget.
    """
    now = time.monotonic()
    attempts = [t for t in _login_attempts[ip] if now - t < _RATE_WINDOW]
    _login_attempts[ip] = attempts
    if len(attempts) >= _RATE_MAX:
        return False
    _login_attempts[ip].append(now)
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
