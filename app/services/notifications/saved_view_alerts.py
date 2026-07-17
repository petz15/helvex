"""Nightly job: check each alert-enabled saved view for new company matches.

For every UserView with alert_enabled=True:
1. Re-run the saved filters through count_companies().
2. If the count increased since the last check, email the user.
3. Update alert_last_count and alert_last_checked_at regardless.

On the very first run (alert_last_count is None) we only establish the
baseline — no email is sent. This prevents a flood when a user enables
alerts on a view that already has thousands of matches.

Security notes:
- Respects per-org email_notifications setting (default enabled).
- Inactive users are skipped.
- Errors on individual views are caught and counted; they never abort the run.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Mapping from CompanyFilters frontend key → count_companies() parameter name.
# Keys absent from this map are silently ignored (e.g. page, page_size, sort).
_FILTER_MAP: dict[str, str] = {
    "q":                        "name_filter",
    "uid":                      "uid_filter",
    "canton":                   "canton",
    "review_status":            "review_status",
    "contact_status":           "contact_status",
    "google_searched":          "google_searched",
    "min_web_score":            "min_web_score",
    "max_web_score":            "max_web_score",
    "min_flex_score":           "min_flex_score",
    "max_flex_score":           "max_flex_score",
    "min_ai_score":             "min_ai_score",
    "max_ai_score":             "max_ai_score",
    "min_combined_score":       "min_combined_score",
    "max_combined_score":       "max_combined_score",
    "ai_category":              "ai_category",
    "tags":                     "tags",
    "tfidf_cluster":            "tfidf_cluster",
    "purpose_keywords":         "purpose_keywords",
    "noga_code":                "noga_code",
    "noga_label":               "noga_label",
    "noga_level":               "noga_level",
    "exclude_tags":             "exclude_tags",
    "exclude_review_status":    "exclude_review_status",
    "exclude_canton":           "exclude_canton",
    "exclude_contact_status":   "exclude_contact_status",
    "exclude_tfidf_cluster":    "exclude_tfidf_cluster",
    "exclude_purpose_keywords": "exclude_purpose_keywords",
    "exclude_ai_category":      "exclude_ai_category",
    "exclude_noga_code":        "exclude_noga_code",
    "exclude_noga_label":       "exclude_noga_label",
    "exclude_noga_level":       "exclude_noga_level",
    "zefix_status":             "zefix_status",
    "has_website":              "has_website",
    "legal_form":               "legal_form",
    "registered_after":         "registered_after",
    "registered_before":        "registered_before",
    "status":                   "zefix_status",  # frontend may use either key
    "active_only":              "active_only",
}


def run_saved_view_alerts(db: Session) -> dict[str, Any]:
    """Check all alert-enabled saved views and send email alerts for new matches.

    Returns stats: ``{"checked": int, "alerted": int, "errors": int}``
    """
    from app.models.user_view import UserView

    stats: dict[str, Any] = {"checked": 0, "alerted": 0, "errors": 0}
    views = db.query(UserView).filter(UserView.alert_enabled.is_(True)).all()

    for view in views:
        stats["checked"] += 1
        try:
            _check_view(db, view, stats)
        except Exception as exc:  # noqa: BLE001
            logger.warning("saved_view_alert view_id=%s: %s: %s", view.id, type(exc).__name__, exc)
            stats["errors"] += 1

    return stats


def _check_view(db: Session, view: "Any", stats: dict[str, Any]) -> None:
    from app.crud.company import count_companies
    from app.crud.app_setting import get_effective_setting
    from app.models.user import User

    user = db.get(User, view.user_id)
    if not user or not user.is_active:
        return

    # Respect org-level email opt-out
    if get_effective_setting(db, "email_notifications", org_id=user.org_id, default="1") != "1":
        return
    if get_effective_setting(db, "notif_saved_view", org_id=user.org_id, default="1") != "1":
        return

    filters = json.loads(view.filters_json or "{}")
    kwargs = {
        param: filters[key]
        for key, param in _FILTER_MAP.items()
        if key in filters and filters[key] is not None and filters[key] != ""
    }

    current_count: int = count_companies(db, **kwargs)
    previous_count: int | None = view.alert_last_count

    # Update baseline regardless of whether we alert
    view.alert_last_count = current_count
    view.alert_last_checked_at = datetime.now(tz=timezone.utc)
    db.commit()

    if previous_count is None:
        # First run — establish baseline only, no alert
        return

    if current_count > previous_count:
        from app.config import settings as _settings
        from app.services.notifications.email import send_saved_view_alert
        view_url = f"{_settings.app_base_url}/app/search"
        send_saved_view_alert(
            to=user.email,
            view_name=view.name,
            new_count=current_count - previous_count,
            previous_count=previous_count,
            view_url=view_url,
        )
        stats["alerted"] += 1
