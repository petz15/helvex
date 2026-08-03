"""CRUD for org_company_ai — org-shared AI classification results."""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.org_company_ai import OrgCompanyAi


def bulk_get_org_ai(db: Session, *, org_id: int, company_ids: list[int]) -> dict[int, OrgCompanyAi]:
    if not company_ids:
        return {}
    rows = (
        db.query(OrgCompanyAi)
        .filter(OrgCompanyAi.org_id == org_id, OrgCompanyAi.company_id.in_(company_ids))
        .all()
    )
    return {r.company_id: r for r in rows}


def upsert_org_ai(
    db: Session, *, org_id: int, company_id: int, ai_score: int | None, ai_data: dict | None
) -> OrgCompanyAi:
    """Insert or update one company's org-shared AI result. Does not commit."""
    row = db.get(OrgCompanyAi, (org_id, company_id))
    if row is None:
        row = OrgCompanyAi(org_id=org_id, company_id=company_id)
        db.add(row)
    row.ai_score = ai_score
    row.ai_data = ai_data
    db.flush()
    return row
