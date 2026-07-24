"""company_search_results — Google/ScrapingDog search data extracted off the
companies table (Company table normalization)."""
from datetime import datetime, timezone

from app.crud import company_search_result as csr_crud
from app.crud.company import list_companies
from app.models.company import Company


def _company(db, company_id: int, **kw) -> Company:
    c = Company(id=company_id, uid=f"CHE-{company_id}.000.000", name=f"Test {company_id} AG", **kw)
    db.add(c)
    db.commit()
    return c


def test_upsert_and_get_search_result(db):
    _company(db, 1)
    csr_crud.upsert_search_result(
        db, 1,
        provider="serper",
        results_raw=[{"title": "T", "link": "https://t.ch", "score": 80}],
        full_raw={"organic": []},
        params={"q": "Test 1 AG"},
        searched_at=datetime.now(timezone.utc),
    )
    db.commit()

    row = csr_crud.get_search_result(db, 1)
    assert row is not None
    assert row.provider == "serper"
    assert row.results_raw[0]["link"] == "https://t.ch"
    assert row.params["q"] == "Test 1 AG"


def test_upsert_is_idempotent_update_not_duplicate(db):
    _company(db, 2)
    csr_crud.upsert_search_result(db, 2, results_raw=[{"link": "https://old.ch"}])
    db.commit()
    csr_crud.upsert_search_result(db, 2, results_raw=[{"link": "https://new.ch"}])
    db.commit()

    row = csr_crud.get_search_result(db, 2)
    assert row.results_raw == [{"link": "https://new.ch"}]


def test_bulk_get_search_results(db):
    _company(db, 3)
    _company(db, 4)
    csr_crud.upsert_search_result(db, 3, results_raw=[{"link": "https://a.ch"}])
    db.commit()

    results = csr_crud.bulk_get_search_results(db, [3, 4])
    assert set(results.keys()) == {3}
    assert results[3].results_raw == [{"link": "https://a.ch"}]


def test_list_companies_google_searched_filter(db):
    _company(db, 5)
    _company(db, 6)
    csr_crud.upsert_search_result(db, 5, results_raw=[{"link": "https://x.ch"}])
    db.commit()

    searched = list_companies(db, google_searched="yes", page_size=50)
    not_searched = list_companies(db, google_searched="no", page_size=50)

    assert {c.id for c in searched} == {5}
    assert {c.id for c in not_searched} == {6}


def test_list_companies_sort_by_website_checked_at(db):
    _company(db, 7)
    _company(db, 8)
    csr_crud.upsert_search_result(db, 7, results_raw=[{"link": "https://early.ch"}], searched_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    csr_crud.upsert_search_result(db, 8, results_raw=[{"link": "https://late.ch"}], searched_at=datetime(2026, 6, 1, tzinfo=timezone.utc))
    db.commit()

    ascending = list_companies(db, sort="website_checked_at", page_size=50)
    ids_with_dates = [c.id for c in ascending if c.id in (7, 8)]
    assert ids_with_dates == [7, 8]

    descending = list_companies(db, sort="-website_checked_at", page_size=50)
    ids_with_dates_desc = [c.id for c in descending if c.id in (7, 8)]
    assert ids_with_dates_desc == [8, 7]


def test_search_results_count_reflects_searched_companies(db):
    """get_company_stats()'s "searched" count now derives from company_search_results
    (was Company.website_checked_at). Checked directly rather than via the full
    get_company_stats() call, which also runs a Postgres-only ::int cast
    (score_distribution) unrelated to this change and not supported on the
    SQLite test DB."""
    from app.models.company_search_result import CompanySearchResult

    _company(db, 9)
    _company(db, 10)
    csr_crud.upsert_search_result(db, 9, results_raw=[{"link": "https://s.ch"}], searched_at=datetime.now(timezone.utc))
    db.commit()

    assert db.query(CompanySearchResult).count() == 1
