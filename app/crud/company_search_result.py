"""CRUD for company_search_results — raw Google/ScrapingDog search data.

Global fact, one row per company (upsert), separated from the companies
table per the Company table normalization effort.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.models.company_search_result import CompanySearchResult


def get_search_result(db: Session, company_id: int) -> CompanySearchResult | None:
    return db.get(CompanySearchResult, company_id)


def upsert_search_result(
    db: Session,
    company_id: int,
    *,
    provider: str | None = None,
    results_raw: list[dict] | None = None,
    full_raw: dict | None = None,
    params: dict | None = None,
    searched_at: datetime | None = None,
) -> CompanySearchResult:
    """Insert or update the search-result row for a company. Does not commit."""
    row = db.get(CompanySearchResult, company_id)
    if row is None:
        row = CompanySearchResult(company_id=company_id)
        db.add(row)
    row.provider = provider
    row.results_raw = results_raw
    row.full_raw = full_raw
    row.params = params
    row.searched_at = searched_at
    db.flush()
    return row


def bulk_get_search_results(db: Session, company_ids: list[int]) -> dict[int, CompanySearchResult]:
    """Return {company_id: CompanySearchResult} for the given ids — one query."""
    if not company_ids:
        return {}
    rows = (
        db.query(CompanySearchResult)
        .filter(CompanySearchResult.company_id.in_(company_ids))
        .all()
    )
    return {r.company_id: r for r in rows}
