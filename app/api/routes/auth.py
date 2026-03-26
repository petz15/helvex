"""Auth API routes — login, registration, email verification, password management."""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

from app import crud
from app.auth import (
    COOKIE_NAME,
    check_public_rate_limit,
    create_access_token,
    create_email_change_token,
    create_password_reset_token,
    create_session_cookie,
    create_verification_token,
    decode_email_change_token,
    decode_password_reset_token,
    decode_verification_token,
    get_client_ip,
    get_current_user,
    is_login_allowed,
    record_login_failure,
)
from app.database import get_db
from app.models.user import User
from app.schemas.user import (
    ChangeEmailRequest,
    ChangePasswordRequest,
    RegisterRequest,
    ResetPasswordRequest,
    TokenResponse,
    UserRead,
)
from app.services.email import (
    send_email_change_verification,
    send_password_reset_email,
    send_verification_email,
    send_welcome_email,
)

router = APIRouter(prefix="/auth", tags=["auth"])

_RESEND_COOLDOWN_SECONDS = 60


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
    ip = get_client_ip(request)
    if not is_login_allowed(ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login attempts. Try again in 15 minutes.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user = crud.authenticate(db, email=email, password=password)
    if not user:
        record_login_failure(ip)
        logger.warning("auth.login_failed email=%r ip=%s", email, ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    logger.info("auth.login_ok user_id=%s email=%r ip=%s", user.id, user.email, ip)
    return TokenResponse(access_token=create_access_token(user.id))


# ---------------------------------------------------------------------------
# Cookie-based login / logout (used by the Next.js login page)
# ---------------------------------------------------------------------------

from pydantic import BaseModel as _BaseModel


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
    if not is_login_allowed(ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login attempts. Try again in 15 minutes.",
        )
    user = crud.authenticate(db, email=body.email, password=body.password)
    if not user:
        record_login_failure(ip)
        logger.warning("auth.login_failed email=%r ip=%s", body.email, ip)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    logger.info("auth.login_ok user_id=%s email=%r ip=%s", user.id, user.email, ip)
    forwarded_proto = request.headers.get("x-forwarded-proto", "")
    is_https = request.url.scheme == "https" or forwarded_proto.split(",")[0].strip().lower() == "https"
    response = JSONResponse({"ok": True})
    response.set_cookie(
        key=COOKIE_NAME,
        value=create_session_cookie(user.id),
        httponly=True,
        samesite="lax",
        secure=is_https,
        max_age=8 * 3600,
    )
    return response


@router.post("/logout", status_code=200, summary="Clear the session cookie")
def logout_cookie() -> JSONResponse:
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
    body: _ConfirmEmailChangeRequest,
    db: Session = Depends(get_db),
) -> None:
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
    logger.info("auth.email_changed user_id=%s new_email=%r", user.id, new_email)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

@router.post("/register", response_model=UserRead, status_code=status.HTTP_201_CREATED,
             summary="Create a new user account")
def register(request: Request, body: RegisterRequest, db: Session = Depends(get_db)) -> UserRead:
    ip = get_client_ip(request)
    if not check_public_rate_limit(ip, "register", window=3600, max_requests=10):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many registration attempts. Try again later.",
        )
    if crud.get_user_by_email(db, body.email):
        logger.warning("auth.register_conflict field=email ip=%s", ip)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")
    user = crud.create_user(db, email=body.email, password=body.password)
    logger.info("auth.register_ok user_id=%s email=%r ip=%s", user.id, user.email, ip)
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
        try:
            send_welcome_email(to=user.email)
        except Exception:
            pass
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
    if current_user.org_id:
        from sqlalchemy.orm import joinedload
        current_user = (
            db.query(User)
            .options(joinedload(User.org))
            .filter(User.id == current_user.id)
            .first()
        )
    return UserRead.model_validate(current_user)


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
