"""Crawler self-preemption on the ML worker.

The rule these pin: a crawl job yields the ML worker's slot to real ML/NOGA
work, but NEVER to its own tier. Watching your own tier is a livelock — the
browser crawl types are NO_DEDUP and auto-enqueued per HTTP batch, so siblings
are normally queued; a job that yields to them preempts itself, gets auto-resumed
(pause_reason='preempt'), and preempts again without ever reaching its first
progress write. Observed as a job stuck at "Starting…" while holding the only
Chromium slot and starving the NOGA jobs it meant to yield to.
"""
from __future__ import annotations

from app.services.jobs.job_handlers.web_crawl import (
    _ML_ONLY_JOB_TYPES,
    _PLAYWRIGHT_JOB_TYPES,
    _preempt_watch_for,
)


def test_playwright_never_watches_for_its_own_tier():
    """The livelock. Both browser types must be absent from the watch set."""
    for job_type in _PLAYWRIGHT_JOB_TYPES:
        watch = _preempt_watch_for(job_type)
        assert not (watch & _PLAYWRIGHT_JOB_TYPES), (
            f"{job_type} would yield to a queued browser-tier sibling and livelock"
        )


def test_playwright_still_yields_to_noga():
    """Yielding to genuine ML work is the reason the mechanism exists."""
    watch = _preempt_watch_for("web_crawl_playwright")
    assert "reclassify_noga" in watch
    assert watch == _ML_ONLY_JOB_TYPES


def test_http_crawl_yields_to_both_ml_and_browser_tiers():
    """HTTP idle-fill is the lowest priority — a Chromium slot is scarcer."""
    watch = _preempt_watch_for("web_crawl_http")
    assert "reclassify_noga" in watch
    assert "web_crawl_playwright" in watch


def test_no_crawl_type_ever_watches_for_itself():
    """General form of the invariant — holds for any future crawl type."""
    for job_type in (
        "web_crawl_http", "web_crawl_content", "web_crawl_external",
        *_PLAYWRIGHT_JOB_TYPES,
    ):
        assert job_type not in _preempt_watch_for(job_type)
