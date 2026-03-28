import base64
import hashlib
from datetime import datetime, timezone

import bcrypt
from sqlalchemy.orm import Session

from app.models.oauth_account import OAuthAccount
from app.models.user import User


def _prehash(plain: str) -> bytes:
    """SHA-256 pre-hash before bcrypt to support passwords longer than 72 bytes."""
    return base64.b64encode(hashlib.sha256(plain.encode()).digest())


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(_prehash(plain), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str | None) -> bool:
    if hashed is None:
        return False  # OAuth-only user — no password set
    return bcrypt.checkpw(_prehash(plain), hashed.encode())


def get_user(db: Session, user_id: int) -> User | None:
    return db.get(User, user_id)


def get_user_by_email(db: Session, email: str) -> User | None:
    return db.query(User).filter(User.email == email).first()


def list_users(db: Session) -> list[User]:
    return db.query(User).order_by(User.created_at).all()


def count_users(db: Session) -> int:
    return db.query(User).count()


def create_user(
    db: Session,
    *,
    email: str,
    password: str,
    is_active: bool = True,
    tier: str = "free",
    is_superadmin: bool = False,
) -> User:
    user = User(
        email=email,
        hashed_password=hash_password(password),
        is_active=is_active,
        tier=tier,
        is_superadmin=is_superadmin,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def get_or_create_oauth_user(
    db: Session,
    *,
    provider: str,
    provider_user_id: str,
    email: str,
) -> User:
    """Return the User linked to this OAuth identity, creating or linking as needed.

    Strategy (auto-link by email):
    1. Existing OAuthAccount for (provider, provider_user_id) → return linked user.
    2. Existing User with matching email → attach new OAuthAccount and return user.
    3. Otherwise → create new User (no password, email pre-verified) + OAuthAccount.
    """
    # 1. Known OAuth identity
    existing_oauth = (
        db.query(OAuthAccount)
        .filter(OAuthAccount.provider == provider, OAuthAccount.provider_user_id == provider_user_id)
        .first()
    )
    if existing_oauth:
        return existing_oauth.user

    # 2. Email already registered — link the OAuth identity to the existing account
    user = get_user_by_email(db, email)
    if user:
        oauth = OAuthAccount(provider=provider, provider_user_id=provider_user_id, user_id=user.id)
        db.add(oauth)
        db.commit()
        return user

    # 3. Brand new user — create account with no password, email already verified by provider
    user = User(
        email=email,
        hashed_password=None,
        is_active=True,
        email_verified=True,
    )
    db.add(user)
    db.flush()  # populate user.id before creating OAuthAccount
    oauth = OAuthAccount(provider=provider, provider_user_id=provider_user_id, user_id=user.id)
    db.add(oauth)
    db.commit()
    db.refresh(user)
    return user


def mark_email_verified(db: Session, user: User) -> User:
    user.email_verified = True
    db.commit()
    db.refresh(user)
    return user


def update_password(db: Session, user: User, new_password: str) -> User:
    user.hashed_password = hash_password(new_password)
    db.commit()
    db.refresh(user)
    return user


def update_email(db: Session, user: User, new_email: str) -> User:
    user.email = new_email
    db.commit()
    db.refresh(user)
    return user


def record_verification_sent(db: Session, user: User) -> None:
    user.email_verification_sent_at = datetime.now(tz=timezone.utc)
    db.commit()


def authenticate(db: Session, *, email: str, password: str) -> User | None:
    user = get_user_by_email(db, email)
    if not user or not user.is_active:
        return None
    if not verify_password(password, user.hashed_password):
        return None
    return user
