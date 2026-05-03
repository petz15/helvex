"""Job handler registry.

Each handler is a callable:
    handler(ctx: JobContext) -> tuple[dict, str]
        Returns (stats_dict, done_message).

Handlers may raise JobWaitingExternalSignal to transition the job to
waiting_external without completing it (used by claude_classify batch path).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from sqlalchemy.orm import Session

from app import crud


class JobWaitingExternalSignal(Exception):
    """Handler raised this to signal waiting_external transition."""


@dataclass
class JobContext:
    db: Session
    job: Any  # JobRun ORM object
    params: dict
    resume_from: int
    app: Any  # FastAPI app or None
    _assert_not_cancelled: Callable
    _maybe_sync: Callable
    _heartbeat: Callable
    _enqueue_job: Callable

    def assert_not_cancelled(self) -> None:
        self._assert_not_cancelled()

    def progress(self, done: int, total: int, stats: dict, msg: str) -> None:
        crud.update_progress(self.db, self.job, message=msg, done=done, total=total, stats=stats)
        crud.create_event(self.db, job_id=self.job.id, level="debug", message=msg)
        self._maybe_sync(self.app, job_type=self.job.job_type, label=self.job.label, message=msg, stats=dict(stats), error=None, done=False)
        self._heartbeat()

    def progress_no_event(self, done: int, total: int, stats: dict, msg: str) -> None:
        crud.update_progress(self.db, self.job, message=msg, done=done, total=total, stats=stats)
        self._heartbeat()

    def event(self, level: str, message: str) -> None:
        crud.create_event(self.db, job_id=self.job.id, level=level, message=message)

    def status(self, msg: str) -> None:
        self._assert_not_cancelled()
        crud.update_progress(self.db, self.job, message=str(msg))
        crud.create_event(self.db, job_id=self.job.id, level="info", message=str(msg))
        self._maybe_sync(self.app, job_type=self.job.job_type, label=self.job.label, message=str(msg),
                         stats={}, error=None, done=False)

    def status_with_stats(self, msg: str) -> None:
        """Status callback that carries existing stats from job.stats_json."""
        import json
        self._assert_not_cancelled()
        crud.update_progress(self.db, self.job, message=str(msg))
        crud.create_event(self.db, job_id=self.job.id, level="info", message=str(msg))
        self._maybe_sync(self.app, job_type=self.job.job_type, label=self.job.label, message=str(msg),
                         stats=json.loads(self.job.stats_json) if self.job.stats_json else {},
                         error=None, done=False)

    def sync(self, msg: str, stats: dict, done: bool) -> None:
        self._maybe_sync(self.app, job_type=self.job.job_type, label=self.job.label,
                         message=msg, stats=stats, error=None, done=done)

    def enqueue_job(self, **kwargs) -> Any:
        return self._enqueue_job(self.app, db=self.db, **kwargs)


# ── Handler imports ────────────────────────────────────────────────────────────

from app.services.job_handlers import (  # noqa: E402
    alerts,
    billing,
    claude,
    clustering,
    export,
    noga,
    shab,
    stopwords,
    zefix_jobs,
)

JOB_HANDLERS: dict[str, Callable[[JobContext], tuple[dict, str]]] = {
    # Zefix import
    "bulk":                      zefix_jobs.handle_bulk,
    "batch":                     zefix_jobs.handle_batch,
    "initial":                   zefix_jobs.handle_initial,
    "detail":                    zefix_jobs.handle_detail,
    # Scoring / geocoding
    "re_geocode":                zefix_jobs.handle_re_geocode,
    "recalculate_scores":        zefix_jobs.handle_recalculate_scores,
    "recalculate_google_scores": zefix_jobs.handle_recalculate_google_scores,
    "reextract_purpose":         zefix_jobs.handle_reextract_purpose,
    # NOGA / language
    "reclassify_noga":           noga.handle_reclassify_noga,
    "build_noga_embeddings":     noga.handle_build_noga_embeddings,
    "detect_language_bulk":      noga.handle_detect_language_bulk,
    "reclassify_low_conf_noga":  noga.handle_reclassify_low_conf_noga,
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
    # SHAB
    "shab_daily":                shab.handle_shab,
    "shab_backfill":             shab.handle_shab,
    # CSV export
    "csv_export":                export.handle_csv_export,
    # Billing
    "billing_renewal":           billing.handle_billing_renewal,
    # Saved view alerts
    "saved_view_alerts":         alerts.handle_saved_view_alerts,
}
