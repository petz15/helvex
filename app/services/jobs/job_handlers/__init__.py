"""Job handler registry.

Each handler is a callable:
    handler(ctx: JobContext) -> tuple[dict, str]
        Returns (stats_dict, done_message).

Handlers may raise JobWaitingExternalSignal to transition the job to
waiting_external without completing it (used by claude_classify batch path).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from sqlalchemy.orm import Session

from app import crud


class JobWaitingExternalSignal(Exception):
    """Handler raised this to signal waiting_external transition."""


@dataclass
class JobContext:
    """What a handler is handed: its DB session, its job row, and the callbacks
    it needs to report progress and to yield on cancel/pause.

    Progress is persisted to `job_runs` and nowhere else — the UI reads it from
    there via the SSE poller in `app/api/routes/jobs.py`. There is deliberately
    no in-process state mirror and no pub/sub fan-out; both existed once and
    both have been removed.
    """

    db: Session
    job: Any  # JobRun ORM object
    params: dict
    resume_from: int
    app: Any  # FastAPI app or None
    _assert_not_cancelled: Callable
    _enqueue_job: Callable

    def assert_not_cancelled(self) -> None:
        self._assert_not_cancelled()

    def progress(self, done: int, total: int, stats: dict, msg: str) -> None:
        crud.update_progress(self.db, self.job, message=msg, done=done, total=total, stats=stats)
        crud.create_event(self.db, job_id=self.job.id, level="debug", message=msg)

    def progress_no_event(self, done: int, total: int, stats: dict, msg: str) -> None:
        crud.update_progress(self.db, self.job, message=msg, done=done, total=total, stats=stats)

    def event(self, level: str, message: str) -> None:
        crud.create_event(self.db, job_id=self.job.id, level=level, message=message)

    def status(self, msg: str) -> None:
        self._assert_not_cancelled()
        crud.update_progress(self.db, self.job, message=str(msg))
        crud.create_event(self.db, job_id=self.job.id, level="info", message=str(msg))

    def status_with_stats(self, msg: str) -> None:
        """Alias of `status()`.

        It only ever differed by forwarding `job.stats_json` to the in-process
        state mirror. That mirror is gone and stats already live on the job row,
        so the two are now identical. Kept because several handlers pass it as a
        status callback.
        """
        self.status(msg)

    def enqueue_job(self, **kwargs) -> Any:
        return self._enqueue_job(self.app, db=self.db, **kwargs)


# ── Handler imports ────────────────────────────────────────────────────────────

from app.services.jobs.job_handlers import (  # noqa: E402
    alerts,
    billing,
    claude,
    clustering,
    export,
    maintenance,
    noga,
    rescore,
    shab,
    shab_archive,
    simap,
    sogc_entity_resolution,
    sogc_persons,
    sogc_preprocess,
    sogc_repair,
    stopwords,
    uid_jobs,
    web_crawl,
    zefix_jobs,
)

JOB_HANDLERS: dict[str, Callable[[JobContext], tuple[dict, str]]] = {
    # UID register import
    "uid_import":                uid_jobs.handle_uid_import,
    "uid_detail":                uid_jobs.handle_uid_detail,
    # Zefix import
    "bulk":                      zefix_jobs.handle_bulk,
    "web_search_batch":          zefix_jobs.handle_batch,
    "initial":                   zefix_jobs.handle_initial,
    "detail":                    zefix_jobs.handle_detail,
    # Scoring / geocoding
    "re_geocode":                zefix_jobs.handle_re_geocode,
    "recalculate_scores":        zefix_jobs.handle_recalculate_scores,
    "recalculate_google_scores": zefix_jobs.handle_recalculate_google_scores,
    "reextract_purpose":         zefix_jobs.handle_reextract_purpose,
    "reextract_zefix_raw":       zefix_jobs.handle_reextract_zefix_raw,
    # NOGA / language
    "reclassify_noga":           noga.handle_reclassify_noga,
    "build_noga_embeddings":     noga.handle_build_noga_embeddings,
    "detect_language_bulk":      noga.handle_detect_language_bulk,
    "reclassify_low_conf_noga":  noga.handle_reclassify_low_conf_noga,
    "noga_v2_explain":           noga.handle_noga_v2_explain,
    "enrich_web_purpose_sim":    noga.handle_enrich_web_purpose_sim,
    "embed_purpose_full":        noga.handle_embed_purpose_full,
    "embed_purpose_clean":       noga.handle_embed_purpose_clean,
    "strip_purpose_semantic":    noga.handle_strip_purpose_semantic,
    # Clustering
    "tfidf_kmeans_cluster":      clustering.handle_tfidf_kmeans_cluster,
    "recompute_keywords":        clustering.handle_recompute_keywords,
    "reextract_keywords":        clustering.handle_reextract_keywords,
    "cluster_analysis":          clustering.handle_cluster_analysis,
    # Stopwords / boilerplate
    "discover_stopwords":        stopwords.handle_discover_stopwords,
    "analyze_boilerplate":       stopwords.handle_analyze_boilerplate,
    # Claude classification
    "claude_classify":           claude.handle_claude_classify,
    # SIMAP
    "simap_daily":               simap.handle_simap,
    "simap_backfill":            simap.handle_simap,
    "simap_archive":             simap.handle_simap_archive,
    # SHAB / SOGC
    "shab_daily":                shab.handle_shab,
    "shab_backfill":             shab.handle_shab,
    "shab_archive":              shab_archive.handle_shab_archive,
    "link_sogc_stubs":           shab_archive.handle_link_sogc_stubs,
    "resolve_shab_old_uids":     shab_archive.handle_resolve_shab_old_uids,
    "backfill_shab_old_uid_extraction": shab_archive.handle_backfill_shab_old_uid_extraction,
    "sogc_preprocess":           sogc_preprocess.handle_sogc_preprocess,
    "extract_sogc_persons":      sogc_persons.handle_extract_sogc_persons,
    "resolve_bisher_links":      sogc_entity_resolution.handle_resolve_bisher_links,
    "repair_is_current":         sogc_repair.handle_repair_is_current,
    # CSV export
    "csv_export":                export.handle_csv_export,
    # Billing
    "billing_renewal":           billing.handle_billing_renewal,
    # Saved view alerts
    "saved_view_alerts":         alerts.handle_saved_view_alerts,
    # Scoring / multi-tenancy rework
    "rescore_scope":             rescore.handle_rescore_scope,
    # Web crawler
    "web_url_populate":          web_crawl.handle_web_url_populate,
    "web_crawl_http":            web_crawl.handle_web_crawl_http,
    "web_crawl_playwright":      web_crawl.handle_web_crawl_playwright,
    "web_crawl_content":         web_crawl.handle_web_crawl_content,
    "web_crawl_content_playwright": web_crawl.handle_web_crawl_content_playwright,
    "web_crawl_external":        web_crawl.handle_web_crawl_external,
    "cleanup_job_runs":          maintenance.handle_cleanup_job_runs,
    "web_extract":               web_crawl.handle_web_extract,
    "web_crawl_single":          web_crawl.handle_web_crawl_single,
    "web_select_url":            web_crawl.handle_web_select_url,
    "recompute_website_status":  web_crawl.handle_recompute_website_status,
    "directory_crawl":           web_crawl.handle_directory_crawl,
    "discover_directory_domains": web_crawl.handle_discover_directory_domains,
}
