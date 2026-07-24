"""Shared constants, helpers, and imports for companies routes."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models.company import Company
from app.models.company_score import CompanyScore
from app.models.company_search_result import CompanySearchResult
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
    search_result: CompanySearchResult | None = None,
    score: CompanyScore | None = None,
) -> CompanyRead:
    """Build CompanyRead, overlaying org-specific workflow fields from OrgCompanyState,
    global search-result facts from CompanySearchResult, and (once materialized) the
    resolved-scope scores from CompanyScore — see scoring/config_resolution.py and
    docs/code-review/scoring-multitenancy-rework.md. `score` is None (falls back to
    the global Company columns, unchanged) until `rescore_scope` has run for that
    scope; the migration backfill seeds an org-default row for every org from the
    then-current global values, so this overlay is a no-op divergence until an org
    customizes its scoring_* config and reruns rescore_scope."""
    base = CompanyRead.model_validate(company)
    overrides: dict = {}
    if org_state is not None:
        overrides.update({f: getattr(org_state, f) for f in _ORG_FIELDS if getattr(org_state, f) is not None})
    if search_result is not None:
        overrides["website_checked_at"] = search_result.searched_at
        overrides["google_search_results_raw"] = search_result.results_raw
    if score is not None:
        overrides["flex_score"] = score.flex_score
        overrides["web_score"] = score.web_score
        overrides["combined_score"] = score.combined_score
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


def _bulk_search_results(db: Session, company_ids: list[int]) -> dict[int, CompanySearchResult]:
    """Return a {company_id: CompanySearchResult} map — one query, no org scoping
    (search results are a global fact, shared across every org)."""
    if not company_ids:
        return {}
    rows = db.query(CompanySearchResult).filter(CompanySearchResult.company_id.in_(company_ids)).all()
    return {r.company_id: r for r in rows}


def _bulk_scores(
    db: Session, company_ids: list[int], org_id: int | None, user_id: int | None
) -> dict[int, CompanyScore]:
    """Return {company_id: CompanyScore} for the resolved (org_id, user_id) scope —
    one query. Empty when there's no org context (superadmin/no-org reads keep
    reading the global Company columns, same as before this rework)."""
    if not org_id or not company_ids:
        return {}
    from app.services.scoring.config_resolution import resolve_scope
    scope_user_id = resolve_scope(db, org_id=org_id, user_id=user_id) if user_id else None
    rows = (
        db.query(CompanyScore)
        .filter(
            CompanyScore.org_id == org_id,
            CompanyScore.user_id == scope_user_id,
            CompanyScore.company_id.in_(company_ids),
        )
        .all()
    )
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
