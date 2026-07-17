"""Handler for claude_classify job."""

from __future__ import annotations

from app.services.jobs.job_handlers import JobContext, JobWaitingExternalSignal


def handle_claude_classify(ctx: JobContext) -> tuple[dict, str]:
    from app.services.ingestion.collection import claude_classify_batch
    from app import crud

    _org_id = ctx.job.org_id

    def _eff(key: str, default: str = "") -> str:
        return crud.get_effective_setting(ctx.db, key, org_id=_org_id, default=default)

    from app.services.scoring.claude import resolve_claude_api_key
    _api_key = resolve_claude_api_key(ctx.db, _org_id)
    _use_batch = bool(ctx.params.get("use_batch_api", False))

    if _use_batch:
        submit_stats = claude_classify_batch(
            ctx.db,
            canton=ctx.params.get("canton") or None,
            min_flex_score=ctx.params.get("min_zefix_score"),
            max_flex_score=ctx.params.get("max_zefix_score"),
            min_web_score=ctx.params.get("min_google_score"),
            purpose_keywords=ctx.params.get("purpose_keywords") or None,
            rerun_classified=bool(ctx.params.get("rerun_classified", False)),
            auto_filter_keywords=bool(ctx.params.get("auto_filter_keywords", False)),
            use_fixed_categories=bool(ctx.params.get("use_fixed_categories", False)),
            limit=int(ctx.params.get("limit", 500)),
            system_prompt=ctx.params.get("system_prompt") or _eff("claude_classify_prompt") or None,
            target_description=_eff("claude_target_description") or None,
            api_key=_api_key,
            org_id=_org_id,
            use_batch_api=True,
            submit_only=True,
            companies_per_message=int(ctx.params.get("companies_per_message", 1)),
        )
        _batch_id = submit_stats.get("batch_id", "")
        updated_params = {
            **ctx.params,
            "batch_id": _batch_id,
            "chunk_company_ids": submit_stats.get("chunk_company_ids", {}),
        }
        crud.mark_waiting_external(
            ctx.db, ctx.job,
            message=f"Anthropic batch submitted: {_batch_id}",
            params=updated_params,
        )
        crud.create_event(ctx.db, job_id=ctx.job.id, level="info", message=f"Batch submitted: {_batch_id}")
        raise JobWaitingExternalSignal()

    def _progress(done: int, total: int, stats: dict) -> None:
        ctx.assert_not_cancelled()
        tokens = stats.get("input_tokens", 0) + stats.get("output_tokens", 0)
        msg = f"Classified {done}/{total} — {stats['classified']} scored, ~{tokens} tokens used"
        ctx.progress(done, total, stats, msg)

    stats = claude_classify_batch(
        ctx.db,
        canton=ctx.params.get("canton") or None,
        min_flex_score=ctx.params.get("min_zefix_score"),
        max_flex_score=ctx.params.get("max_zefix_score"),
        min_web_score=ctx.params.get("min_google_score"),
        purpose_keywords=ctx.params.get("purpose_keywords") or None,
        rerun_classified=bool(ctx.params.get("rerun_classified", False)),
        auto_filter_keywords=bool(ctx.params.get("auto_filter_keywords", False)),
        use_fixed_categories=bool(ctx.params.get("use_fixed_categories", False)),
        limit=int(ctx.params.get("limit", 500)),
        system_prompt=ctx.params.get("system_prompt") or _eff("claude_classify_prompt") or None,
        target_description=_eff("claude_target_description") or None,
        api_key=_api_key,
        org_id=_org_id,
        resume_from=ctx.resume_from,
        use_batch_api=False,
        companies_per_message=int(ctx.params.get("companies_per_message", 1)),
        progress_cb=_progress,
    )
    tokens = stats.get("input_tokens", 0) + stats.get("output_tokens", 0)
    return stats, f"Done — {stats['classified']} classified, {stats['skipped']} skipped, ~{tokens} tokens, {len(stats['errors'])} errors"
