"""CRUD helpers for ClusterRegistry.

The registry stores a stable canonical_name for each cluster across pipeline runs.
Matching is done by Jaccard similarity on top_terms lists (threshold 0.5).
"""
from __future__ import annotations

import json
import logging
from sqlalchemy.orm import Session

from app.models.cluster_registry import ClusterRegistry

logger = logging.getLogger(__name__)

_MATCH_THRESHOLD = 0.5


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    union = a | b
    return len(a & b) / len(union) if union else 0.0


def get_or_create_registry_entry(
    db: Session,
    label: str,
) -> ClusterRegistry:
    """Return the existing registry entry whose top_terms best match *label*, or create one.

    *label* is the comma-separated c-TF-IDF string produced by label_clusters(),
    e.g. "software,entwicklung,cloud,api,plattform".
    """
    new_terms = {t.strip() for t in label.split(",") if t.strip()}
    active_entries = db.query(ClusterRegistry).filter(ClusterRegistry.active == True).all()  # noqa: E712

    best: ClusterRegistry | None = None
    best_score = 0.0
    for entry in active_entries:
        try:
            existing_terms = set(json.loads(entry.top_terms))
        except (json.JSONDecodeError, TypeError):
            continue
        score = _jaccard(new_terms, existing_terms)
        if score > best_score:
            best_score = score
            best = entry

    if best is not None and best_score >= _MATCH_THRESHOLD:
        # Update top_terms if they've shifted
        if set(json.loads(best.top_terms)) != new_terms:
            best.top_terms = json.dumps(sorted(new_terms))
        return best

    # No match — create a new entry
    entry = ClusterRegistry(
        canonical_name=label,
        top_terms=json.dumps(sorted(new_terms)),
        active=True,
    )
    db.add(entry)
    db.flush()  # populate id without full commit
    return entry


def deactivate_missing_clusters(db: Session, seen_canonical_names: set[str]) -> int:
    """Mark registry entries as inactive if they weren't produced in the latest run."""
    count = 0
    for entry in db.query(ClusterRegistry).filter(ClusterRegistry.active == True).all():  # noqa: E712
        if entry.canonical_name not in seen_canonical_names:
            entry.active = False
            count += 1
    return count


def list_active_clusters(db: Session) -> list[ClusterRegistry]:
    return db.query(ClusterRegistry).filter(ClusterRegistry.active == True).order_by(ClusterRegistry.canonical_name).all()  # noqa: E712
