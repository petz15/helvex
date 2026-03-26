"""CRUD operations for org_company_state overlay."""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.org_company_state import OrgCompanyState


def get_org_company_state(db: Session, *, org_id: int, company_id: int) -> OrgCompanyState | None:
    return (
        db.query(OrgCompanyState)
        .filter(OrgCompanyState.org_id == org_id, OrgCompanyState.company_id == company_id)
        .first()
    )


def get_or_create_org_company_state(db: Session, *, org_id: int, company_id: int) -> OrgCompanyState:
    row = get_org_company_state(db, org_id=org_id, company_id=company_id)
    if row is None:
        row = OrgCompanyState(org_id=org_id, company_id=company_id)
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def update_org_company_state(
    db: Session,
    row: OrgCompanyState,
    *,
    tags: str | None = ...,
    review_status: str | None = ...,
    proposal_status: str | None = ...,
    contact_name: str | None = ...,
    contact_email: str | None = ...,
    contact_phone: str | None = ...,
) -> OrgCompanyState:
    """Update only the fields that were explicitly provided (not sentinel ...)."""
    if tags is not ...:
        row.tags = tags
    if review_status is not ...:
        row.review_status = review_status
    if proposal_status is not ...:
        row.proposal_status = proposal_status
    if contact_name is not ...:
        row.contact_name = contact_name
    if contact_email is not ...:
        row.contact_email = contact_email
    if contact_phone is not ...:
        row.contact_phone = contact_phone
    db.commit()
    db.refresh(row)
    return row


def update_org_google_results(
    db: Session,
    *,
    org_id: int,
    company_id: int,
    website_url: str | None,
    website_match_score: float | None,
    google_search_results_raw: str | None,
    website_checked_at,
    social_media_only: bool | None,
) -> OrgCompanyState:
    """Upsert Google scoring results into the org overlay. Called by workers."""
    row = get_or_create_org_company_state(db, org_id=org_id, company_id=company_id)
    row.website_url = website_url
    row.website_match_score = website_match_score
    row.google_search_results_raw = google_search_results_raw
    row.website_checked_at = website_checked_at
    row.social_media_only = social_media_only
    db.commit()
    db.refresh(row)
    return row
