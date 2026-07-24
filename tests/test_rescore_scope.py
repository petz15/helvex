"""Scoring & multi-tenancy rework — config resolution + rescore_scope materialization."""
from app.crud import company_score as score_crud
from app.crud import org_company_ai as ai_crud
from app.crud.user_org_setting import set_user_org_setting
from app.models.company import Company
from app.models.organization import Organization
from app.models.user import User
from app.services.scoring.config_resolution import effective_config, resolve_scope
from app.services.scoring.rescore_scope import rescore_scope


def _seed_org_and_user(db, org_id: int, user_id: int) -> tuple[Organization, User]:
    org = Organization(id=org_id, name=f"Org {org_id}", slug=f"org-{org_id}", tier=1)
    db.add(org)
    user = User(id=user_id, email=f"u{user_id}@test.ch", org_id=org_id, hashed_password="x")
    db.add(user)
    db.commit()
    return org, user


def test_resolve_scope_defaults_to_org_scope_without_overrides(db):
    _seed_org_and_user(db, 101, 201)
    assert resolve_scope(db, org_id=101, user_id=201) is None


def test_resolve_scope_returns_user_id_once_they_override(db):
    _seed_org_and_user(db, 102, 202)
    set_user_org_setting(db, 202, 102, "scoring_target_keywords", "beratung")
    assert resolve_scope(db, org_id=102, user_id=202) == 202


def test_effective_config_layers_user_override_over_org_default(db):
    _seed_org_and_user(db, 103, 203)
    from app.crud.app_setting import set_org_setting
    set_org_setting(db, 103, "scoring_target_keywords", "org-default")
    set_user_org_setting(db, 203, 103, "scoring_target_keywords", "user-override")

    org_config = effective_config(db, org_id=103, user_id=None)
    user_config = effective_config(db, org_id=103, user_id=203)

    assert org_config["scoring_target_keywords"] == "org-default"
    assert user_config["scoring_target_keywords"] == "user-override"


def test_rescore_scope_materializes_org_default_scores(db):
    org, _ = _seed_org_and_user(db, 104, 204)
    db.add(Company(id=1040, uid="CHE-104.000.000", name="Alpha AG", legal_form="AG", status="active", web_score=50))
    db.add(Company(id=1041, uid="CHE-104.000.001", name="Beta GmbH", legal_form="GmbH", status="active", web_score=80))
    db.commit()
    ai_crud.upsert_org_ai(db, org_id=104, company_id=1040, ai_score=70, ai_data={"ai_category": "lead"})
    db.commit()

    stats = rescore_scope(db, org_id=104, user_id=None)
    assert stats["updated"] == 2
    assert stats["errors"] == []

    row1040 = score_crud.get_score(db, org_id=104, user_id=None, company_id=1040)
    row1041 = score_crud.get_score(db, org_id=104, user_id=None, company_id=1041)
    assert row1040 is not None and row1041 is not None
    assert row1040.web_score == 50
    assert row1041.web_score == 80
    # ai_score only exists for 1040 -> its combined_score should be computable and non-None
    assert row1040.combined_score is not None


def test_rescore_scope_user_scope_is_independent_of_org_default(db):
    org, _ = _seed_org_and_user(db, 105, 205)
    set_user_org_setting(db, 205, 105, "scoring_target_keywords", "consulting")
    db.add(Company(
        id=1050, uid="CHE-105.000.000", name="Consulting AG", legal_form="AG", status="active",
        purpose_keywords="consulting,advisory",
    ))
    db.commit()

    rescore_scope(db, org_id=105, user_id=None)
    rescore_scope(db, org_id=105, user_id=205)

    org_row = score_crud.get_score(db, org_id=105, user_id=None, company_id=1050)
    user_row = score_crud.get_score(db, org_id=105, user_id=205, company_id=1050)
    assert org_row is not None
    assert user_row is not None
    # Both exist independently — the point of per-scope materialization.
    assert score_crud.scope_exists(db, org_id=105, user_id=None)
    assert score_crud.scope_exists(db, org_id=105, user_id=205)
