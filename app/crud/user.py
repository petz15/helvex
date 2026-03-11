import base64
import hashlib

from passlib.context import CryptContext
from sqlalchemy.orm import Session

from app.models.user import User

_pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")


def _prehash(plain: str) -> str:
    """SHA-256 pre-hash before bcrypt to support passwords longer than 72 bytes."""
    return base64.b64encode(hashlib.sha256(plain.encode()).digest()).decode()


def hash_password(plain: str) -> str:
    return _pwd_ctx.hash(_prehash(plain))


def verify_password(plain: str, hashed: str) -> bool:
    return _pwd_ctx.verify(_prehash(plain), hashed)


def get_user(db: Session, user_id: int) -> User | None:
    return db.get(User, user_id)


def get_user_by_username(db: Session, username: str) -> User | None:
    return db.query(User).filter(User.username == username).first()


def list_users(db: Session) -> list[User]:
    return db.query(User).order_by(User.created_at).all()


def count_users(db: Session) -> int:
    return db.query(User).count()


def create_user(db: Session, *, username: str, password: str, is_active: bool = True) -> User:
    user = User(
        username=username,
        hashed_password=hash_password(password),
        is_active=is_active,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def authenticate(db: Session, *, username: str, password: str) -> User | None:
    user = get_user_by_username(db, username)
    if not user or not user.is_active:
        return None
    if not verify_password(password, user.hashed_password):
        return None
    return user
