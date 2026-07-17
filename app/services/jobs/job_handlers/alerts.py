"""Handler for saved_view_alerts job."""

from __future__ import annotations

from app.services.jobs.job_handlers import JobContext


def handle_saved_view_alerts(ctx: JobContext) -> tuple[dict, str]:
    from app.services.notifications.saved_view_alerts import run_saved_view_alerts
    from app import crud

    crud.update_progress(ctx.db, ctx.job, message="Checking saved view alerts…")
    stats = run_saved_view_alerts(ctx.db)
    return stats, (
        f"Done — {stats['checked']} views checked, "
        f"{stats['alerted']} alerted, "
        f"{stats.get('errors', 0)} errors"
    )
