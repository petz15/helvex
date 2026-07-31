"""Resume-checkpoint correctness for `reclassify_noga`.

`reclassify_noga` paginates the 700k-row companies table by keyset
(`Company.id > last_id`), which is the right pattern at this scale — but the
progress it reported to callers was a row *count*, not `last_id`, and the two
diverge whenever the `only_detailed_raw` filter creates id gaps (any company
with no purpose text). Regenerating `resume_from` from that count made a
paused/restarted job re-process (or, depending on gap direction, skip)
companies instead of continuing cleanly. Fixed by stashing the real cursor in
`stats["_resume_last_id"]`.
"""
import pytest

from app.models.company import Company
from app.schemas.company import CompanyUpdate
from app.services.ml import noga_pipeline


def _make_company(db, *, uid: str, purpose: str | None):
    c = Company(uid=uid, name=f"Company {uid}", purpose=purpose)
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def test_reclassify_noga_resume_cursor_survives_a_pause(db, monkeypatch):
    # First company has no purpose — excluded by only_detailed_raw at the DB
    # level, so the remaining companies' ids don't start at a round count.
    # This is the id gap that makes `processed` (a count) diverge from the
    # real keyset cursor `last_id`.
    no_purpose = _make_company(db, uid="CHE-000.000.000", purpose=None)
    companies = [no_purpose] + [
        _make_company(db, uid=f"CHE-000.000.{i:03d}", purpose="Bau AG, Handel mit Baumaterialien")
        for i in range(1, 6)
    ]
    detailed_ids = [c.id for c in companies[1:]]  # the 5 companies with a purpose

    monkeypatch.setattr("app.services.ml.noga.is_branch_office", lambda c: False)

    processed_ids: list[int] = []

    def fake_apply(db_, company, *, _vec_out=None):
        processed_ids.append(company.id)
        return None  # skipped_no_match — no company write, no embedding needed

    monkeypatch.setattr(noga_pipeline, "apply_noga_classification", fake_apply)

    captured_stats: dict = {}

    def pausing_progress_cb(done, total, stats):
        captured_stats.update(stats)
        raise RuntimeError("simulated pause after first batch")

    with pytest.raises(RuntimeError):
        noga_pipeline.reclassify_noga(
            db, batch_size=2, only_detailed_raw=True, progress_cb=pausing_progress_cb,
        )

    # First batch = the first 2 *detailed* companies, i.e. detailed_ids[0:2] —
    # their ids, not "2" (the naive row count) which is what the pre-fix code
    # would have reported as the resume point.
    assert processed_ids == detailed_ids[:2]
    resume_cursor = captured_stats["_resume_last_id"]
    assert resume_cursor == detailed_ids[1]

    # Resume from the real cursor: must cover the rest exactly once, no
    # re-processing of detailed_ids[:2] and no skipping.
    processed_ids.clear()
    noga_pipeline.reclassify_noga(
        db, batch_size=2, resume_from=resume_cursor, only_detailed_raw=True, progress_cb=None,
    )
    assert processed_ids == detailed_ids[2:]


def test_reclassify_low_confidence_noga_shrinking_filter_does_not_skip(db, monkeypatch):
    """The WHERE clause (noga_confidence < threshold) shrinks as batches commit:
    every company this fake classifier "improves" leaves the filter immediately.
    OFFSET pagination over that live-shrinking set would skip whichever
    not-yet-processed company happens to be sitting at the new offset once the
    improved ones in front of it disappear. Keyset pagination must not.
    """
    companies = [
        _make_company(db, uid=f"CHE-000.001.{i:03d}", purpose="Bau AG")
        for i in range(6)
    ]
    ids = [c.id for c in companies]

    monkeypatch.setattr("app.services.ml.noga.is_branch_office", lambda c: False)

    processed_ids: list[int] = []

    def fake_apply(db_, company, *, _vec_out=None):
        processed_ids.append(company.id)
        # Every company "improves" above threshold — each one committed leaves
        # the low-confidence filter before the next batch is fetched.
        return CompanyUpdate(noga_confidence=0.95)

    monkeypatch.setattr(noga_pipeline, "apply_noga_classification", fake_apply)

    # `SET LOCAL statement_timeout` is Postgres-only tuning unrelated to this
    # test; the SQLite test DB doesn't understand it, so swallow just that
    # statement and pass everything else through to the real session.
    real_execute = db.execute

    def execute_skip_pg_only(statement, *args, **kwargs):
        if "statement_timeout" in str(statement):
            return None
        return real_execute(statement, *args, **kwargs)

    monkeypatch.setattr(db, "execute", execute_skip_pg_only)

    stats = noga_pipeline.reclassify_low_confidence_noga(
        db, batch_size=2, confidence_threshold=0.80, progress_cb=None,
    )

    assert sorted(processed_ids) == sorted(ids)
    assert stats["updated"] == len(ids)
