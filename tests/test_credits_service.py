from app.models.org_credit_transaction import OrgCreditTransaction
from app.models.organization import Organization
from app.services.billing.credits import check_and_deduct


def _make_org(db, *, tier: str, credits: int) -> Organization:
    org = Organization(name=f"{tier}-org", slug=f"{tier}-org", tier=tier, credits_balance=credits)
    db.add(org)
    db.commit()
    db.refresh(org)
    return org


def test_simple_tier_flex_rescore_deducts_cost(db):
    org = _make_org(db, tier="simple", credits=100)

    ok = check_and_deduct(db, org.id, action="flex_rescore", count=50, reference_id="t1")
    assert ok is True

    db.refresh(org)
    assert org.credits_balance == 50  # 100 - 50×1 credit/company

    txs = db.query(OrgCreditTransaction).filter(OrgCreditTransaction.org_id == org.id).all()
    assert len(txs) == 1
    assert txs[0].amount == -50
    assert txs[0].action_type == "flex_rescore"


def test_explorer_tier_flex_rescore_is_free(db):
    org = _make_org(db, tier="explorer", credits=100)

    ok = check_and_deduct(db, org.id, action="flex_rescore", count=50, reference_id="t1")
    assert ok is True

    db.refresh(org)
    assert org.credits_balance == 100  # explorer+ flex rescore is free

    txs = db.query(OrgCreditTransaction).filter(OrgCreditTransaction.org_id == org.id).all()
    assert len(txs) == 0  # no ledger entry for free entitlement


def test_insufficient_balance_returns_false(db):
    org = _make_org(db, tier="free", credits=10)

    ok = check_and_deduct(db, org.id, action="immediate_llm", count=1, reference_id="t3")
    assert ok is False

    db.refresh(org)
    assert org.credits_balance == 10
