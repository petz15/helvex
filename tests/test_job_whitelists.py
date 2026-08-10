"""Guards against job allow-list drift.

Worker pods are whitelist-only: a job type absent from every enabled pod's
`JOB_TYPE_WHITELIST` is enqueued successfully, returns 202, and then sits in
`queued` forever. Nothing errors, nothing logs — the job simply never runs.
That failure mode has bitten this repo before, and it is invisible in exactly
the cases that matter most (user-triggered actions like `web_select_url`, and
the `web_crawl_single` fallback chain that identity resolution depends on).

These tests parse the Helm templates directly, so they catch a new job type
registered in JOB_HANDLERS without a matching whitelist entry, and a whitelist
entry that no longer corresponds to a real handler. No cluster required.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from app.services.jobs.job_handlers import JOB_HANDLERS

_REPO = Path(__file__).resolve().parents[1]
_TEMPLATES = _REPO / "infra" / "charts" / "helvex" / "templates"
_ENVIRONMENTS = _REPO / "infra" / "environments"

_WHITELIST_RE = re.compile(r'JOB_TYPE_WHITELIST\s*\n\s*value:\s*"([^"]+)"')
_BLACKLIST_RE = re.compile(r'JOB_TYPE_BLACKLIST\s*\n\s*value:\s*"([^"]+)"')


def _whitelist(template: str) -> set[str]:
    src = (_TEMPLATES / template).read_text(encoding="utf-8")
    m = _WHITELIST_RE.search(src)
    assert m, f"no JOB_TYPE_WHITELIST found in {template}"
    return {j.strip() for j in m.group(1).split(",") if j.strip()}


def _web_pod_blacklists() -> tuple[set[str], set[str]]:
    """The web pod's two blacklist variants, in template order.

    deployment.yaml branches: apiWorker AND mlWorker -> DISABLE_JOB_WORKER;
    else mlWorker only -> blacklist #1; else apiWorker only -> blacklist #2.
    """
    src = (_TEMPLATES / "deployment.yaml").read_text(encoding="utf-8")
    found = _BLACKLIST_RE.findall(src)
    assert len(found) == 2, (
        f"expected exactly 2 JOB_TYPE_BLACKLIST blocks in deployment.yaml, got {len(found)} — "
        "the branch order this test relies on has changed"
    )
    return (
        {j.strip() for j in found[0].split(",") if j.strip()},
        {j.strip() for j in found[1].split(",") if j.strip()},
    )


API = _whitelist("api-worker-deployment.yaml")
ML = _whitelist("ml-worker-deployment.yaml")
CRAWLER = _whitelist("crawler-http-deployment.yaml")
BL_ML_ONLY, BL_API_ONLY = _web_pod_blacklists()
REGISTERED = set(JOB_HANDLERS)


def _runnable(*, api: bool, ml: bool, crawler: bool) -> set[str]:
    """Job types claimable by at least one pod under this worker configuration."""
    out: set[str] = set()
    if api:
        out |= API
    if ml:
        out |= ML
    if crawler:
        out |= CRAWLER

    # The web pod, whose behaviour depends on which workers are enabled.
    if api and ml:
        pass                              # DISABLE_JOB_WORKER=true — runs nothing
    elif ml:
        out |= REGISTERED - BL_ML_ONLY
    elif api:
        out |= REGISTERED - BL_API_ONLY
    else:
        out |= REGISTERED                 # no blacklist env set — runs everything
    return out


def _env_worker_flags(env: str) -> dict[str, bool]:
    data = yaml.safe_load((_ENVIRONMENTS / env).read_text(encoding="utf-8")) or {}
    return {
        key: bool((data.get(key) or {}).get("enabled", False))
        for key in ("apiWorker", "mlWorker", "crawlerHttpWorker")
    }


# ── Core invariants ───────────────────────────────────────────────────────────

def test_prod_can_run_every_registered_job_type():
    """The bug class: a handler exists but no enabled pod may claim it."""
    flags = _env_worker_flags("prod.yaml")
    runnable = _runnable(
        api=flags["apiWorker"], ml=flags["mlWorker"], crawler=flags["crawlerHttpWorker"],
    )
    orphaned = sorted(REGISTERED - runnable)
    assert not orphaned, (
        "Job types registered in JOB_HANDLERS but not claimable by any pod enabled in "
        f"prod.yaml: {orphaned}. Add each to a JOB_TYPE_WHITELIST in "
        "infra/charts/helvex/templates/*-deployment.yaml — otherwise they enqueue, "
        "return 202, and never run."
    )


@pytest.mark.parametrize("template", [
    "api-worker-deployment.yaml",
    "ml-worker-deployment.yaml",
    "crawler-http-deployment.yaml",
])
def test_whitelists_contain_no_unknown_job_types(template):
    """Catches typos and job types deleted from the registry but left in Helm."""
    ghosts = sorted(_whitelist(template) - REGISTERED)
    assert not ghosts, (
        f"{template} whitelists job types with no handler in JOB_HANDLERS: {ghosts}"
    )


def test_web_pod_blacklists_contain_no_unknown_job_types():
    ghosts = sorted((BL_ML_ONLY | BL_API_ONLY) - REGISTERED)
    assert not ghosts, (
        f"deployment.yaml blacklists job types with no handler: {ghosts}"
    )


# ── Web pipeline placement ────────────────────────────────────────────────────

def test_web_pipeline_jobs_land_on_the_intended_pods():
    """Locks in where each web-pipeline job runs.

    Placement here is deliberate, not incidental: web_crawl_playwright needs the
    Chromium that only requirements.ml.txt installs, and the two bulk crawlers
    are on both crawl-capable pods so identity and content phases can progress
    in parallel. A change to this map should be a conscious edit, not a side
    effect of editing a long comma-separated string.
    """
    expected = {
        "web_url_populate":            {"crawler-http"},
        "web_crawl_http":              {"ml", "crawler-http"},
        "web_crawl_content":           {"ml", "crawler-http"},
        "web_crawl_playwright":        {"ml"},
        "web_crawl_single":            {"crawler-http"},
        "web_select_url":              {"crawler-http"},
        # Deliberately on BOTH. web_extract is the identity-critical step
        # (crawled HTML -> website verdict) and needs nothing from the ML image:
        # lxml/trafilatura/extruct/lingua are all in requirements.backend.txt and
        # spaCy NER degrades to the regex pass when the model is absent. ML-only
        # placement put it behind NOGA and Chromium on that pod's single slot, so
        # one stuck browser crawl stalled the entire crawl pipeline.
        "web_extract":                 {"ml", "crawler-http"},
        "directory_crawl":             {"crawler-http"},
        "discover_directory_domains":  {"api"},
        "recompute_website_status":    {"api"},
        "web_search_batch":            {"api"},
        "enrich_web_purpose_sim":      {"ml"},
    }
    pods = {"api": API, "ml": ML, "crawler-http": CRAWLER}
    actual = {
        job: {name for name, wl in pods.items() if job in wl}
        for job in expected
    }
    assert actual == expected


def test_every_web_pipeline_job_is_registered():
    """The placement map above is only meaningful if these all still exist."""
    mapped = {
        "web_url_populate", "web_crawl_http", "web_crawl_content",
        "web_crawl_playwright", "web_crawl_single", "web_select_url",
        "web_extract", "directory_crawl", "discover_directory_domains",
        "recompute_website_status", "web_search_batch", "enrich_web_purpose_sim",
    }
    missing = sorted(mapped - REGISTERED)
    assert not missing, f"placement map references non-existent job types: {missing}"


# ── Known-hazard snapshot ─────────────────────────────────────────────────────

# Scenarios other than prod's. The orphan sets are asserted rather than merely
# reported so that a whitelist edit which makes any configuration *worse* fails
# here instead of in a cluster.
_SCENARIOS = {
    # name:                        (api,   ml,    crawler, expected orphans)
    "all workers off (chart default)": (False, False, False, set()),
    "ml only":                        (False, True,  False, set()),
    "api only":                       (True,  False, False, {"enrich_web_purpose_sim"}),
    # The real hazard: scaling crawler-http to zero silently strands the
    # interactive URL-selection and single-crawl jobs, and with them the
    # web_extract fallback chain that identity resolution depends on.
    "api + ml, crawler-http off":     (True,  True,  False, {
        "directory_crawl", "web_crawl_single", "web_select_url", "web_url_populate",
    }),
}


@pytest.mark.parametrize("name", sorted(_SCENARIOS))
def test_known_orphaning_scenarios_are_unchanged(name):
    api, ml, crawler, expected = _SCENARIOS[name]
    orphaned = REGISTERED - _runnable(api=api, ml=ml, crawler=crawler)
    assert orphaned == expected, (
        f"orphaned job types under '{name}' changed.\n"
        f"  expected: {sorted(expected)}\n"
        f"  actual:   {sorted(orphaned)}\n"
        "If this is intentional, update _SCENARIOS. If not, a whitelist is missing an entry."
    )
