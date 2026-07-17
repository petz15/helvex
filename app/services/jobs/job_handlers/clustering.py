"""Handlers for TF-IDF clustering and keyword jobs."""

from __future__ import annotations

from app.services.jobs.job_handlers import JobContext


def handle_tfidf_kmeans_cluster(ctx: JobContext) -> tuple[dict, str]:
    from app.services.ml.cluster_pipeline import PipelineConfig, run_pipeline

    def _progress(done: int, total: int, stats: dict) -> None:
        ctx.assert_not_cancelled()
        step = stats.get("step", "clustering")
        msg = f"[{step}] {done}/{total} — {stats.get('classified', 0)} clustered, {stats.get('noise', 0)} noise"
        ctx.progress(done, total, stats, msg)

    cfg = PipelineConfig(
        n_clusters=int(ctx.params.get("n_clusters", 150)),
        max_clusters_per_company=int(ctx.params.get("max_clusters_per_company", 7)),
        min_similarity=float(ctx.params.get("min_similarity", 0.10)),
        n_components=int(ctx.params.get("n_components", 50)),
        top_terms_per_cluster=int(ctx.params.get("top_terms", 5)),
        top_keywords_per_company=int(ctx.params.get("top_keywords_per_company", 10)),
    )
    stats = run_pipeline(
        ctx.db, cfg,
        canton=ctx.params.get("canton") or None,
        min_zefix_score=int(ctx.params["min_zefix_score"]) if ctx.params.get("min_zefix_score") else None,
        max_zefix_score=int(ctx.params["max_zefix_score"]) if ctx.params.get("max_zefix_score") else None,
        limit=int(ctx.params["limit"]) if ctx.params.get("limit") else None,
        use_keywords=bool(ctx.params.get("use_keywords", False)),
        progress_cb=_progress,
    )
    n_c = stats.get("n_clusters", 0)
    classified = stats.get("classified", 0)
    noise = stats.get("noise", 0)
    done_msg = f"Done — {n_c} clusters, {classified} companies labelled, {noise} noise"

    try:
        sw_job = ctx.enqueue_job(
            job_type="discover_stopwords",
            label="Stopword & boilerplate discovery (auto — post cluster)",
            params={"use_ai": False},
        )
        ctx.event("info", f"Auto-enqueued discover_stopwords (job #{sw_job.id})")
    except Exception as _exc:  # noqa: BLE001
        import logging
        logging.getLogger(__name__).warning("Could not auto-enqueue discover_stopwords: %s", _exc)

    return stats, done_msg


def handle_recompute_keywords(ctx: JobContext) -> tuple[dict, str]:
    from app.services.ml.cluster_pipeline import PipelineConfig, recompute_keywords

    def _progress(done: int, total: int, stats: dict) -> None:
        ctx.assert_not_cancelled()
        msg = f"[{stats.get('step', 'keywords')}] {done}/{total} — {stats.get('updated', 0)} updated"
        ctx.progress_no_event(done, total, stats, msg)
        ctx.sync(msg, dict(stats), False)

    cfg = PipelineConfig(
        top_keywords_per_company=int(ctx.params.get("top_keywords_per_company", 10)),
    )
    stats = recompute_keywords(
        ctx.db, cfg,
        canton=ctx.params.get("canton") or None,
        limit=int(ctx.params["limit"]) if ctx.params.get("limit") else None,
        progress_cb=_progress,
    )
    return stats, f"Done — {stats['updated']} keywords updated, {stats['skipped']} skipped"


def handle_reextract_keywords(ctx: JobContext) -> tuple[dict, str]:
    from app.services.ml.cluster_pipeline import PipelineConfig, reextract_keywords_all

    def _progress(done: int, total: int, stats: dict) -> None:
        ctx.assert_not_cancelled()
        msg = f"[keywords] {done}/{total} — {stats.get('updated', 0)} updated, {stats.get('skipped_no_purpose', 0)} skipped"
        ctx.progress(done, total, stats, msg)

    cfg = PipelineConfig(
        top_keywords_per_company=int(ctx.params.get("top_keywords_per_company", 10)),
    )
    stats = reextract_keywords_all(
        ctx.db, cfg,
        only_missing=bool(ctx.params.get("only_missing", False)),
        canton=ctx.params.get("canton") or None,
        limit=int(ctx.params["limit"]) if ctx.params.get("limit") else None,
        progress_cb=_progress,
    )
    if stats.get("skipped_no_artifacts") == -1:
        done_msg = "Aborted — no S3 artifacts found. Run a full tfidf_kmeans_cluster job first."
    else:
        done_msg = (
            f"Done — {stats['updated']} updated, "
            f"{stats['skipped_no_purpose']} skipped (no purpose), "
            f"{len(stats['errors'])} errors"
        )
    return stats, done_msg


def handle_cluster_analysis(ctx: JobContext) -> tuple[dict, str]:
    from app.services.ml.cluster_pipeline import PipelineConfig, analyze_cross_cluster_terms

    cfg = PipelineConfig(
        analysis_top_clusters=int(ctx.params.get("top_n_clusters", 20)),
        analysis_top_terms=int(ctx.params.get("top_n_terms", 10)),
    )
    analyze_cross_cluster_terms(ctx.db, cfg)
    return {"errors": []}, "Cross-cluster analysis written — download at /static/cluster_analysis.txt"
