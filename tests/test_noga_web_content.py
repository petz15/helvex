"""NOGA classification is supplemented with crawled website content
(about_text + service titles) — web-pipeline holistic rework, Layer C.
Helps companies with thin/boilerplate Zefix purpose text (ROADMAP "NOGA v2"
pain points). See app.services.ml.noga._web_content_text.
"""
from datetime import datetime, timezone

from app.models.company import Company
from app.models.company_url_candidate import CompanyUrlCandidate
from app.models.company_web_extract import CompanyWebExtract
from app.services.ml.noga import _web_content_text


def _seed_extract(db, *, company_id: int, about_text: str | None, services_struct: list | None, confidence: float = 0.8):
    db.add(Company(id=company_id, uid=f"CHE-{company_id}.000.000", name="Muster AG"))
    candidate = CompanyUrlCandidate(company_id=company_id, url="https://muster.ch", status="selected")
    db.add(candidate)
    db.flush()
    db.add(CompanyWebExtract(
        company_id=company_id,
        url_candidate_id=candidate.id,
        about_text=about_text,
        services_struct=services_struct,
        confidence=confidence,
        extracted_at=datetime.now(timezone.utc),
    ))
    db.commit()


def test_web_content_text_combines_about_and_service_titles(db):
    _seed_extract(
        db, company_id=9001,
        about_text="Wir sind ein Schweizer Anbieter fuer Abwasserreinigungsanlagen.",
        services_struct=[
            {"title": "Anlagenbau", "summary": "..."},
            {"title": "Wartung", "summary": "..."},
        ],
    )
    text = _web_content_text(db, 9001)
    assert text is not None
    assert "Abwasserreinigungsanlagen" in text
    assert "Anlagenbau" in text
    assert "Wartung" in text


def test_web_content_text_none_when_no_extract(db):
    db.add(Company(id=9002, uid="CHE-9002.000.000", name="No Site AG"))
    db.commit()
    assert _web_content_text(db, 9002) is None


def test_web_content_text_none_when_extract_has_no_content(db):
    _seed_extract(db, company_id=9003, about_text=None, services_struct=None)
    assert _web_content_text(db, 9003) is None


def test_web_content_text_respects_max_chars(db):
    _seed_extract(db, company_id=9004, about_text="A" * 2000, services_struct=None)
    text = _web_content_text(db, 9004, max_chars=100)
    assert text is not None
    assert len(text) <= 100
