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


def list_all_clusters(db: Session) -> list[ClusterRegistry]:
    """Return all registry entries (active and inactive) ordered by active desc, then name."""
    return (
        db.query(ClusterRegistry)
        .order_by(ClusterRegistry.active.desc(), ClusterRegistry.canonical_name.asc())
        .all()
    )


def rename_cluster(db: Session, registry_id: int, new_name: str) -> ClusterRegistry | None:
    """Rename a cluster's canonical_name in the registry and update all companies that reference it.

    Returns the updated entry, or None if not found.
    Raises ValueError if new_name is empty or already taken.
    """
    from app.models.company import Company

    entry = db.query(ClusterRegistry).filter(ClusterRegistry.id == registry_id).first()
    if entry is None:
        return None

    new_name = new_name.strip()
    if not new_name:
        raise ValueError("New name must not be empty")

    old_name = entry.canonical_name
    if old_name == new_name:
        return entry

    # Check uniqueness
    conflict = (
        db.query(ClusterRegistry)
        .filter(ClusterRegistry.canonical_name == new_name)
        .filter(ClusterRegistry.id != registry_id)
        .first()
    )
    if conflict:
        raise ValueError(f"Cluster name '{new_name}' is already taken by registry entry #{conflict.id}")

    # Update all companies that reference this cluster (pipe-separated multi-label).
    # We use three LIKE patterns to match the cluster name as a whole pipe segment:
    #   exact match  : tfidf_cluster = old_name
    #   leading      : tfidf_cluster LIKE 'old_name|%'
    #   trailing     : tfidf_cluster LIKE '%|old_name'
    #   middle       : tfidf_cluster LIKE '%|old_name|%'
    # This avoids false positives from substring matches (e.g. "IT" inside "IT Dienstleistungen").
    from sqlalchemy import or_
    exact = old_name
    affected = (
        db.query(Company)
        .filter(Company.tfidf_cluster.isnot(None))
        .filter(
            or_(
                Company.tfidf_cluster == exact,
                Company.tfidf_cluster.like(f"{exact}|%"),
                Company.tfidf_cluster.like(f"%|{exact}"),
                Company.tfidf_cluster.like(f"%|{exact}|%"),
            )
        )
        .all()
    )
    updated_companies = 0
    for company in affected:
        if company.tfidf_cluster is None:
            continue
        parts = [p.strip() for p in company.tfidf_cluster.split("|")]
        new_parts = [new_name if p == old_name else p for p in parts]
        if new_parts != parts:
            company.tfidf_cluster = "|".join(new_parts)
            updated_companies += 1

    entry.canonical_name = new_name
    db.commit()
    logger.info(
        "Cluster renamed: '%s' → '%s' (registry_id=%d, %d companies updated)",
        old_name, new_name, registry_id, updated_companies,
    )
    return entry


def get_cluster_company_count(db: Session, canonical_name: str) -> int:
    """Return the number of companies currently assigned to this cluster.

    Matches only whole pipe-separated segments to avoid substring false positives.
    """
    from sqlalchemy import func, or_
    from app.models.company import Company

    return (
        db.query(func.count(Company.id))
        .filter(Company.tfidf_cluster.isnot(None))
        .filter(
            or_(
                Company.tfidf_cluster == canonical_name,
                Company.tfidf_cluster.like(f"{canonical_name}|%"),
                Company.tfidf_cluster.like(f"%|{canonical_name}"),
                Company.tfidf_cluster.like(f"%|{canonical_name}|%"),
            )
        )
        .scalar()
        or 0
    )
