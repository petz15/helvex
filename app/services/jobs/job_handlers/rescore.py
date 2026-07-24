"""rescore_scope job — materializes company_score for one (org, optional user) scope.

Scoring/multi-tenancy rework, phase 3. Triggered by: org config saved, user
override saved, org AI run completes, or manually from the org/user scoring
settings UI (per-scope job trigger — see ROADMAP frontend-wiring rule).
"""
from __future__ import annotations

from app.services.jobs.job_handlers import JobContext


def handle_rescore_scope(ctx: JobContext) -> tuple[dict, str]:
    from app.services.scoring.rescore_scope import rescore_scope

    org_id = ctx.job.org_id
    if org_id is None:
        raise ValueError("rescore_scope requires an org_id on the job")
    user_id = ctx.params.get("user_id")

    def _progress(done: int, total: int, stats: dict) -> None:
        ctx.assert_not_cancelled()
        phase = stats.get("_phase", "scoring")
        label = "Computing flex scores" if phase == "scoring" else "Writing company_score"
        ctx.progress(done, total, stats, f"{label} — {done}/{total}")

    stats = rescore_scope(ctx.db, org_id=org_id, user_id=user_id, progress_cb=_progress)
    scope_label = f"user {user_id}" if user_id else "org default"
    done_msg = f"Rescored {stats['updated']} companies ({scope_label}) — {len(stats['errors'])} errors"
    return stats, done_msg
