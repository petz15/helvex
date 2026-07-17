"""Auth API routes — login, registration, email verification, password management."""

import json
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app import crud
from app.auth import (
    COOKIE_NAME,
    check_public_rate_limit,
    create_access_token,
    create_email_change_token,
    create_password_reset_token,
    create_verification_token,
    decode_email_change_token,
    decode_password_reset_token,
    decode_verification_token,
    get_client_ip,
    get_current_user,
    is_email_login_allowed,
    is_login_allowed,
    record_email_login_failure,
    record_login_failure,
    set_session_cookie,
)
from app.database import get_db
from app.models.user import User
from app.schemas.user import (
    ChangeEmailRequest,
    ChangePasswordRequest,
    RegisterRequest,
    ResendVerificationRequest,
    ResetPasswordRequest,
    TokenResponse,
    UserRead,
)
from app.schemas.billing import BillingAddress, BillingAddressBookRead, BillingAddressCreate, BillingAddressItem
from app.services.notifications.activity import log_activity
from app.services.billing.billing_addresses import parse_billing_address_book, serialize_billing_address_book
from app.services.notifications.email import (
    send_email_change_verification,
    send_password_reset_email,
    send_verification_email,
    send_welcome_email,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

_RESEND_COOLDOWN_SECONDS = 60


def _managed_current_user(db: Session, current_user: User) -> User:
    user = db.query(User).filter(User.id == current_user.id).first()
    if user is None:
        user = db.merge(current_user)
        db.flush()
    return user


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

@router.post("/token", response_model=TokenResponse, summary="Obtain a JWT Bearer token")
def login_for_token(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
) -> TokenResponse:
    from app.config import settings as _s
    if not getattr(_s, "enable_password_token_endpoint", False):
        # Disabled by default — see Settings.enable_password_token_endpoint.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    ip = get_client_ip(request)
    if not is_login_allowed(ip) or not is_email_login_allowed(email):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login attempts. Try again in 15 minutes.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user = crud.authenticate(db, email=email, password=password)
    if not user:
        record_login_failure(ip)
        record_email_login_failure(email)
        logger.warning("auth.login_failed email=%r ip=%s", email, ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    logger.info("auth.login_ok user_id=%s email=%r ip=%s", user.id, user.email, ip)
    log_activity(db, action="user_login", user_id=user.id, org_id=user.org_id, meta={"method": "token"}, ip=ip)
    db.commit()
    return TokenResponse(access_token=create_access_token(user.id))


# ---------------------------------------------------------------------------
# Cookie-based login / logout (used by the Next.js login page)
# ---------------------------------------------------------------------------

from pydantic import BaseModel as _BaseModel  # noqa: E402


class _LoginRequest(_BaseModel):
    email: str
    password: str


@router.post("/login", summary="Login and set a session cookie")
def login_cookie(
    request: Request,
    body: _LoginRequest,
    db: Session = Depends(get_db),
) -> JSONResponse:
    ip = get_client_ip(request)
    if not is_login_allowed(ip) or not is_email_login_allowed(body.email):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login attempts. Try again in 15 minutes.",
        )
    user = crud.authenticate(db, email=body.email, password=body.password)
    if not user:
        record_login_failure(ip)
        record_email_login_failure(body.email)
        logger.warning("auth.login_failed email=%r ip=%s", body.email, ip)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    logger.info("auth.login_ok user_id=%s email=%r ip=%s", user.id, user.email, ip)
    log_activity(db, action="user_login", user_id=user.id, org_id=user.org_id, meta={"method": "cookie"}, ip=ip)
    db.commit()
    forwarded_proto = request.headers.get("x-forwarded-proto", "")
    is_https = request.url.scheme == "https" or forwarded_proto.split(",")[0].strip().lower() == "https"
    response = JSONResponse({"ok": True})
    set_session_cookie(response, user.id, is_https=is_https, samesite="strict")
    return response


@router.post("/logout", status_code=200, summary="Clear the session cookie")
def logout_cookie(
    request: Request,
    db: Session = Depends(get_db),
) -> JSONResponse:
    from app.auth import _user_id_from_request, invalidate_user_cache
    user_id = _user_id_from_request(request)
    if user_id:
        user = crud.get_user(db, user_id)
        if user:
            user.logged_out_at = datetime.now(tz=timezone.utc)
            db.commit()
            invalidate_user_cache(user_id)
    response = JSONResponse({"ok": True})
    response.delete_cookie(key=COOKIE_NAME)
    return response


# ---------------------------------------------------------------------------
# Confirm email change (token from link in email)
# ---------------------------------------------------------------------------

class _ConfirmEmailChangeRequest(_BaseModel):
    token: str


@router.post("/confirm-email-change", status_code=204,
             summary="Confirm an email address change via signed token")
def confirm_email_change_api(
    request: Request,
    body: _ConfirmEmailChangeRequest,
    db: Session = Depends(get_db),
) -> None:
    if not check_public_rate_limit(get_client_ip(request), "confirm-email-change", window=3600, max_requests=5):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many requests. Try again later.")
    result = decode_email_change_token(body.token)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired confirmation link. Please request a new one.",
        )
    user_id, new_email = result
    user = crud.get_user(db, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")
    existing = crud.get_user_by_email(db, new_email)
    if existing and existing.id != user.id:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already in use")
    crud.update_email(db, user, new_email)
    log_activity(db, action="email_changed", user_id=user.id, org_id=user.org_id)
    db.commit()
    logger.info("auth.email_changed user_id=%s new_email=%r", user.id, new_email)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

@router.post("/register", response_model=UserRead, status_code=status.HTTP_201_CREATED,
             summary="Create a new user account")
def register(request: Request, body: RegisterRequest, db: Session = Depends(get_db)) -> UserRead:
    ip = get_client_ip(request)
    if not check_public_rate_limit(ip, "register", window=3600, max_requests=5):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many registration attempts. Try again later.",
        )
    existing = crud.get_user_by_email(db, body.email)
    if existing:
        logger.warning("auth.register_conflict field=email ip=%s verified=%s", ip, existing.email_verified)
        if not existing.email_verified:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="email_unverified")
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")
    user = crud.create_user(db, email=body.email, password=body.password)
    logger.info("auth.register_ok user_id=%s email=%r ip=%s", user.id, user.email, ip)
    log_activity(db, action="user_registered", user_id=user.id, meta={"email": user.email}, ip=ip)
    db.commit()
    _send_verification(db, user)
    return UserRead.model_validate(user)


