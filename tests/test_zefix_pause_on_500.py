import httpx
import pytest

from app.schemas.company import ZefixSearchResult


def _http_500_error() -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://www.zefix.admin.ch/ZefixREST/api/v1/company/search")
    response = httpx.Response(status_code=500, request=request)
    return httpx.HTTPStatusError("Internal Server Error", request=request, response=response)


def test_iter_prefix_with_fallback_pauses_and_rechecks_on_500(monkeypatch):
    from app.services import collection

    # Avoid real sleeping in tests
    monkeypatch.setattr(collection.time, "sleep", lambda *_args, **_kwargs: None)

    calls = {"fetch": 0, "probe": 0}

    def fake_fetch(prefix: str, canton: str | None, *, active_only: bool = True):
        calls["fetch"] += 1
        if calls["fetch"] == 1:
            raise _http_500_error()
        return [ZefixSearchResult(uid="CHE-123.456.789", name="Test AG")]

    def fake_probe(*_args, **_kwargs):
        calls["probe"] += 1
        return []

    monkeypatch.setattr(collection, "fetch_companies_by_prefix", fake_fetch)
    monkeypatch.setattr(collection, "search_companies", fake_probe)

    status: list[str] = []

    items = list(
        collection._iter_prefix_with_fallback(
            None,
            "AB",
            active_only=True,
            request_delay=0,
            status_cb=lambda m: status.append(str(m)),
        )
    )

    assert calls["fetch"] == 2
    assert calls["probe"] >= 1
    assert len(items) == 1
    assert items[0].uid == "CHE-123.456.789"
    assert any("500 Internal Server Error" in m for m in status)
    assert any("reachable again" in m.lower() for m in status)


def test_import_company_from_zefix_uid_retries_on_500_when_enabled(monkeypatch):
    from app.services import collection

    # Avoid real sleeping in tests
    monkeypatch.setattr(collection.time, "sleep", lambda *_args, **_kwargs: None)

    calls = {"get": 0, "probe": 0}

    def fake_get(uid: str):
        calls["get"] += 1
        if calls["get"] == 1:
            raise _http_500_error()
        return {"uid": uid, "name": "Test"}

    def fake_probe(*_args, **_kwargs):
        calls["probe"] += 1
        return []

    monkeypatch.setattr(collection, "zefix_get_company", fake_get)
    monkeypatch.setattr(collection, "search_companies", fake_probe)

    # Bypass DB/scoring internals; focus on retry control flow.
    monkeypatch.setattr(collection, "_load_scoring_config", lambda _db: {})
    monkeypatch.setattr(
        collection,
        "_extract_company_fields",
        lambda _raw, _uid, *, scoring_config: collection.CompanyCreate(uid="CHE-123.456.789", name="Test AG"),
    )

    class DummyCompany:
        def __init__(self, uid: str):
            self.uid = uid

    monkeypatch.setattr(collection.crud, "get_company_by_uid", lambda _db, _uid: None)
    monkeypatch.setattr(collection.crud, "create_company", lambda _db, company_data: DummyCompany(company_data.uid))

    status: list[str] = []

    company, created = collection.import_company_from_zefix_uid(
        db=object(),
        uid="CHE-123.456.789",
        pause_on_zefix_500=True,
        status_cb=lambda m: status.append(str(m)),
    )

    assert created is True
    assert getattr(company, "uid") == "CHE-123.456.789"
    assert calls["get"] == 2
    assert calls["probe"] >= 1
    assert any("500 Internal Server Error" in m for m in status)


@pytest.mark.parametrize("pause_on_zefix_500", [False])
def test_import_company_from_zefix_uid_does_not_retry_by_default(monkeypatch, pause_on_zefix_500):
    from app.services import collection

    monkeypatch.setattr(collection.time, "sleep", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(collection, "zefix_get_company", lambda _uid: (_ for _ in ()).throw(_http_500_error()))

    with pytest.raises(httpx.HTTPStatusError):
        collection.import_company_from_zefix_uid(db=object(), uid="CHE-123.456.789", pause_on_zefix_500=pause_on_zefix_500)
