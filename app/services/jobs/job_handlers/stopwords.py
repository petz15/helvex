"""Handlers for stopword discovery and boilerplate analysis jobs."""

from __future__ import annotations

from app.services.jobs.job_handlers import JobContext


def handle_discover_stopwords(ctx: JobContext) -> tuple[dict, str]:
    from app.services.ml.stopword_discovery import discover_stopwords
    from app.config import settings as _settings

    def _progress(done: int, total: int, stats: dict) -> None:
        ctx.assert_not_cancelled()
        msg = f"Phase {done}/{total} complete"
        ctx.progress_no_event(done, total, stats, msg)

    use_ai = bool(ctx.params.get("use_ai", False))
    api_key = _settings.anthropic_api_key if use_ai else None
    stats = discover_stopwords(ctx.db, use_ai=use_ai, anthropic_api_key=api_key, progress_cb=_progress)
    return stats, (
        f"Done — phase1:{stats['phase1_stopwords_staged']} staged, "
        f"phase2:{stats['phase2_boilerplate_staged']} boilerplate, "
        f"phase3:{stats['phase3_stopwords_staged']} cross-cluster, "
        f"phase4:{stats['phase4_auto_approved']} auto-approved"
    )


def handle_analyze_boilerplate(ctx: JobContext) -> tuple[dict, str]:
    from app.services.ml.boilerplate_analysis import run_boilerplate_analysis
    from app import crud

    def _progress(done: int, total: int, bstats: dict) -> None:
        ctx.assert_not_cancelled()
        msg = (
            f"Scanned {done}/{total} purposes — "
            f"{bstats.get('unique_sentences', 0)} unique sentences found"
        )
        crud.update_progress(ctx.db, ctx.job, message=msg, done=done, total=total, stats=bstats)

    stats = run_boilerplate_analysis(
        ctx.db,
        min_match_count=ctx.params.get("min_match_count") or 500,
        max_candidates=ctx.params.get("max_candidates") or 200,
        sample_limit=ctx.params.get("sample_limit") or 200_000,
        language_filter=ctx.params.get("language_filter") or None,
        truncate_mode=bool(ctx.params.get("truncate_mode", False)),
        pre_strip=bool(ctx.params.get("pre_strip", False)),
        progress_cb=_progress,
    )
    return stats, (
        f"Done — {stats['total_purposes_scanned']} purposes scanned, "
        f"{stats['candidates_found']} candidates, "
        f"{stats['new_patterns_saved']} new inactive patterns saved for review"
    )
