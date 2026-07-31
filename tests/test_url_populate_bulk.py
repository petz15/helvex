"""Bulk URL-candidate seeding used by the web_url_populate backfill.

The three bulk_* statements are PostgreSQL-only (DISTINCT ON, unnest, array
casts, ON CONFLICT), so the suite's SQLite engine cannot execute them. What is
tested here instead: the portable row-building logic, and that each statement
still compiles against the PostgreSQL dialect with exactly the bind parameters
the callers pass — which catches an unbalanced paren, a typo'd :param, or a
regex whose colon got swallowed as a bindparam, none of which would surface
until the job ran in prod.
"""
from __future__ import annotations

import inspect
import re
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.dialects import postgresql

from app.crud import crawler as crawler_crud


NOW = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)


# ── build_candidate_rows ──────────────────────────────────────────────────────

def test_build_candidate_rows_maps_serper_fields():
    rows = crawler_crud.build_candidate_rows(
        7, [{"link": "https://a.ch", "title": "A", "snippet": "s", "score": 0.9, "position": 1}], NOW,
    )
    assert rows == [{
        "company_id": 7, "url": "https://a.ch", "title": "A", "snippet": "s",
        "score": 0.9, "position": 1, "status": "pending", "source": "serper",
        "first_seen_at": NOW,
    }]


def test_build_candidate_rows_dedupes_repeated_urls():
    """A single ON CONFLICT DO UPDATE cannot touch the same key twice.

    Serper does return the same URL more than once for a company, so without
    this the whole batch statement errors out.
    """
    rows = crawler_crud.build_candidate_rows(
        1,
        [
            {"link": "https://a.ch", "score": 0.9},
            {"link": "https://a.ch", "score": 0.1},   # duplicate — first wins
            {"url": "https://b.ch", "score": 0.7},    # 'url' key also accepted
        ],
        NOW,
    )
    assert [(r["url"], r["score"]) for r in rows] == [
        ("https://a.ch", 0.9), ("https://b.ch", 0.7),
    ]


def test_build_candidate_rows_skips_entries_without_a_url():
    rows = crawler_crud.build_candidate_rows(
        1, [{"title": "no url"}, {"link": "", "score": 1.0}, {"link": "https://ok.ch"}], NOW,
    )
    assert [r["url"] for r in rows] == ["https://ok.ch"]


def test_build_candidate_rows_empty_input():
    assert crawler_crud.build_candidate_rows(1, [], NOW) == []


# ── No-op guards (must not emit SQL for an empty batch) ───────────────────────

def test_bulk_helpers_are_noops_on_empty_input(db):
    assert crawler_crud.bulk_upsert_url_candidates(db, []) == 0
    assert crawler_crud.bulk_select_best_candidates(db, [], frozenset({"x.com"})) == 0
    assert crawler_crud.bulk_create_crawl_states(db, []) == 0


# ── PostgreSQL compilation ────────────────────────────────────────────────────

def _sql_blocks(fn) -> list[str]:
    """Extract the text(\"\"\"...\"\"\") SQL literals from a function's source."""
    body = inspect.getsource(fn)
    out = []
    for m in re.finditer(r'text\(\s*(?:f)?"""(.*?)"""', body, re.S):
        out.append(m.group(1).replace("{_APEX_DOMAIN_SQL}", crawler_crud._APEX_DOMAIN_SQL))
    return out


def test_bulk_select_best_candidates_sql_compiles():
    blocks = _sql_blocks(crawler_crud.bulk_select_best_candidates)
    assert blocks, "expected a SQL block in bulk_select_best_candidates"
    for sql in blocks:
        stmt = text(sql)
        # The apex-domain regex contains ':[0-9]+$' — assert the colon was not
        # mistaken for a bind parameter.
        assert set(stmt._bindparams) <= {"ids", "blocked"}
        stmt.compile(dialect=postgresql.dialect())


def test_bulk_create_crawl_states_sql_compiles():
    blocks = _sql_blocks(crawler_crud.bulk_create_crawl_states)
    assert blocks, "expected a SQL block in bulk_create_crawl_states"
    for sql in blocks:
        stmt = text(sql)
        assert set(stmt._bindparams) == {"ids"}
        stmt.compile(dialect=postgresql.dialect())


def test_apex_domain_sql_targets_last_two_labels():
    """The blocklist holds apex domains, so matching on the bare host would let
    ch.linkedin.com past an entry for linkedin.com."""
    sql = crawler_crud._APEX_DOMAIN_SQL
    assert "([^.]+\\.[^.]+)$" in sql
    assert "^https?://" in sql          # scheme stripped
    assert "split_part" in sql           # host taken up to the first '/'


def test_arrays_are_cast_for_empty_safety():
    """Postgres rejects an untyped empty ARRAY[]; every array param must be cast."""
    for fn in (crawler_crud.bulk_select_best_candidates, crawler_crud.bulk_create_crawl_states):
        src = inspect.getsource(fn)
        for param, sqltype in (("ids", "bigint[]"), ("blocked", "text[]")):
            if f":{param}" in src:
                assert f"CAST(:{param} AS {sqltype})" in src, (
                    f"{fn.__name__} passes :{param} without a CAST"
                )
