"""Auth API routes — login, registration, email verification, password management."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from sqlalchemy.orm import Session

from app import crud
from app.auth import (
    check_public_rate_limit,
    create_access_token,
    create_password_reset_token,
    create_verification_token,
    decode_password_reset_token,
    decode_verification_token,
    get_current_user,
    is_login_allowed,
    record_login_failure,
)
from app.database import get_db
from app.models.user import User
from app.schemas.user import ChangePasswordRequest, RegisterRequest, ResetPasswordRequest, TokenResponse, UserRead
from app.services.email import send_password_reset_email, send_verification_email, send_welcome_email

router = APIRouter(prefix="/auth", tags=["auth"])

_RESEND_COOLDOWN_SECONDS = 60


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

@router.post("/token", response_model=TokenResponse, summary="Obtain a JWT Bearer token")
def login_for_token(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
) -> TokenResponse:
    ip = request.client.host if request.client else "unknown"
    if not is_login_allowed(ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login attempts. Try again in 15 minutes.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user = crud.authenticate(db, username=username, password=password)
    if not user:
        record_login_failure(ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return TokenResponse(access_token=create_access_token(user.id))


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

@router.post("/register", response_model=UserRead, status_code=status.HTTP_201_CREATED,
             summary="Create a new user account")
def register(request: Request, body: RegisterRequest, db: Session = Depends(get_db)) -> UserRead:
    ip = request.client.host if request.client else "unknown"
    if not check_public_rate_limit(ip, "register", window=3600, max_requests=10):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many registration attempts. Try again later.",
        )
    if crud.get_user_by_username(db, body.username):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Username already taken")
    if crud.get_user_by_email(db, body.email):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")
    user = crud.create_user(db, username=body.username, password=body.password, email=body.email)
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
    if not current_user.email:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No email address on file")
    last_sent = current_user.email_verification_sent_at
    if last_sent:
        elapsed = (datetime.now(tz=timezone.utc) - last_sent).total_seconds()
        if elapsed < _RESEND_COOLDOWN_SECONDS:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Please wait {int(_RESEND_COOLDOWN_SECONDS - elapsed)}s before requesting another email",
            )
    _send_verification(db, current_user)


@router.get("/verify-email", response_model=UserRead, summary="Verify email via signed token")
def verify_email(token: str, db: Session = Depends(get_db)) -> UserRead:
    user_id = decode_verification_token(token)
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired verification link. Please request a new one.",
        )
    user = crud.get_user(db, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if not user.email_verified:
        user = crud.mark_email_verified(db, user)
        if user.email:
            try:
                send_welcome_email(to=user.email, username=user.username)
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
# Forgot / reset password (public)
# ---------------------------------------------------------------------------

@router.post("/forgot-password", status_code=status.HTTP_204_NO_CONTENT,
             summary="Request a password reset email")
def forgot_password(request: Request, email: str = Form(...), db: Session = Depends(get_db)) -> None:
    ip = request.client.host if request.client else "unknown"
    if not check_public_rate_limit(ip, "forgot-password", window=900, max_requests=5):
        # Return 204 even on rate limit to avoid confirming anything
        return
    # Always return 204 — never reveal whether an email is registered
    user = crud.get_user_by_email(db, email)
    if user and user.email:
        token = create_password_reset_token(user.id)
        try:
            send_password_reset_email(to=user.email, username=user.username, token=token)
        except Exception:
            pass  # swallow to avoid leaking whether the email exists


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
def get_me(current_user: User = Depends(get_current_user)) -> UserRead:
    return UserRead.model_validate(current_user)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _send_verification(db: Session, user: User) -> None:
    import logging
    _log = logging.getLogger(__name__)
    token = create_verification_token(user.id)
    crud.record_verification_sent(db, user)
    if user.email:
        try:
            send_verification_email(to=user.email, username=user.username, token=token)
        except Exception:
            _log.exception("Failed to send verification email to %s", user.email)
