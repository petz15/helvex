
from app.config import settings
from app.models.company import Company
from app.services.ingestion.collection import run_batch_collect


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


def test_run_batch_collect_circuit_breaker_on_provider_failures(db, monkeypatch):
    """Regression: a provider that fails on every request (e.g. ScrapingDog rejecting
    concurrent requests above the plan's limit) must not silently burn through the
    whole selection — enrich_company_website used to swallow the exception entirely
    (return False, None), which meant no exception ever reached run_batch_collect and
    the circuit breaker never saw a failure. It now re-raises, so the breaker trips."""
    import json

    from app.crud.company_error import get_errors
    from app.services.enrichment import web_enrichment

    monkeypatch.setattr(settings, "serper_api_key", "test-key")
    for i in range(30):
        _create_company(db, uid=f"CHE-100.000.{i:03d}", name=f"Failing Co {i}")

    def _boom(*args, **kwargs):
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(web_enrichment, "search_website", _boom)

    stats = run_batch_collect(db, limit=30, run_google=True, concurrency=1)

    assert stats["circuit_breaker_tripped"] is True
    assert stats["error_count"] >= 15
    attempted = stats["google_enriched"] + stats["google_no_result"] + stats["error_count"]
    assert attempted < 30  # stopped early instead of attempting all 30 — the window
    # (20) needs to fully fill before the breaker can trip, so it can't stop before
    # item 20, but must stop before the full 30 are attempted.

    errors = get_errors(db, source="web_enrichment")
    assert errors["total"] >= 1
    err = errors["items"][0]
    assert err["error_type"] == "search_api_failed"
    detail = json.loads(err["detail_json"])
    assert detail["request"]["provider"] == "serper"
    assert "provider exploded" in err["message"]


def test_run_batch_collect_processes_all_companies_across_chunks(db, monkeypatch):
    """Regression: run_batch_collect used to load every selected company via an
    individual db.get() call before processing anything — for a large `limit`
    (a real prod job selected 100002) that's tens of thousands of sequential
    round-trips with zero progress/heartbeat/cancellation checkpoints reached the
    whole time, which looks exactly like a stuck job. Now companies are loaded in
    fixed-size batches; shrink the batch size here to prove multiple chunks are
    each fully processed, not just the first."""
    from app.services.enrichment import web_enrichment

    monkeypatch.setattr(web_enrichment, "_BATCH_LOAD_CHUNK", 3)
    monkeypatch.setattr(settings, "serper_api_key", "test-key")

    for i in range(10):
        _create_company(db, uid=f"CHE-300.000.{i:03d}", name=f"Chunked Co {i}")

    def _ok(*args, **kwargs):
        return [], {}  # empty results — "no result", but every company must be reached

    monkeypatch.setattr(web_enrichment, "search_website", _ok)

    stats = run_batch_collect(db, limit=10, run_google=True, concurrency=1)

    assert stats["selected"] == 10
    assert stats["google_no_result"] == 10
    assert stats["error_count"] == 0


def test_run_batch_collect_pending_crawl_distinct_from_no_result(db, monkeypatch):
    """Regression: the crawl-only verdict (identity rework phase 3) means a fresh
    search essentially never sets a positive verdict by itself anymore — but that's
    NOT the same as the provider genuinely returning nothing. Companies where the
    provider found a real candidate (results non-empty) must land in
    google_pending_crawl, not be lumped into google_no_result alongside searches
    that truly found nothing — conflating the two made it look like search itself
    was failing when company_search_results actually had real data."""
    from app.services.enrichment import web_enrichment

    monkeypatch.setattr(settings, "serper_api_key", "test-key")
    _create_company(db, uid="CHE-400.000.001", name="Findable AG")

    def _found(*args, **kwargs):
        from app.schemas.company import GoogleSearchResult
        return [GoogleSearchResult(title="Findable AG", link="https://findable.ch", snippet="Findable AG Zürich")], None

    monkeypatch.setattr(web_enrichment, "search_website", _found)

    stats = run_batch_collect(db, limit=10, run_google=True, concurrency=1)

    assert stats["selected"] == 1
    assert stats["google_enriched"] == 0        # crawl-gated: no crawl exists yet
    assert stats["google_pending_crawl"] == 1   # but the provider DID find something
    assert stats["google_no_result"] == 0       # must not be double-counted as "nothing found"


def test_run_batch_collect_triggers_provider_backoff_on_circuit_break(db, monkeypatch):
    """The 'used a lot of credits, barely searched anything' report traced to
    rapid back-to-back web_search_batch runs, each burning through ~650 requests
    before its own circuit breaker tripped. A per-run breaker isn't enough if
    something immediately starts a new run — so a trip now also sets a
    provider-level backoff (AppSetting), checked by _google_search_ready, so an
    immediate re-trigger skips Google enrichment entirely instead of hammering
    the already-struggling provider again."""
    from app import crud
    from app.services.enrichment import web_enrichment

    monkeypatch.setattr(settings, "serper_api_key", "test-key")
    for i in range(30):
        _create_company(db, uid=f"CHE-500.000.{i:03d}", name=f"Backoff Co {i}")

    def _boom(*args, **kwargs):
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(web_enrichment, "search_website", _boom)

    stats = run_batch_collect(db, limit=30, run_google=True, concurrency=1)
    assert stats["circuit_breaker_tripped"] is True

    backoff_raw = crud.get_setting(db, "google_search_backoff_until_serper", "")
    assert backoff_raw, "circuit breaker trip should have recorded a backoff window"

    # A fresh run right after should skip Google entirely instead of retrying the
    # already-struggling provider.
    for i in range(30, 35):
        _create_company(db, uid=f"CHE-500.000.{i:03d}", name=f"Backoff Co {i}")
    stats2 = run_batch_collect(db, limit=5, run_google=True, concurrency=1)
    assert stats2["google_enriched"] == 0
    assert stats2["google_pending_crawl"] == 0
    assert stats2["google_no_result"] == 0
    assert stats2["error_count"] == 0
    assert any("backing off" in w for w in stats2["warnings"])


def test_run_batch_collect_logs_processing_failure_distinct_from_api_failure(db, monkeypatch):
    """Regression: a bug in scoring/verdict/persistence AFTER a successful provider
    response used to propagate with no company_errors row at all (only the API-call
    try/except logged anything) — indistinguishable from an actual provider outage.
    error_type=search_processing_failed makes this diagnosable: it fires when the
    provider genuinely returned data but our own code choked on it."""
    import json

    from app.crud.company_error import get_errors
    from app.schemas.company import GoogleSearchResult
    from app.services.enrichment import web_enrichment

    monkeypatch.setattr(settings, "serper_api_key", "test-key")
    company = _create_company(db, uid="CHE-200.000.001", name="Good Response AG")

    def _good_response(*args, **kwargs):
        return [GoogleSearchResult(title="Good Response AG", link="https://good-response.ch", snippet="x")], None

    def _boom(*args, **kwargs):
        raise RuntimeError("scoring exploded")

    monkeypatch.setattr(web_enrichment, "search_website", _good_response)
    monkeypatch.setattr(web_enrichment, "_score_google_results_for_company", _boom)

    try:
        web_enrichment.enrich_company_website(db, company)
        raised = False
    except RuntimeError:
        raised = True
    assert raised

    errors = get_errors(db, source="web_enrichment")
    assert errors["total"] == 1
    err = errors["items"][0]
    assert err["error_type"] == "search_processing_failed"
    detail = json.loads(err["detail_json"])
    assert detail["result_count"] == 1
    assert "scoring exploded" in err["message"]