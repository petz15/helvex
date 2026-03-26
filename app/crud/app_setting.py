from sqlalchemy.orm import Session

from app.models.app_setting import AppSetting
from app.models.org_setting import OrgSetting


def get_setting(db: Session, key: str, default: str = "") -> str:
    row = db.get(AppSetting, key)
    return row.value if row is not None else default


def get_effective_setting(db: Session, key: str, *, org_id: int | None = None, default: str = "") -> str:
    """Return org-specific override if present, else global setting, else default."""
    if org_id is not None:
        org_row = (
            db.query(OrgSetting)
            .filter(OrgSetting.org_id == org_id, OrgSetting.key == key)
            .first()
        )
        if org_row is not None and org_row.value is not None:
            return org_row.value
    return get_setting(db, key, default)


def set_setting(db: Session, key: str, value: str) -> None:
    row = db.get(AppSetting, key)
    if row is None:
        db.add(AppSetting(key=key, value=value))
    else:
        row.value = value
    db.commit()


def get_all_settings(db: Session) -> dict[str, str]:
    return {row.key: row.value for row in db.query(AppSetting).all()}


def seed_defaults(db: Session, defaults: dict[str, str]) -> None:
    """Write each default only if the key does not already exist."""
    for key, value in defaults.items():
        if db.get(AppSetting, key) is None:
            db.add(AppSetting(key=key, value=value))
    db.commit()
