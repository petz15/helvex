"""Bisher-first entity resolution for SOGC person entities.

Stage 2 post-batch job that runs after extract_sogc_persons.

The SOGC gazette annotates every mutation with [bisher: ...] (previously),
encoding the prior state of the person at that company.  This lets us
hard-link appearances that belong to the same person even when their
normalized_key differs — most commonly because the person's name changed
(e.g. Müller → Müller-Schneider after marriage).

Algorithm
---------
1. Load all non-merged entity IDs into an in-memory union-find.
2. For every appearance that carries a parsed bisher_lastname or
   bisher_residence_municipality, look up the prior appearance at the
   same company that matches the bisher description.
3. If found and the two appearances belong to different entities, union
   those entity IDs.
4. After processing all bisher links, apply the unions: set
   merged_into_id on the non-canonical entity, redirect all its
   appearances to the canonical entity, and recompute counts +
   confidence on the canonical entity.

Cross-company linking is already handled at insertion time (key-based
dedup on lastname|firstname|hometown), so this resolver only needs to
handle same-company mutations that reveal a prior state.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


# ── Union-find ─────────────────────────────────────────────────────────────────

class _UnionFind:
    def __init__(self, ids: list[int]) -> None:
        self._parent: dict[int, int] = {i: i for i in ids}

    def find(self, x: int) -> int:
        while self._parent[x] != x:
            self._parent[x] = self._parent[self._parent[x]]  # path compression
            x = self._parent[x]
        return x

    def union(self, x: int, y: int) -> bool:
        px, py = self.find(x), self.find(y)
        if px == py:
            return False
        # Always merge higher ID into lower ID so the canonical entity is stable.
        if px > py:
            px, py = py, px
        self._parent[py] = px
        return True

    def merged_pairs(self) -> list[tuple[int, int]]:
        """Return (non_canonical_id, canonical_id) for every merged entity."""
        return [
            (eid, self.find(eid))
            for eid in self._parent
            if self.find(eid) != eid
        ]


# ── Bisher match lookup ────────────────────────────────────────────────────────

def _find_bisher_match(db: Session, app):
    """Find the prior appearance at the same company that this bisher refers to.

    Looks for an earlier appearance where:
    - same company_uid
    - pub_date is strictly earlier
    - lastname matches bisher_lastname (if name changed) or current lastname
    - optionally residence matches bisher_residence_municipality

    Returns the most recent qualifying prior appearance, or None.
    """
    from app.models.sogc_person_appearance import SogcPersonAppearance
    from sqlalchemy import func

    if not app.company_uid or not app.pub_date:
        return None

    # Determine the name we expect to find in the prior appearance.
    # If the person changed their name, bisher_lastname holds the old name.
    prior_lastname = app.bisher_lastname or app.lastname
    prior_firstname = app.bisher_firstname if app.bisher_lastname else app.firstname

    if not prior_lastname:
        return None

    q = (
        db.query(SogcPersonAppearance)
        .filter(
            SogcPersonAppearance.company_uid == app.company_uid,
            SogcPersonAppearance.pub_date < app.pub_date,
            SogcPersonAppearance.id != app.id,
            func.lower(SogcPersonAppearance.lastname) == prior_lastname.lower(),
        )
    )

    if prior_firstname:
        q = q.filter(
            func.lower(SogcPersonAppearance.firstname) == prior_firstname.lower()
        )

    # Narrow by prior residence when available — avoids false matches when
    # two people with the same name work at the same company at different times.
    if app.bisher_residence_municipality:
        q = q.filter(
            func.lower(SogcPersonAppearance.residence_municipality)
            == app.bisher_residence_municipality.lower()
        )

    return q.order_by(SogcPersonAppearance.pub_date.desc()).first()


# ── Entity merge helpers ───────────────────────────────────────────────────────

def _merge_entities(db: Session, non_canonical_id: int, canonical_id: int) -> None:
    """Redirect all appearances from non_canonical to canonical and set merged_into_id."""
    from app.models.sogc_person_entity import SogcPersonEntity
    from app.models.sogc_person_appearance import SogcPersonAppearance

    # Redirect appearances
    db.query(SogcPersonAppearance).filter_by(
        person_entity_id=non_canonical_id
    ).update({"person_entity_id": canonical_id}, synchronize_session=False)

    # Mark non-canonical as merged
    entity = db.get(SogcPersonEntity, non_canonical_id)
    if entity:
        entity.merged_into_id = canonical_id


def _recompute_after_merge(db: Session, entity_id: int) -> None:
    """Recompute appearance_count, active_company_count, and confidence after merge."""
    from app.models.sogc_person_entity import SogcPersonEntity
    from app.models.sogc_person_appearance import SogcPersonAppearance
    from app.services.sogc_person_extractor import _recompute_entity_confidence
    from sqlalchemy import func, distinct, or_

    entity = db.get(SogcPersonEntity, entity_id)
    if entity is None:
        return

    total = (
        db.query(func.count(SogcPersonAppearance.id))
        .filter_by(person_entity_id=entity_id)
        .scalar() or 0
    )
    active = (
        db.query(func.count(SogcPersonAppearance.id))
        .filter(
            SogcPersonAppearance.person_entity_id == entity_id,
            SogcPersonAppearance.is_current.is_(True),
        )
        .scalar() or 0
    )
    entity.appearance_count = total
    entity.active_company_count = active
    db.flush()

    _recompute_entity_confidence(db, entity_id)


# ── Main resolution job ────────────────────────────────────────────────────────

def run_resolve_bisher_links(
    db: Session,
    *,
    batch_size: int = 500,
    progress_cb: Callable | None = None,
    status_cb: Callable | None = None,
    abort_cb: Callable | None = None,
) -> dict:
    """Resolve person entities using bisher hard links.

    Should be run after extract_sogc_persons completes.  Safe to re-run
    (already-merged entities are skipped via merged_into_id check).

    Returns stats dict with hard_links_found, entities_merged, errors.
    """
    from app.models.sogc_person_entity import SogcPersonEntity
    from app.models.sogc_person_appearance import SogcPersonAppearance
    from sqlalchemy import or_

    stats: dict = {
        "appearances_with_bisher": 0,
        "hard_links_found": 0,
        "entities_merged": 0,
        "errors": [],
    }

    # ── Step 1: Load all non-merged entity IDs into union-find ─────────────────
    if status_cb:
        status_cb("Loading entity IDs into union-find…")

    entity_ids: list[int] = [
        eid
        for (eid,) in db.query(SogcPersonEntity.id)
        .filter(SogcPersonEntity.merged_into_id.is_(None))
        .all()
    ]
    uf = _UnionFind(entity_ids)

    # ── Step 2: Walk appearances with bisher fields, build hard links ──────────
    total = (
        db.query(SogcPersonAppearance)
        .filter(
            or_(
                SogcPersonAppearance.bisher_lastname.isnot(None),
                SogcPersonAppearance.bisher_residence_municipality.isnot(None),
            )
        )
        .count()
    )

    if status_cb:
        status_cb(f"Resolving bisher links: {total} appearances to process")

    last_id = 0
    done = 0

    while True:
        if abort_cb:
            abort_cb()

        batch = (
            db.query(SogcPersonAppearance)
            .filter(
                SogcPersonAppearance.id > last_id,
                or_(
                    SogcPersonAppearance.bisher_lastname.isnot(None),
                    SogcPersonAppearance.bisher_residence_municipality.isnot(None),
                ),
            )
            .order_by(SogcPersonAppearance.id.asc())
            .limit(batch_size)
            .all()
        )
        if not batch:
            break

        for app in batch:
            stats["appearances_with_bisher"] += 1
            try:
                prior = _find_bisher_match(db, app)
                if prior is None:
                    continue
                if not app.person_entity_id or not prior.person_entity_id:
                    continue
                # Only union non-merged entities that are in our union-find
                a_eid = app.person_entity_id
                p_eid = prior.person_entity_id
                if a_eid not in uf._parent or p_eid not in uf._parent:
                    continue
                if uf.union(a_eid, p_eid):
                    stats["hard_links_found"] += 1
            except Exception as exc:
                stats["errors"].append(
                    f"appearance_id={app.id}: {type(exc).__name__}: {exc}"
                )

        last_id = batch[-1].id
        done += len(batch)

        if progress_cb:
            progress_cb(done, total, stats)

    # ── Step 3: Apply merges ───────────────────────────────────────────────────
    merged_pairs = uf.merged_pairs()

    if status_cb:
        status_cb(f"Applying {len(merged_pairs)} entity merges…")

    touched_canonical: set[int] = set()

    for non_canonical_id, canonical_id in merged_pairs:
        try:
            _merge_entities(db, non_canonical_id, canonical_id)
            touched_canonical.add(canonical_id)
            stats["entities_merged"] += 1
        except Exception as exc:
            stats["errors"].append(
                f"merge entity {non_canonical_id}→{canonical_id}: "
                f"{type(exc).__name__}: {exc}"
            )

    db.flush()

    # ── Step 4: Recompute counts + confidence on canonical entities ────────────
    if status_cb:
        status_cb(f"Recomputing counts for {len(touched_canonical)} entities…")

    for eid in touched_canonical:
        try:
            _recompute_after_merge(db, eid)
        except Exception as exc:
            stats["errors"].append(
                f"recompute entity {eid}: {type(exc).__name__}: {exc}"
            )

    db.commit()

    if status_cb:
        status_cb(
            f"Bisher resolution done — "
            f"{stats['hard_links_found']} hard links, "
            f"{stats['entities_merged']} entities merged, "
            f"{len(stats['errors'])} errors"
        )

    return stats
