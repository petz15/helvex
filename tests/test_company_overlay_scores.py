"""_overlay() merges the resolved-scope CompanyScore when materialized, and
falls back to the global Company columns otherwise (scoring/multi-tenancy
rework, phase 4 — read-path cutover, detail + list views)."""
from app.api.routes.companies._shared import _bulk_scores, _overlay
from app.crud import company_score as score_crud
from app.models.company import Company
from app.models.organization import Organization
from app.models.user import User


def _seed(db, org_id: int, user_id: int, company_id: int) -> Company:
    db.add(Organization(id=org_id, name=f"Org {org_id}", slug=f"org-{org_id}", tier=1))
    db.add(User(id=user_id, email=f"u{user_id}@test.ch", org_id=org_id, hashed_password="x"))
    company = Company(
        id=company_id, uid=f"CHE-{company_id}.000.000", name="Test AG",
        flex_score=40, web_score=40, combined_score=40.0,
    )
    db.add(company)
    db.commit()
    return company


def test_overlay_falls_back_to_global_scores_without_materialized_scope(db):
    company = _seed(db, 301, 401, 3010)
    result = _overlay(company, None, None, None)
    assert result.flex_score == 40
    assert result.combined_score == 40.0


def test_overlay_uses_materialized_scope_score_when_present(db):
    company = _seed(db, 302, 402, 3020)
    score_crud.upsert_score(
        db, org_id=302, user_id=None, company_id=3020,
        flex_score=90, web_score=85, combined_score=88.5,
    )
    db.commit()

    scoped = _bulk_scores(db, [3020], 302, 402).get(3020)
    result = _overlay(company, None, None, scoped)

    assert result.flex_score == 90
    assert result.web_score == 85
    assert result.combined_score == 88.5
    # Confirms the overlay actually overrides — not coincidentally equal to the
    # Company column defaults seeded above (40/40/40.0).
    assert result.flex_score != company.flex_score


def test_bulk_scores_empty_without_org_context(db):
    _seed(db, 303, 403, 3030)
    assert _bulk_scores(db, [3030], None, None) == {}
