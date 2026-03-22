"""Session authentication using itsdangerous signed cookies.

Tokens are signed with the app SECRET_KEY + a fixed salt.  The token payload
is the integer user_id.  Expiry is enforced by itsdangerous (timestamp embedded
in the token), so there is no server-side session store required.

Usage in routes:
    from app.auth import require_login

    @router.get("/ui")
    def ui_home(request: Request, user: User = Depends(require_login), ...):
        ...
"""

import time
from collections import defaultdict
from typing import Annotated
from urllib.parse import quote

from fastapi import Cookie, Depends, HTTPException, Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy.orm import Session

from app import crud
from app.config import settings
from app.database import get_db
from app.models.user import User

COOKIE_NAME = "session"
_SESSION_MAX_AGE = 8 * 3600  # 8 hours
_SALT = "session-v1"

# ---------------------------------------------------------------------------
# Token helpers
# ---------------------------------------------------------------------------

def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.secret_key, salt=_SALT)


def create_session_cookie(user_id: int) -> str:
    """Return a signed token encoding the given user_id."""
    return _serializer().dumps(user_id)


def decode_session_cookie(token: str) -> int | None:
    """Return the user_id from a valid, non-expired token, else None."""
    try:
        user_id = _serializer().loads(token, max_age=_SESSION_MAX_AGE)
        return int(user_id)
    except (SignatureExpired, BadSignature, ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Login rate limiting (in-memory; replaced by Redis in Phase 1)
# ---------------------------------------------------------------------------

_login_attempts: dict[str, list[float]] = defaultdict(list)
_RATE_WINDOW = 900   # 15 minutes
_RATE_MAX = 10       # attempts per window per IP


def check_login_rate_limit(ip: str) -> bool:
    """Return True if the request is allowed, False if rate-limited."""
    now = time.monotonic()
    attempts = [t for t in _login_attempts[ip] if now - t < _RATE_WINDOW]
    _login_attempts[ip] = attempts
    if len(attempts) >= _RATE_MAX:
        return False
    _login_attempts[ip].append(now)
    return True


# ---------------------------------------------------------------------------
# FastAPI dependency
# ---------------------------------------------------------------------------

def require_login(
    request: Request,
    session: Annotated[str | None, Cookie(alias=COOKIE_NAME)] = None,
    db: Session = Depends(get_db),
) -> User:
    """Dependency that returns the authenticated User or redirects to /login."""
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
