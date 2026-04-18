from sqlalchemy.orm import Session

from app.models.app_setting import AppSetting
from app.models.org_setting import OrgSetting


def get_setting(db: Session, key: str, default: str = "") -> str:
    row = db.get(AppSetting, key)
    return row.value if row is not None else default


_BASE_ORG_ID = 1  # Org 1 acts as the global default — its settings serve as the fallback
                  # for all other orgs that have not configured their own overrides.


def get_effective_setting(db: Session, key: str, *, org_id: int | None = None, default: str = "") -> str:
    """Return the most specific setting value using a three-tier fallback:

    1. Org-specific OrgSetting (if org_id provided and value set)
    2. Base-org (org_id=1) OrgSetting — global defaults configured by the platform admin
    3. Global AppSetting
    4. Hard-coded default
    """
    if org_id is not None:
        org_row = (
            db.query(OrgSetting)
            .filter(OrgSetting.org_id == org_id, OrgSetting.key == key)
            .first()
        )
        if org_row is not None and org_row.value is not None:
            return org_row.value

    # Fall back to base org (org 1) if the requesting org is not org 1 itself
    if org_id != _BASE_ORG_ID:
        base_row = (
            db.query(OrgSetting)
            .filter(OrgSetting.org_id == _BASE_ORG_ID, OrgSetting.key == key)
            .first()
        )
        if base_row is not None and base_row.value is not None:
            return base_row.value

    return get_setting(db, key, default)


def set_setting(db: Session, key: str, value: str) -> None:
    row = db.get(AppSetting, key)
    if row is None:
        db.add(AppSetting(key=key, value=value))
    else:
        row.value = value
    db.commit()


def set_org_setting(db: Session, org_id: int, key: str, value: str) -> None:
    """Upsert a per-org setting override."""
    row = db.query(OrgSetting).filter(OrgSetting.org_id == org_id, OrgSetting.key == key).first()
    if row is None:
        db.add(OrgSetting(org_id=org_id, key=key, value=value))
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
