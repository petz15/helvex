import base64
import hashlib
import re
from datetime import datetime, timezone

import bcrypt
from sqlalchemy.orm import Session

from app.models.oauth_account import OAuthAccount
from app.models.org_member import OrgMember
from app.models.organization import Organization
from app.models.user import User


def _slugify(name: str) -> str:
    slug = name.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-") or "workspace"


def _unique_slug(db: Session, base: str) -> str:
    slug = base
    n = 1
    while db.query(Organization).filter(Organization.slug == slug).first():
        slug = f"{base}-{n}"
        n += 1
    return slug


_WELCOME_CREDITS = 1_000


def ensure_personal_org(db: Session, user: User) -> None:
    """Create and assign a personal workspace org if the user has none.

    Also creates the OrgMember row so the new org_members table stays in sync.
    New orgs receive 1,000 welcome credits.
    Safe to call multiple times — no-ops if user already has an org.
    """
    if user.org_id is not None:
        return
    email_prefix = user.email.split("@")[0]
    base = _slugify(email_prefix) or "workspace"
    slug = _unique_slug(db, base)
    org = Organization(
        name=f"{email_prefix}'s workspace",
        slug=slug,
        credits_unlimited=user.is_superadmin,
    )
    db.add(org)
    db.flush()
    user.org_id = org.id
    user.org_role = "owner"
    # Keep org_members in sync — upsert so this is idempotent
    existing = db.query(OrgMember).filter(OrgMember.org_id == org.id, OrgMember.user_id == user.id).first()
    if not existing:
        db.add(OrgMember(org_id=org.id, user_id=user.id, role="owner"))
    db.commit()
    db.refresh(user)
    # Grant welcome credits (skip for unlimited orgs — they don't need a balance)
    if not user.is_superadmin:
        try:
            from app.services.credits import grant_credits
            grant_credits(
                db,
                org_id=org.id,
                amount=_WELCOME_CREDITS,
                tx_type="grant",
                reference_id="welcome_bonus",
            )
        except Exception:
            import logging as _logging
            _logging.getLogger(__name__).exception(
                "ensure_personal_org: failed to grant welcome credits org_id=%s", org.id
            )


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
    is_superadmin: bool = False,
) -> User:
    user = User(
        email=email,
        hashed_password=hash_password(password),
        is_active=is_active,
        is_superadmin=is_superadmin,
    )
    db.add(user)
    db.flush()
    ensure_personal_org(db, user)
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
    ensure_personal_org(db, user)
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
