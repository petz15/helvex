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

        def get(self, url, auth=None):
            self.calls.append(url)
            day = url.rsplit("/", 1)[-1]
            if day == "2026-01-01":
                return _Resp([
                    {
                        "sogcPublication": {
                            "sogcId": 101,
                            "sogcDate": "2026-01-01",
                            "publicationNumber": "HR01-1000000001",
                            "mutationTypes": [{"key": "NEW"}],
                        },
                        "companyShort": {"uid": "CHE-111.111.111", "name": "A AG"},
                    },
                    {
                        "sogcPublication": {
                            "sogcId": 102,
                            "sogcDate": "2026-01-01",
                            "publicationNumber": "HR02-1000000002",
                            "mutationTypes": [{"key": "MUTATION"}],
                        },
                        "companyShort": {"uid": "CHE-222.222.222", "name": "B AG"},
                    },
                ])
            return _Resp([
                {
                    "sogcPublication": {
                        "sogcId": 103,
                        "sogcDate": "2026-01-02",
                        "publicationNumber": "HR03-1000000003",
                        "mutationTypes": [{"key": "DELETION"}],
                    },
                    "companyShort": {"uid": "CHE-333.333.333", "name": "C AG"},
                }
            ])

    monkeypatch.setattr(shab_client.httpx, "Client", _Client)

    items = shab_client.fetch_hr_publications(
        from_date=date(2026, 1, 1),
        to_date=date(2026, 1, 2),
    )

    assert len(items) == 3
    assert items[0]["meta"]["id"] == "101"
    assert items[0]["meta"]["subRubric"] == shab_client.SUBR_NEW
    assert items[2]["meta"]["subRubric"] == shab_client.SUBR_DELETION


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

        def get(self, url, auth=None):
            self.calls.append(url)
            if url.endswith("/sogc/555"):
                return _Resp({
                    "sogcPublication": {
                        "sogcId": 555,
                        "sogcDate": "2026-01-10",
                        "publicationNumber": "HR02-1000000555",
                        "mutationTypes": [{"key": "MUTATION"}],
                    },
                    "companyShort": {"uid": "CHE-555.555.555", "name": "Mutation AG"},
                })
            return _Resp({})

    monkeypatch.setattr(shab_client.httpx, "Client", _Client)

    detail = shab_client.fetch_publication_detail("555")

    assert detail["meta"]["id"] == "555"
    assert detail["meta"]["subRubric"] == shab_client.SUBR_MUTATION
    assert detail["meta"]["uid"] == "CHE-555.555.555"


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
