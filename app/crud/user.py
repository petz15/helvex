import base64
import hashlib
from datetime import datetime, timezone

import bcrypt
from sqlalchemy.orm import Session

from app.models.user import User


def _prehash(plain: str) -> bytes:
    """SHA-256 pre-hash before bcrypt to support passwords longer than 72 bytes."""
    return base64.b64encode(hashlib.sha256(plain.encode()).digest())


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(_prehash(plain), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(_prehash(plain), hashed.encode())


def get_user(db: Session, user_id: int) -> User | None:
    return db.get(User, user_id)


def get_user_by_username(db: Session, username: str) -> User | None:
    return db.query(User).filter(User.username == username).first()


def list_users(db: Session) -> list[User]:
    return db.query(User).order_by(User.created_at).all()


def count_users(db: Session) -> int:
    return db.query(User).count()


def create_user(
    db: Session,
    *,
    username: str,
    password: str,
    is_active: bool = True,
    email: str | None = None,
    tier: str = "free",
    is_superadmin: bool = False,
) -> User:
    user = User(
        username=username,
        hashed_password=hash_password(password),
        is_active=is_active,
        email=email,
        tier=tier,
        is_superadmin=is_superadmin,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def get_user_by_email(db: Session, email: str) -> User | None:
    return db.query(User).filter(User.email == email).first()


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


def record_verification_sent(db: Session, user: User) -> None:
    user.email_verification_sent_at = datetime.now(tz=timezone.utc)
    db.commit()


def authenticate(db: Session, *, username: str, password: str) -> User | None:
    user = get_user_by_username(db, username)
    if not user or not user.is_active:
        return None
    if not verify_password(password, user.hashed_password):
        return None
    return user
