"""Per-user per-org settings — generic key/value store.

Used today for notification prefs (workspace/_shared.py) and now also for
per-user `scoring_*` overrides (scoring/config_resolution.py). A user's row
for a given key only exists if they've explicitly overridden it; absence
means "inherit the org/global default".
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.user_org_setting import UserOrgSetting


def get_user_org_setting(db: Session, user_id: int, org_id: int, key: str) -> str | None:
    row = (
        db.query(UserOrgSetting)
        .filter(UserOrgSetting.user_id == user_id, UserOrgSetting.org_id == org_id, UserOrgSetting.key == key)
        .first()
    )
    return row.value if row else None


def get_user_org_settings_batch(db: Session, user_id: int, org_id: int, keys: list[str]) -> dict[str, str]:
    if not keys:
        return {}
    rows = (
        db.query(UserOrgSetting)
        .filter(
            UserOrgSetting.user_id == user_id,
            UserOrgSetting.org_id == org_id,
            UserOrgSetting.key.in_(keys),
        )
        .all()
    )
    return {r.key: r.value for r in rows if r.value is not None}


def set_user_org_setting(db: Session, user_id: int, org_id: int, key: str, value: str) -> None:
    row = (
        db.query(UserOrgSetting)
        .filter(UserOrgSetting.user_id == user_id, UserOrgSetting.org_id == org_id, UserOrgSetting.key == key)
        .first()
    )
    if row is None:
        db.add(UserOrgSetting(user_id=user_id, org_id=org_id, key=key, value=value))
    else:
        row.value = value
    db.commit()


def user_has_any_setting(db: Session, user_id: int, org_id: int, keys: list[str]) -> bool:
    """True if the user has overridden at least one of `keys` for this org."""
    if not keys:
        return False
    return (
        db.query(UserOrgSetting.id)
        .filter(
            UserOrgSetting.user_id == user_id,
            UserOrgSetting.org_id == org_id,
            UserOrgSetting.key.in_(keys),
        )
        .first()
        is not None
    )
