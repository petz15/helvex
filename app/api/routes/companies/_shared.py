"""Shared constants, helpers, and imports for companies routes."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models.company import Company
from app.models.org_company_state import OrgCompanyState
from app.models.organization import Organization
from app.schemas.company import CompanyRead
from app.services.billing.tiers import get_web_results_privacy_months, normalize_tier, TIER_RANK

# Fields whose values are org-specific and live in OrgCompanyState, not Company.
_ORG_FIELDS = frozenset({
    "review_status", "contact_status",
    "contact_name", "contact_email", "contact_phone",
    "tags",
})

# NOGA hierarchy cache: {org_id: hierarchy}. Cache is cleared on company changes.
_noga_hierarchy_cache: dict[int | None, list] = {}


def _clear_noga_cache(org_id: int | None = None) -> None:
    if org_id is None:
        _noga_hierarchy_cache.clear()
    else:
        _noga_hierarchy_cache.pop(org_id, None)


def _overlay(
    company: Company,
    org_state: OrgCompanyState | None,
) -> CompanyRead:
    """Build CompanyRead, overlaying org-specific workflow fields from OrgCompanyState."""
    base = CompanyRead.model_validate(company)
    overrides: dict = {}
    if org_state is not None:
        overrides.update({f: getattr(org_state, f) for f in _ORG_FIELDS if getattr(org_state, f) is not None})
    return base.model_copy(update=overrides) if overrides else base


def _bulk_org_states(db: Session, company_ids: list[int], org_id: int | None) -> dict[int, OrgCompanyState]:
    """Return a {company_id: OrgCompanyState} map for the given org."""
    if not org_id or not company_ids:
        return {}
    rows = db.query(OrgCompanyState).filter(
        OrgCompanyState.org_id == org_id,
        OrgCompanyState.company_id.in_(company_ids),
    ).all()
    return {r.company_id: r for r in rows}


_WEB_RESULT_FIELDS = (
    "website_url", "web_score", "google_search_results_raw",
    "website_status", "website_count",
)


def _apply_web_results_gate(company: CompanyRead, org: Organization | None, is_superadmin: bool) -> CompanyRead:
    """Mask web-search result fields based on the org's tier and the privacy window."""
    if is_superadmin:
        return company
    if org is None:
        return company.model_copy(update={f: None for f in _WEB_RESULT_FIELDS})

    tier = normalize_tier(org.tier)
    rank = TIER_RANK.get(tier, 0)

    if rank == 0:
        return company.model_copy(update={f: None for f in _WEB_RESULT_FIELDS})

    if rank >= 2:
        return company

    privacy_months = get_web_results_privacy_months(org)
    if privacy_months > 0 and company.website_checked_at is not None:
        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=privacy_months * 30)
        checked_at = company.website_checked_at
        if checked_at.tzinfo is None:
            checked_at = checked_at.replace(tzinfo=timezone.utc)
        if checked_at > cutoff:
            return company.model_copy(update={f: None for f in _WEB_RESULT_FIELDS})

    return company