# ---------------------------------------------------------------------------
# Email verification
# ---------------------------------------------------------------------------

@router.post("/resend-verification", status_code=status.HTTP_204_NO_CONTENT,
             summary="Re-send the verification email")
def resend_verification(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    if current_user.email_verified:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already verified")
    last_sent = current_user.email_verification_sent_at
    if last_sent:
        elapsed = (datetime.now(tz=timezone.utc) - last_sent).total_seconds()
        if elapsed < _RESEND_COOLDOWN_SECONDS:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Please wait {int(_RESEND_COOLDOWN_SECONDS - elapsed)}s before requesting another email",
            )
    logger.info("auth.resend_verification user_id=%s", current_user.id)
    _send_verification(db, current_user)


@router.post("/resend-verification-public", status_code=status.HTTP_204_NO_CONTENT,
             summary="Re-send verification email (public, accepts email address)")
def resend_verification_public(request: Request, body: ResendVerificationRequest, db: Session = Depends(get_db)) -> None:
    """Public endpoint — always returns 204 to avoid user enumeration.
    Sends a new verification email only if the account exists and is not yet verified,
    and the per-user cooldown has elapsed.
    """
    ip = get_client_ip(request)
    if not check_public_rate_limit(ip, "resend_verification", window=3600, max_requests=10):
        # Silently ignore to avoid leaking rate-limit info tied to a specific email
        return
    user = crud.get_user_by_email(db, body.email)
    if not user or user.email_verified:
        return  # silent — don't reveal whether the email exists
    last_sent = user.email_verification_sent_at
    if last_sent:
        elapsed = (datetime.now(tz=timezone.utc) - last_sent).total_seconds()
        if elapsed < _RESEND_COOLDOWN_SECONDS:
            return  # silent — cooldown enforced server-side
    logger.info("auth.resend_verification_public user_id=%s", user.id)
    _send_verification(db, user)


