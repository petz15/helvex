"""Scoring config resolution — org default + per-user override.

Scoring/multi-tenancy rework: a user's effective scoring_* config is the org's
settings (which themselves fall back through base-org → global AppSetting,
via crud.app_setting.get_effective_settings_batch) with any of that user's own
UserOrgSetting overrides layered on top. AI is never overridden per-user — it's
always read from org_company_ai.

resolve_scope() picks which company_score row a request should read/write:
`user_id` if that user has any scoring_* override recorded for this org, else
`None` (the org-default row). This keeps every read to a single indexed
(org_id, user_id) lookup — no per-row COALESCE across scopes.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.crud.app_setting import get_effective_settings_batch
from app.crud.user_org_setting import get_user_org_settings_batch, user_has_any_setting
from app.services.scoring.scoring import _DEFAULT_SCORING_CONFIG

SCORING_KEYS: list[str] = list(_DEFAULT_SCORING_CONFIG.keys())


def effective_config(db: Session, *, org_id: int, user_id: int | None = None) -> dict[str, str]:
    """Org-default scoring_* config, with the user's own overrides layered on top."""
    config = get_effective_settings_batch(db, SCORING_KEYS, org_id=org_id)
    if user_id is not None:
        config.update(get_user_org_settings_batch(db, user_id, org_id, SCORING_KEYS))
    return config


def resolve_scope(db: Session, *, org_id: int, user_id: int | None) -> int | None:
    """Return the user_id to scope a company_score read/write to, or None for
    the org-default scope. A user only gets their own materialized scope once
    they've overridden at least one scoring_* key for this org."""
    if user_id is None:
        return None
    if user_has_any_setting(db, user_id, org_id, SCORING_KEYS):
        return user_id
    return None
