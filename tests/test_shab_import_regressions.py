from datetime import date


def test_fetch_hr_publications_uses_total_pages_metadata(monkeypatch):
    from app.api import shab_client

    class _Resp:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    class _Client:
        def __init__(self, *args, **kwargs):
            self.calls = []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, url, params):
            self.calls.append((url, dict(params)))
            page = int(params.get("page", 0))
            if page == 0:
                return _Resp({
                    "publications": [{"meta": {"id": f"p{n}"}} for n in range(1, 101)],
                    "pageNumber": 1,
                    "totalPages": 3,
                })
            if page == 1:
                return _Resp({
                    "publications": [{"meta": {"id": f"p{n}"}} for n in range(101, 201)],
                    "pageNumber": 2,
                    "totalPages": 3,
                })
            return _Resp({
                "publications": [{"meta": {"id": f"p{n}"}} for n in range(201, 241)],
                "pageNumber": 3,
                "totalPages": 3,
            })

    monkeypatch.setattr(shab_client.httpx, "Client", _Client)

    items = shab_client.fetch_hr_publications(
        from_date=date(2026, 1, 1),
        to_date=date(2026, 1, 31),
        page_size=2000,
    )

    assert len(items) == 240


def test_fetch_hr_publications_supports_content_page_request_wrapper(monkeypatch):
    from app.api import shab_client

    class _Resp:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    class _Client:
        def __init__(self, *args, **kwargs):
            self.calls = []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, url, params):
            self.calls.append((url, dict(params)))
            page = int(params.get("page", 0))
            if page == 0:
                return _Resp({
                    "content": [{"meta": {"id": f"c{n}"}} for n in range(1, 101)],
                    "total": 240,
                    "pageRequest": {"page": 0},
                })
            if page == 1:
                return _Resp({
                    "content": [{"meta": {"id": f"c{n}"}} for n in range(101, 201)],
                    "total": 240,
                    "pageRequest": {"page": 1},
                })
            return _Resp({
                "content": [{"meta": {"id": f"c{n}"}} for n in range(201, 241)],
                "total": 240,
                "pageRequest": {"page": 2},
            })

    monkeypatch.setattr(shab_client.httpx, "Client", _Client)

    items = shab_client.fetch_hr_publications(
        from_date=date(2026, 1, 1),
        to_date=date(2026, 1, 31),
        page_size=2000,
    )

    assert len(items) == 240


def test_import_company_from_zefix_uid_accepts_nested_list_uid(monkeypatch):
    from app.services import collection

    captured = {"uid": None}

    def fake_get(uid):
        captured["uid"] = uid
        return {"uid": uid, "name": "Test AG"}

    monkeypatch.setattr(collection, "zefix_get_company", fake_get)
    monkeypatch.setattr(collection, "_load_scoring_config", lambda _db: {})
    monkeypatch.setattr(
        collection,
        "_extract_company_fields",
        lambda _raw, _uid, *, scoring_config: collection.CompanyCreate(uid="CHE-123.456.789", name="Test AG"),
    )
    monkeypatch.setattr(collection.crud, "get_company_by_uid", lambda _db, _uid: None)

    class DummyCompany:
        def __init__(self, uid: str):
            self.uid = uid

    monkeypatch.setattr(collection.crud, "create_company", lambda _db, company_data: DummyCompany(company_data.uid))

    company, created = collection.import_company_from_zefix_uid(
        db=object(),
        uid=[["CHE-123.456.789"]],
    )

    assert created is True
    assert company.uid == "CHE-123.456.789"
    assert captured["uid"] == "CHE-123.456.789"


def test_zefix_get_company_accepts_nested_list_uid(monkeypatch):
    from app.api import zefix_client

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"uid": "CHE-123.456.789", "name": "Test AG"}

    class _Client:
        def __init__(self, *args, **kwargs):
            self.last_url = None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, url, auth=None):
            self.last_url = url
            assert url.endswith("/company/uid/CHE123456789")
            return _Resp()

    monkeypatch.setattr(zefix_client.httpx, "Client", _Client)

    data = zefix_client.get_company([["CHE-123.456.789"]])
    assert data["uid"] == "CHE-123.456.789"
