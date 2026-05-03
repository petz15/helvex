"""Handlers for NOGA classification and language detection jobs."""

from __future__ import annotations

from app.services.job_handlers import JobContext


def handle_reclassify_noga(ctx: JobContext) -> tuple[dict, str]:
    from app.services.collection import reclassify_noga

    def _progress(done: int, total: int, stats: dict) -> None:
        ctx.assert_not_cancelled()
        msg = (
            f"Processed {done}/{total} — {stats.get('updated', 0)} updated, "
            f"{stats.get('skipped_no_match', 0)} no match, "
            f"{stats.get('skipped_not_detailed', 0)} not-detailed"
        )
        ctx.progress(done, total, stats, msg)

    stats = reclassify_noga(
        ctx.db,
        resume_from=ctx.resume_from,
        only_missing_noga=bool(ctx.params.get("only_missing_noga", False)),
        include_stale=bool(ctx.params.get("include_stale", False)),
        only_detailed_raw=bool(ctx.params.get("only_detailed_raw", True)),
        progress_cb=_progress,
    )
    done_msg = (
        f"Done — {stats.get('updated', 0)} reclassified, "
        f"{stats.get('skipped_existing', 0)} skipped existing, "
        f"{stats.get('skipped_not_detailed', 0)} skipped not-detailed, "
        f"{stats.get('skipped_no_match', 0)} skipped no-match, "
        f"{len(stats.get('errors', []))} errors"
    )
    if ctx.resume_from:
        done_msg += f" (resumed from {ctx.resume_from})"
    return stats, done_msg


def handle_build_noga_embeddings(ctx: JobContext) -> tuple[dict, str]:
    from scripts.build_noga_embeddings_pg import run as _build_noga_emb
    from app import crud

    batch_size = int(ctx.params.get("batch_size", 256))
    crud.update_progress(ctx.db, ctx.job, message="Embedding NOGA taxonomy…", done=0, total=1, stats={})
    _build_noga_emb(batch_size=batch_size, dry_run=False)
    return {"batch_size": batch_size}, "NOGA embeddings built and stored in pgvector"


def handle_detect_language_bulk(ctx: JobContext) -> tuple[dict, str]:
    from app.services.collection import detect_language_bulk

    def _progress(done: int, total: int, stats: dict) -> None:
        ctx.assert_not_cancelled()
        msg = (
            f"Processed {done}/{total} — {stats.get('updated', 0)} updated, "
            f"{stats.get('skipped_no_purpose', 0)} no purpose"
        )
        ctx.progress_no_event(done, total, stats, msg)

    stats = detect_language_bulk(
        ctx.db,
        only_missing=bool(ctx.params.get("only_missing", True)),
        progress_cb=_progress,
    )
    return stats, (
        f"Done — {stats.get('updated', 0)} languages detected, "
        f"{stats.get('skipped_existing', 0)} skipped existing, "
        f"{len(stats.get('errors', []))} errors"
    )


def handle_reclassify_low_conf_noga(ctx: JobContext) -> tuple[dict, str]:
    from app.services.collection import reclassify_low_confidence_noga

    threshold = float(ctx.params.get("confidence_threshold", 0.80))

    def _progress(done: int, total: int, stats: dict) -> None:
        ctx.assert_not_cancelled()
        msg = (
            f"Processed {done}/{total} — {stats.get('improved', 0)} improved, "
            f"{stats.get('still_low', 0)} still low confidence"
        )
        ctx.progress_no_event(done, total, stats, msg)

    stats = reclassify_low_confidence_noga(
        ctx.db,
        confidence_threshold=threshold,
        progress_cb=_progress,
    )
    return stats, (
        f"Done — {stats.get('updated', 0)} reclassified, "
        f"{stats.get('improved', 0)} now above threshold, "
        f"{stats.get('still_low', 0)} still low, "
        f"{len(stats.get('errors', []))} errors"
    )
