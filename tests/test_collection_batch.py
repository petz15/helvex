from datetime import datetime, timezone

from app.config import settings
from app.models.company import Company
from app.services.collection import run_batch_collect


def _create_company(db, *, uid: str, name: str, purpose_keywords: str | None = None, website_url: str | None = None):
    c = Company(uid=uid, name=name)
    c.purpose_keywords = purpose_keywords
    c.website_url = website_url
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def test_run_batch_collect_purpose_keywords_filter(db):
    _create_company(db, uid="CHE-000.000.001", name="Robotics AG", purpose_keywords="robotics,automation")
    _create_company(db, uid="CHE-000.000.002", name="Bakery GmbH", purpose_keywords="bakery,bread")

    stats = run_batch_collect(db, limit=10, run_google=False, purpose_keywords="robotics")
    assert stats["selected"] == 1


def test_run_batch_collect_skips_google_when_serper_missing(db, monkeypatch):
    _create_company(db, uid="CHE-000.000.003", name="NoKey SA", purpose_keywords=None, website_url=None)

    # Ensure the global settings object has no API key
    monkeypatch.setattr(settings, "serper_api_key", "")

    stats = run_batch_collect(db, limit=5, run_google=True)

    assert stats["selected"] == 1
    assert stats["google_enriched"] == 0
    assert stats["google_no_result"] == 0
    assert not stats["errors"]
    assert any("SERPER_API_KEY" in w for w in stats["warnings"])