@router.get("/verify-email", response_model=UserRead, summary="Verify email via signed token")
def verify_email(token: str, db: Session = Depends(get_db)) -> UserRead:
    user_id = decode_verification_token(token)
    if user_id is None:
        logger.warning("auth.verify_email_failed reason=invalid_or_expired_token")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired verification link. Please request a new one.",
        )
    user = crud.get_user(db, user_id)
    if not user:
        logger.warning("auth.verify_email_failed reason=user_not_found user_id=%s", user_id)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if not user.email_verified:
        user = crud.mark_email_verified(db, user)
        logger.info("auth.email_verified user_id=%s", user.id)
        log_activity(db, action="email_verified", user_id=user.id, org_id=user.org_id)
        db.commit()
        try:
            send_welcome_email(to=user.email)
        except Exception:
            logger.warning("Failed to send welcome email to %s", user.email, exc_info=True)
    return UserRead.model_validate(user)


# ---------------------------------------------------------------------------
# Change password (requires auth)
# ---------------------------------------------------------------------------

@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT,
             summary="Change password for the current user")
def change_password(
    body: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    if not crud.verify_password(body.current_password, current_user.hashed_password):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is incorrect")
    crud.update_password(db, current_user, body.new_password)
    log_activity(db, action="password_changed", user_id=current_user.id, org_id=current_user.org_id)
    db.commit()


# ---------------------------------------------------------------------------
# Change email (requires auth + sends verification to new address)
# ---------------------------------------------------------------------------

@router.post("/request-email-change", status_code=status.HTTP_204_NO_CONTENT,
             summary="Request an email address change (sends verification to new address)")
def request_email_change(
    body: ChangeEmailRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    if not crud.verify_password(body.current_password, current_user.hashed_password):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is incorrect")
    if crud.get_user_by_email(db, body.new_email):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already in use")
    token = create_email_change_token(current_user.id, body.new_email)
    logger.info("auth.email_change_requested user_id=%s", current_user.id)
    try:
        send_email_change_verification(to=body.new_email, token=token)
    except Exception as exc:
        logger.exception("auth.email_change_send_failed user_id=%s", current_user.id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Email service unavailable. Please try again later.",
        ) from exc


# ---------------------------------------------------------------------------
# Forgot / reset password (public)
# ---------------------------------------------------------------------------

@router.post("/forgot-password", status_code=status.HTTP_204_NO_CONTENT,
             summary="Request a password reset email")
def forgot_password(request: Request, email: str = Form(...), db: Session = Depends(get_db)) -> None:
    ip = get_client_ip(request)
    if not check_public_rate_limit(ip, "forgot-password", window=900, max_requests=5):
        return
    user = crud.get_user_by_email(db, email)
    if user:
        token = create_password_reset_token(user.id)
        logger.info("auth.password_reset_requested user_id=%s ip=%s", user.id, ip)
        try:
            send_password_reset_email(to=user.email, token=token)
        except Exception:
            logger.exception("auth.password_reset_email_failed user_id=%s", user.id)
            pass


@router.post("/reset-password", status_code=status.HTTP_204_NO_CONTENT,
             summary="Set a new password using a reset token")
def reset_password(body: ResetPasswordRequest, db: Session = Depends(get_db)) -> None:
    user_id = decode_password_reset_token(body.token)
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset link. Please request a new one.",
        )
    user = crud.get_user(db, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    crud.update_password(db, user, body.new_password)


# ---------------------------------------------------------------------------
# Current user
# ---------------------------------------------------------------------------

@router.get("/me", response_model=UserRead, summary="Current authenticated user")
def get_me(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UserRead:
    # Lazy migration: ensure every user has a personal workspace org
    if not current_user.org_id:
        crud.ensure_personal_org(db, current_user)
    from sqlalchemy.orm import joinedload
    current_user = (
        db.query(User)
        .options(joinedload(User.org))
        .filter(User.id == current_user.id)
        .first()
    )
    return UserRead.model_validate(current_user)


@router.put("/me/billing-address", response_model=UserRead, summary="Update the current user's billing address")
def update_my_billing_address(
    body: BillingAddress,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UserRead:
    managed_user = _managed_current_user(db, current_user)
    managed_user.billing_address_json = json.dumps(body.model_dump())
    db.commit()
    db.refresh(managed_user)
    return UserRead.model_validate(managed_user)


@router.get("/me/billing-addresses", response_model=BillingAddressBookRead, summary="List the current user's billing addresses")
def list_my_billing_addresses(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BillingAddressBookRead:
    managed_user = _managed_current_user(db, current_user)
    addresses, default_id = parse_billing_address_book(managed_user.billing_address_json)
    return BillingAddressBookRead(addresses=[BillingAddressItem.model_validate(a) for a in addresses], default_id=default_id)


@router.post("/me/billing-addresses", response_model=BillingAddressBookRead, summary="Add a billing address")
def add_my_billing_address(
    body: BillingAddressCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BillingAddressBookRead:
    if not check_public_rate_limit(str(current_user.id), "billing-addr-write", window=60, max_requests=10):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many requests. Try again later.")
    managed_user = _managed_current_user(db, current_user)
    addresses, default_id = parse_billing_address_book(managed_user.billing_address_json)

    new_item = {
        "id": uuid.uuid4().hex,
        "label": (body.label or "").strip() or None,
        **body.model_dump(exclude={"label", "make_default"}),
    }
    addresses.append(new_item)
    if body.make_default or not default_id:
        default_id = str(new_item["id"])

    managed_user.billing_address_json = serialize_billing_address_book(addresses, default_id)
    db.commit()
    db.refresh(managed_user)
    return BillingAddressBookRead(addresses=[BillingAddressItem.model_validate(a) for a in addresses], default_id=default_id)


@router.put("/me/billing-addresses/{address_id}/default", response_model=BillingAddressBookRead, summary="Set default billing address")
def set_default_my_billing_address(
    address_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BillingAddressBookRead:
    if not check_public_rate_limit(str(current_user.id), "billing-addr-write", window=60, max_requests=10):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many requests. Try again later.")
    managed_user = _managed_current_user(db, current_user)
    addresses, _default_id = parse_billing_address_book(managed_user.billing_address_json)
    if not addresses:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No billing addresses found")
    if not any(str(a.get("id")) == address_id for a in addresses):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Billing address not found")

    managed_user.billing_address_json = serialize_billing_address_book(addresses, address_id)
    db.commit()
    db.refresh(managed_user)
    return BillingAddressBookRead(addresses=[BillingAddressItem.model_validate(a) for a in addresses], default_id=address_id)


@router.delete("/me/billing-addresses/{address_id}", response_model=BillingAddressBookRead, summary="Delete a billing address")
def delete_my_billing_address(
    address_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BillingAddressBookRead:
    if not check_public_rate_limit(str(current_user.id), "billing-addr-write", window=60, max_requests=10):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many requests. Try again later.")
    managed_user = _managed_current_user(db, current_user)
    addresses, default_id = parse_billing_address_book(managed_user.billing_address_json)
    kept = [a for a in addresses if str(a.get("id")) != address_id]
    if len(kept) == len(addresses):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Billing address not found")

    if not kept:
        managed_user.billing_address_json = None
        db.commit()
        db.refresh(managed_user)
        return BillingAddressBookRead(addresses=[], default_id=None)

    if default_id == address_id or not any(str(a.get("id")) == default_id for a in kept):
        default_id = str(kept[0]["id"])

    managed_user.billing_address_json = serialize_billing_address_book(kept, default_id)
    db.commit()
    db.refresh(managed_user)
    return BillingAddressBookRead(addresses=[BillingAddressItem.model_validate(a) for a in kept], default_id=default_id)


# ---------------------------------------------------------------------------
# Google OAuth2
# ---------------------------------------------------------------------------

import secrets as _secrets  # noqa: E402
from urllib.parse import urlencode as _urlencode  # noqa: E402

import httpx as _httpx  # noqa: E402
from fastapi.responses import RedirectResponse as _Redirect  # noqa: E402

_OAUTH_STATE_COOKIE = "oauth_state"
_OAUTH_NEXT_COOKIE = "oauth_next"
_OAUTH_STATE_MAX_AGE = 600  # 10 minutes


def _first_forwarded(v: str | None) -> str | None:
    if not v:
        return None
    return v.split(",")[0].strip() or None


def _public_base_url(request: Request) -> str:
    """Best-effort external base URL for redirects (scheme + host).

    Prefers X-Forwarded-* headers as set by an ingress/reverse proxy.
    """
    xf_proto = _first_forwarded(request.headers.get("x-forwarded-proto"))
    xf_host = _first_forwarded(request.headers.get("x-forwarded-host"))

    scheme = (xf_proto or request.url.scheme or "http").lower()
    host = xf_host or request.headers.get("host") or request.url.netloc

    return f"{scheme}://{host}".rstrip("/")


def _oauth_callback_uri(request: Request, provider: str) -> str:
    return f"{_public_base_url(request)}/api/v1/auth/{provider}/callback"


def _set_session(response: _Redirect, user_id: int, *, is_https: bool) -> None:
    # lax (not strict) is required here: after the OAuth redirect from Google
    # the browser treats the chain as cross-site-initiated, so strict cookies
    # are not sent on the following same-site redirect and auth breaks.
    set_session_cookie(response, user_id, is_https=is_https, samesite="lax")


@router.get("/google/authorize", include_in_schema=False)
async def google_authorize(request: Request, next: str | None = None) -> _Redirect:
    from app.config import settings as _s
    if not _s.google_client_id:
        raise HTTPException(status_code=503, detail="Google sign-in is not configured")
    state = _secrets.token_urlsafe(32)
    params = _urlencode({
        "client_id": _s.google_client_id,
        "redirect_uri": _oauth_callback_uri(request, "google"),
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "access_type": "online",
    })
    forwarded_proto = request.headers.get("x-forwarded-proto", "")
    is_https = request.url.scheme == "https" or forwarded_proto.split(",")[0].strip().lower() == "https"
    response = _Redirect(url=f"https://accounts.google.com/o/oauth2/v2/auth?{params}", status_code=302)
    response.set_cookie(_OAUTH_STATE_COOKIE, state, httponly=True, max_age=_OAUTH_STATE_MAX_AGE, samesite="lax", secure=is_https)
    # Persist the intended post-login destination across the OAuth redirect
    safe_next = next if (next and next.startswith("/") and "//" not in next) else "/app/search"
    response.set_cookie(_OAUTH_NEXT_COOKIE, safe_next, httponly=True, max_age=_OAUTH_STATE_MAX_AGE, samesite="lax", secure=is_https)
    return response


@router.get("/google/callback", include_in_schema=False)
async def google_callback(
    request: Request,
    db: Session = Depends(get_db),
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> _Redirect:
    from app.config import settings as _s
    from app import crud as _crud

    if error:
        logger.info("auth.google_oauth_denied error=%r", error)
        return _Redirect(url=f"{_public_base_url(request)}/login?oauth_error=1", status_code=302)

    stored_state = request.cookies.get(_OAUTH_STATE_COOKIE)
    if not stored_state or not code or not _secrets.compare_digest(stored_state, state or ""):
        raise HTTPException(status_code=400, detail="Invalid OAuth state or missing code")

    try:
        async with _httpx.AsyncClient(timeout=10) as client:
            token_resp = await client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "code": code,
                    "client_id": _s.google_client_id,
                    "client_secret": _s.google_client_secret,
                    "redirect_uri": _oauth_callback_uri(request, "google"),
                    "grant_type": "authorization_code",
                },
            )
            token_resp.raise_for_status()
            access_token = token_resp.json()["access_token"]

            userinfo_resp = await client.get(
                "https://www.googleapis.com/oauth2/v3/userinfo",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            userinfo_resp.raise_for_status()
            userinfo = userinfo_resp.json()
    except Exception:
        logger.exception("auth.google_oauth_exchange_failed")
        raise HTTPException(status_code=502, detail="Failed to complete Google sign-in. Please try again.")

    email = userinfo.get("email")
    provider_user_id = userinfo.get("sub")
    if not email or not provider_user_id:
        raise HTTPException(status_code=400, detail="Google account did not provide an email address")

    user = _crud.get_or_create_oauth_user(db, provider="google", provider_user_id=provider_user_id, email=email)
    logger.info("auth.google_oauth_ok user_id=%s email=%r", user.id, user.email)
    log_activity(db, action="user_login", user_id=user.id, org_id=user.org_id, meta={"method": "google"}, ip=get_client_ip(request))
    db.commit()

    forwarded_proto = request.headers.get("x-forwarded-proto", "")
    is_https = request.url.scheme == "https" or forwarded_proto.split(",")[0].strip().lower() == "https"
    next_url = request.cookies.get(_OAUTH_NEXT_COOKIE, "/app/search")
    if not next_url.startswith("/") or "//" in next_url:
        next_url = "/app/search"
    response = _Redirect(url=f"{_public_base_url(request)}{next_url}", status_code=302)
    _set_session(response, user.id, is_https=is_https)
    response.delete_cookie(_OAUTH_STATE_COOKIE)
    response.delete_cookie(_OAUTH_NEXT_COOKIE)
    return response


# ---------------------------------------------------------------------------
# LinkedIn OAuth2 (Sign In with LinkedIn using OpenID Connect)
# ---------------------------------------------------------------------------

@router.get("/linkedin/authorize", include_in_schema=False)
async def linkedin_authorize(request: Request, next: str | None = None) -> _Redirect:
    from app.config import settings as _s
    if not _s.linkedin_client_id:
        raise HTTPException(status_code=503, detail="LinkedIn sign-in is not configured")
    state = _secrets.token_urlsafe(32)
    params = _urlencode({
        "response_type": "code",
        "client_id": _s.linkedin_client_id,
        "redirect_uri": _oauth_callback_uri(request, "linkedin"),
        "state": state,
        "scope": "openid profile email",
    })
    forwarded_proto = request.headers.get("x-forwarded-proto", "")
    is_https = request.url.scheme == "https" or forwarded_proto.split(",")[0].strip().lower() == "https"
    response = _Redirect(url=f"https://www.linkedin.com/oauth/v2/authorization?{params}", status_code=302)
    response.set_cookie(_OAUTH_STATE_COOKIE, state, httponly=True, max_age=_OAUTH_STATE_MAX_AGE, samesite="lax", secure=is_https)
    safe_next = next if (next and next.startswith("/") and "//" not in next) else "/app/search"
    response.set_cookie(_OAUTH_NEXT_COOKIE, safe_next, httponly=True, max_age=_OAUTH_STATE_MAX_AGE, samesite="lax", secure=is_https)
    return response


@router.get("/linkedin/callback", include_in_schema=False)
async def linkedin_callback(
    request: Request,
    db: Session = Depends(get_db),
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> _Redirect:
    from app.config import settings as _s
    from app import crud as _crud

    if error:
        logger.info("auth.linkedin_oauth_denied error=%r", error)
        return _Redirect(url=f"{_public_base_url(request)}/login?oauth_error=1", status_code=302)

    stored_state = request.cookies.get(_OAUTH_STATE_COOKIE)
    if not stored_state or not code or not _secrets.compare_digest(stored_state, state or ""):
        raise HTTPException(status_code=400, detail="Invalid OAuth state or missing code")

    try:
        async with _httpx.AsyncClient(timeout=10) as client:
            token_resp = await client.post(
                "https://www.linkedin.com/oauth/v2/accessToken",
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": _oauth_callback_uri(request, "linkedin"),
                    "client_id": _s.linkedin_client_id,
                    "client_secret": _s.linkedin_client_secret,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            token_resp.raise_for_status()
            access_token = token_resp.json()["access_token"]

            userinfo_resp = await client.get(
                "https://api.linkedin.com/v2/userinfo",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            userinfo_resp.raise_for_status()
            userinfo = userinfo_resp.json()
    except Exception:
        logger.exception("auth.linkedin_oauth_exchange_failed")
        raise HTTPException(status_code=502, detail="Failed to complete LinkedIn sign-in. Please try again.")

    email = userinfo.get("email")
    provider_user_id = userinfo.get("sub")
    if not email or not provider_user_id:
        raise HTTPException(status_code=400, detail="LinkedIn account did not provide an email address. Ensure your LinkedIn primary email is set to public.")

    user = _crud.get_or_create_oauth_user(db, provider="linkedin", provider_user_id=provider_user_id, email=email)
    logger.info("auth.linkedin_oauth_ok user_id=%s email=%r", user.id, user.email)
    log_activity(db, action="user_login", user_id=user.id, org_id=user.org_id, meta={"method": "linkedin"}, ip=get_client_ip(request))
    db.commit()

    forwarded_proto = request.headers.get("x-forwarded-proto", "")
    is_https = request.url.scheme == "https" or forwarded_proto.split(",")[0].strip().lower() == "https"
    next_url = request.cookies.get(_OAUTH_NEXT_COOKIE, "/app/search")
    if not next_url.startswith("/") or "//" in next_url:
        next_url = "/app/search"
    response = _Redirect(url=f"{_public_base_url(request)}{next_url}", status_code=302)
    _set_session(response, user.id, is_https=is_https)
    response.delete_cookie(_OAUTH_STATE_COOKIE)
    response.delete_cookie(_OAUTH_NEXT_COOKIE)
    return response


# ---------------------------------------------------------------------------
# Page view tracking
# ---------------------------------------------------------------------------

class _PageViewBody(_BaseModel):
    path: str


@router.post("/page-view", status_code=204, include_in_schema=False)
def record_page_view(
    body: _PageViewBody,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    """Record a frontend page navigation event for the current user."""
    from app.services.notifications.activity import log_activity as _log_activity
    _log_activity(
        db,
        action="page_viewed",
        user_id=current_user.id,
        org_id=current_user.org_id,
        meta={"path": body.path},
        ip=get_client_ip(request),
    )
    db.commit()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _send_verification(db: Session, user: User) -> None:
    token = create_verification_token(user.id)
    try:
        crud.record_verification_sent(db, user)
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        logger.exception("Failed to record verification email sent timestamp")
    try:
        send_verification_email(to=user.email, token=token)
    except Exception as exc:
        logger.exception("Failed to send verification email to %s", user.email)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Email service unavailable. Please try again later.",
        ) from exc
