"""UID register gap import — name/keyword dictionary sweep, geo/status-refined.

Goal: DISCOVER the companies that are NOT in Zefix — sole traders / MWST-only /
uid_only entities (commercialRegisterStatus = "3", "not in HR"). MWST registration
implies turnover > CHF 100k, so these are commercially relevant leads.

Why this design (see app/clients/uid_client.py header + the phase-0 experiments):
  - `organisationName` is exact word-token AND matching (no CONTAINS). The old
    2-char prefix sweep was structurally lossy and is removed.
  - The Search API caps at 30 results with no pagination. PLZ / canton / HR / MWST
    filters all work server-side but none lifts the cap alone.
  - DISCOVERY must be driven by the NAME, not by geography: a company is only found
    if we query a word in its name. So the OUTER LOOP is a dictionary of name
    tokens + keywords built from the Zefix company names (every surname/word that
    appears in any registered name). Geography/status are REFINEMENTS that break
    the 30-cap, applied only when a token query overflows:

      for each dictionary token t:
          search(name=t, not_in_hr=True[, mwst_only])          # whole country
          if capped (==30):
              for canton in cantons:                            # 26 — cheap coarse split
                  search(name=t, canton=canton, not_in_hr=True)
                  if capped:
                      for plz in that canton's postcodes:        # fine split
                          search(name=t, plz=plz, not_in_hr=True)
                          if capped:                             # rare
                              [mwst subset] then AND a 2nd token

  - Completeness is bounded by the dictionary: a company whose name shares no token
    with any Zefix name is unreachable. With ~all Zefix name tokens this covers the
    vast majority (most sole traders are "Firstname Surname …" and surnames recur
    across the register). Coverage is unmeasurable from the API (no count) —
    validate by external recall sampling. Tune max_terms / min_token_freq to trade
    completeness vs. API calls.

Scale: the dictionary + canton→postcode map are built in ONE streaming pass over
non-UID `companies` rows (chunked, never loaded whole). Built from source<>'uid'
so the term list is STABLE across resumes (inserted uid rows don't shift indices).

Resume: `resume_from` is the index into the freq-sorted dictionary, stored in job
progress, so a crashed/paused job restarts from the last completed token.
"""
from __future__ import annotations

import json
import logging
import re
import unicodedata
from collections import Counter
from typing import Any, Callable

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.crud.company_error import log_error as _log_error
from app.models.company import Company

logger = logging.getLogger(__name__)

_BATCH_SIZE = 500
_NAME_SCAN_CHUNK = 5000          # rows per chunk when building the PLZ vocab

# Tokens never worth querying: legal-form noise + API zero-result stopwords.
_TOKEN_STOPWORDS = {
    "AG", "GMBH", "SA", "SARL", "SAGL", "SAS", "LTD", "INC", "CO", "AND", "UND",
    "THE", "VON", "DER", "DIE", "DAS", "LA", "LE", "LES", "DI", "DE", "DU", "EN",
    "IN", "ME", "ET", "UN", "UNE", "EG", "KG", "PLC",
}


# ── Text helpers ──────────────────────────────────────────────────────────────

def _fold(s: str) -> str:
    """Uppercase + strip accents — mirrors the API's accent-folding so a folded
    token like 'MULLER' matches 'Müller' server-side."""
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).upper()


_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")


def _name_tokens(name: str) -> list[str]:
    """Folded alphanumeric word tokens of a name, length ≥ 2, minus stopwords."""
    out = []
    for raw in _TOKEN_RE.findall(_fold(name)):
        if len(raw) >= 2 and raw not in _TOKEN_STOPWORDS:
            out.append(raw)
    return out


def _serialize_raw(raw: dict | None) -> str | None:
    if not raw:
        return None
    try:
        return json.dumps(raw, default=str, ensure_ascii=False)
    except Exception:
        return None


# ── Dictionary + canton→postcode map (single streaming pass) ──────────────────

_ZIP_RE = re.compile(r"^[0-9]{4}$")


def build_dictionary(
    db: Session,
    *,
    min_token_freq: int,
    max_terms: int,
    chunk: int = _NAME_SCAN_CHUNK,
    heartbeat_cb: Callable[[], None] | None = None,
) -> tuple[list[str], dict[str, list[str]]]:
    """Build the discovery dictionary + canton→postcodes map from Zefix names.

    Returns (terms, canton_plz):
      - terms: name/keyword tokens across all source<>'uid' company names,
        frequency-descending, filtered to count >= min_token_freq, capped at
        max_terms. This is the OUTER LOOP — every word that names a Swiss company.
      - canton_plz: {canton: [postcodes]} — the geographic refinement search space
        used to break the 30-cap on common tokens.

    One keyset-paginated pass (chunked). Built from source<>'uid' so the term order
    is stable across resumes. heartbeat_cb runs each chunk to keep the job alive.
    """
    counter: Counter = Counter()
    canton_plz: dict[str, set] = {}
    last_id = 0
    scanned = 0
    while True:
        if heartbeat_cb:
            heartbeat_cb()
        rows = db.execute(
            text(
                "SELECT id, name, canton, address_zip FROM companies "
                "WHERE id > :last AND source <> 'uid' "
                "ORDER BY id ASC LIMIT :lim"
            ),
            {"last": last_id, "lim": chunk},
        ).fetchall()
        if not rows:
            break
        for rid, name, canton, zip_code in rows:
            last_id = rid
            scanned += 1
            if name:
                for tok in set(_name_tokens(name)):
                    counter[tok] += 1
            if canton and zip_code and _ZIP_RE.match(zip_code):
                canton_plz.setdefault(canton.upper(), set()).add(zip_code)

    terms = [t for t, c in counter.most_common() if c >= min_token_freq][:max_terms]
    canton_map = {k: sorted(v) for k, v in canton_plz.items()}
    logger.info(
        "uid_sweep: dictionary=%d terms (of %d distinct), %d cantons, from %d names",
        len(terms), len(counter), len(canton_map), scanned,
    )
    return terms, canton_map


# ── Upsert ────────────────────────────────────────────────────────────────────

def _upsert_batch(
    db: Session,
    entities: list[dict[str, Any]],
    *,
    on_conflict: str = "update",
) -> int:
    """Bulk-upsert entity dicts. New rows are inserted with source='uid'.

    Zefix (and any non-uid) rows are NEVER overwritten in either mode:
      - on_conflict="ignore": ON CONFLICT DO NOTHING — no existing row is touched.
      - on_conflict="update": refresh existing source='uid' rows only (registration_type,
        uid_raw, status always; other fields filled only when currently NULL via
        COALESCE), gated by `WHERE companies.source = 'uid'` so Zefix rows are skipped.

    Returns affected row count.
    """
    if not entities:
        return 0

    rows = []
    for e in entities:
        rows.append({
            "uid": e["uid"],
            "name": e.get("name") or e["uid"],
            "status": e.get("status"),
            "legal_form": e.get("legal_form"),
            "municipality": e.get("municipality"),
            "canton": e.get("canton"),
            "address": e.get("address"),
            "address_city": e.get("address_city"),
            "address_zip": e.get("address_zip"),
            "registration_type": e.get("registration_type"),
            "source": "uid",
            "uid_raw": _serialize_raw(e.get("_raw")),
        })

    base = pg_insert(Company.__table__).values(rows)

    if on_conflict == "ignore":
        stmt = base.on_conflict_do_nothing(index_elements=["uid"])
    else:  # "update" — refresh existing uid rows only; never touch Zefix
        excl = base.excluded
        stmt = base.on_conflict_do_update(
            index_elements=["uid"],
            set_={
                "registration_type": excl.registration_type,
                "uid_raw": excl.uid_raw,
                "status": excl.status,
                # Enrichment fields: keep what's there, fill only if NULL (so a prior
                # uid_detail GetByUID enrichment is never clobbered by a sparse Search row).
                "legal_form": text("COALESCE(companies.legal_form, excluded.legal_form)"),
                "canton": text("COALESCE(companies.canton, excluded.canton)"),
                "municipality": text("COALESCE(companies.municipality, excluded.municipality)"),
                "address": text("COALESCE(companies.address, excluded.address)"),
                "address_city": text("COALESCE(companies.address_city, excluded.address_city)"),
                "address_zip": text("COALESCE(companies.address_zip, excluded.address_zip)"),
            },
            where=text("companies.source = 'uid'"),
        )

    result = db.execute(stmt)
    db.commit()
    return result.rowcount or 0


def _count_existing_by_uid(db: Session, uids: list[str]) -> set[str]:
    if not uids:
        return set()
    rows = db.execute(
        text("SELECT uid FROM companies WHERE uid = ANY(:uids)"),
        {"uids": uids},
    ).fetchall()
    return {r[0] for r in rows}


# ── Upsert helper ─────────────────────────────────────────────────────────────

def _upsert_entities(
    db: Session,
    entities: list[dict[str, Any]],
    *,
    batch_size: int,
    on_conflict: str,
    stats: dict[str, Any],
    label: str = "",
) -> None:
    """Upsert entity dicts in sub-batches, updating stats in place."""
    for i in range(0, len(entities), batch_size):
        sub = entities[i : i + batch_size]
        valid = [e for e in sub if e.get("uid")]
        stats["skipped_invalid"] += len(sub) - len(valid)
        if not valid:
            continue
        existing = _count_existing_by_uid(db, [e["uid"] for e in valid])
        new_count = sum(1 for e in valid if e["uid"] not in existing)
        try:
            _upsert_batch(db, valid, on_conflict=on_conflict)
        except Exception as exc:
            tag = f"[{label}] " if label else ""
            logger.error("UID upsert failed %s: %s", label or "", exc)
            stats["errors"].append(f"{tag}upsert: {exc}")
            try:
                db.rollback()
            except Exception:
                pass
            continue
        stats["inserted"] += new_count
        existing_count = len(valid) - new_count
        if on_conflict == "ignore":
            stats["skipped_existing"] += existing_count
        else:
            stats["updated_type"] += existing_count


# ── Per-token collection with geo/status refinement ───────────────────────────

def _collect_token(
    token: str,
    *,
    cantons: list[str],
    canton_plz: dict[str, list[str]],
    second_vocab: list[str],
    seen: set[str],
    not_in_hr: bool,
    mwst_only: bool,
    refine_mwst: bool,
    max_depth: int,
    abort_cb: Callable[[], None] | None,
    stats: dict[str, Any],
) -> tuple[list[dict[str, Any]], bool]:
    """Discover gap companies for one dictionary token.

    Returns (entities, was_rate_limited). Catches UIDRateLimitError internally so
    partial results from earlier sub-queries are preserved and returned to the
    caller even when a later refinement query is rate-limited. Callers should
    upsert the partial entities and queue the token for retry.

    Base query is name=token over the whole country (HR=3 to target the gap).
    When a bucket caps at 30, refine: canton → postcode → [MWST subset] → AND a
    2nd token. Dedups against `seen` (shared across the whole sweep)."""
    from app.clients.uid_client import UIDRateLimitError, search_entities

    out: list[dict] = []
    was_rate_limited = False

    def run(name: str, *, plz=None, canton=None, mwst=None) -> bool | None:
        """Search one bucket. Returns True=capped, False=not capped, None=rate-limited."""
        nonlocal was_rate_limited
        try:
            entities, capped = search_entities(
                name=name, plz=plz, canton=canton,
                not_in_hr=not_in_hr,
                mwst_only=mwst_only if mwst is None else mwst,
                abort_cb=abort_cb,
            )
        except UIDRateLimitError:
            was_rate_limited = True
            return None
        for e in entities:
            u = e.get("uid")
            if u and u not in seen:
                seen.add(u)
                out.append(e)
        return capped

    # 1) whole-country token query — None (rate-limited) or False (not capped) both bail out
    if not run(token):
        return out, was_rate_limited

    # 2) capped → split by canton
    stats["buckets_capped"] += 1
    for canton in cantons:
        if not run(token, canton=canton):
            continue
        # 3) canton still capped → split by postcode
        for plz in canton_plz.get(canton, ()):
            if not run(token, plz=plz):
                continue
            # 4) postcode still capped (rare) → MWST subset, then AND a 2nd token
            if refine_mwst and not mwst_only:
                run(token, plz=plz, mwst=True)
            if max_depth >= 1:
                for tok2 in second_vocab:
                    if tok2 == token:
                        continue
                    run(f"{token} {tok2}", plz=plz)
    return out, was_rate_limited


# ── Main sweep ────────────────────────────────────────────────────────────────

def sweep_uid_gap(
    db: Session,
    *,
    resume_from: int = 0,
    batch_size: int = _BATCH_SIZE,
    not_in_hr: bool = True,
    mwst_only: bool = False,
    refine_mwst: bool = True,
    max_depth: int = 1,
    min_token_freq: int = 2,
    max_terms: int = 50000,
    second_token_count: int = 200,
    on_conflict: str = "update",
    progress_cb: Callable[[int, int, dict], None] | None = None,
    abort_cb: Callable[[], None] | None = None,
    status_cb: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Discover non-Zefix (gap) companies via a name/keyword dictionary sweep.

    Outer loop = dictionary of name tokens from Zefix names (freq-sorted). Each
    token is queried nationally, then refined by canton → postcode → MWST → 2nd
    token to break the 30-cap. See module docstring.

    not_in_hr=True (default): commercialRegisterStatus="3" — the non-Zefix gap.
    mwst_only=True: restrict to MWST/VAT-registered (commercially active).
    refine_mwst: also pull the MWST subset of a still-capped postcode bucket.
    max_depth: 1 = allow one AND-ed 2nd token on capped postcodes; 0 = off.
    min_token_freq / max_terms: dictionary size knobs (cost vs completeness).
    second_token_count: how many top tokens to try as a 2nd AND term.
    on_conflict: "update" (refresh existing uid rows, never Zefix) | "ignore"
        (insert new UIDs only, leave every existing row untouched).

    resume_from: index into the freq-sorted dictionary (job stores progress_done).
    """
    stats: dict[str, Any] = {
        "inserted": 0,
        "updated_type": 0,
        "skipped_existing": 0,
        "skipped_invalid": 0,
        "api_errors": 0,
        "buckets_capped": 0,
        "tokens_done": 0,
        "tokens_total": 0,
        "rate_limited_tokens": 0,
        "current_token": "",
        "errors": [],
    }

    # Dictionary (outer loop) + canton→postcode refinement map, one DB pass.
    if status_cb:
        status_cb("(building dictionary)")
    terms, canton_plz = build_dictionary(
        db, min_token_freq=min_token_freq, max_terms=max_terms, heartbeat_cb=abort_cb,
    )
    cantons = sorted(canton_plz.keys())
    second_vocab = terms[:second_token_count]
    total = len(terms)
    stats["tokens_total"] = total

    if resume_from >= total:
        logger.warning("uid_sweep: resume_from=%d out of range (total=%d) — restarting at 0", resume_from, total)
        resume_from = 0

    seen: set[str] = set()
    retry_queue: list[tuple[int, str]] = []  # (original_idx, token) for rate-limited tokens

    for idx, token in enumerate(terms):
        if idx < resume_from:
            continue

        stats["current_token"] = token
        if status_cb:
            status_cb(token)

        try:
            entities, was_rate_limited = _collect_token(
                token,
                cantons=cantons, canton_plz=canton_plz, second_vocab=second_vocab,
                seen=seen, not_in_hr=not_in_hr, mwst_only=mwst_only,
                refine_mwst=refine_mwst, max_depth=max_depth,
                abort_cb=abort_cb, stats=stats,
            )
        except Exception as exc:
            if type(exc).__name__ in ("JobCancelledError", "JobPausedError"):
                raise
            logger.error("UID token sweep failed: token=%r: %s", token, exc)
            stats["api_errors"] += 1
            stats["errors"].append(f"[token={token}] {exc}")
            try:
                _log_error(
                    db, company_id=None, source="uid_import", error_type="api_error",
                    message=f"UID sweep failed (token={token!r}): {exc}",
                    detail={"token": token, "token_idx": idx},
                )
                db.commit()
            except Exception:
                db.rollback()
            if stats["api_errors"] > 50:
                logger.error("Too many non-rate-limit token failures — aborting UID sweep")
                return stats
            continue

        # Upsert whatever was collected — even partial results from a rate-limited token.
        _upsert_entities(
            db, entities,
            batch_size=batch_size, on_conflict=on_conflict, stats=stats,
            label=f"token={token}",
        )

        if was_rate_limited:
            # Queue for a retry after the main sweep completes with a cooldown.
            # Don't advance tokens_done so a resume from crash also picks it up.
            stats["rate_limited_tokens"] += 1
            stats["errors"].append(f"[token={token}] rate-limited (partial results saved, queued for retry)")
            logger.warning("UID token rate-limited, queued for retry: token=%r", token)
            retry_queue.append((idx, token))
            continue

        stats["tokens_done"] = idx + 1
        if progress_cb:
            progress_cb(idx + 1, total, stats)

    # ── Retry phase — process rate-limited tokens after a cooldown ────────────
    if retry_queue:
        logger.info("uid_sweep: %d rate-limited tokens to retry; cooling down 180s", len(retry_queue))
        if status_cb:
            status_cb("(cooldown before retry)")
        # Chunked sleep so cancellation is detected during the wait.
        import time
        _cooldown, _chunk = 180.0, 5.0
        _slept = 0.0
        while _slept < _cooldown:
            time.sleep(min(_chunk, _cooldown - _slept))
            _slept += _chunk
            if abort_cb:
                abort_cb()

        for retry_idx, retry_token in retry_queue:
            stats["current_token"] = retry_token
            if status_cb:
                status_cb(f"(retry) {retry_token}")
            try:
                entities, still_rate_limited = _collect_token(
                    retry_token,
                    cantons=cantons, canton_plz=canton_plz, second_vocab=second_vocab,
                    seen=seen, not_in_hr=not_in_hr, mwst_only=mwst_only,
                    refine_mwst=refine_mwst, max_depth=max_depth,
                    abort_cb=abort_cb, stats=stats,
                )
            except Exception as exc:
                if type(exc).__name__ in ("JobCancelledError", "JobPausedError"):
                    raise
                logger.error("UID retry sweep failed: token=%r: %s", retry_token, exc)
                stats["api_errors"] += 1
                stats["errors"].append(f"[retry][token={retry_token}] {exc}")
                continue

            _upsert_entities(
                db, entities,
                batch_size=batch_size, on_conflict=on_conflict, stats=stats,
                label=f"retry token={retry_token}",
            )

            if still_rate_limited:
                logger.warning("UID token still rate-limited after retry — giving up: token=%r", retry_token)
                stats["errors"].append(f"[retry][token={retry_token}] still rate-limited")
            else:
                stats["rate_limited_tokens"] -= 1  # recovered
                logger.info("uid_sweep: retry succeeded for token=%r", retry_token)

    return stats


# ── Detail fetch (GetByUID) ───────────────────────────────────────────────────

_DETAIL_BATCH_SIZE = 100


def fetch_uid_details(
    db: Session,
    *,
    resume_from: int = 0,
    batch_size: int = _DETAIL_BATCH_SIZE,
    progress_cb: Callable[[int, int, dict], None] | None = None,
) -> dict[str, Any]:
    """Call GetByUID for every source='uid' company that has not yet been fetched.

    Filters on uid_detail_fetched_at IS NULL — set after each attempt (success
    or not) so companies are never re-processed on subsequent runs even when the
    detail endpoint returns nothing. Populates address, legal_form, municipality,
    canton, address_zip from the UID detail endpoint.
    """
    from app.clients.uid_client import get_by_uid, detail_to_update

    stats: dict[str, Any] = {
        "updated": 0,
        "skipped_no_detail": 0,
        "api_errors": 0,
        "errors": [],
    }

    total: int = db.execute(
        text(
            "SELECT COUNT(*) FROM companies "
            "WHERE source = 'uid' AND uid_detail_fetched_at IS NULL AND id > :resume"
        ),
        {"resume": resume_from},
    ).scalar() or 0

    last_id = resume_from
    processed = 0

    while True:
        rows = db.execute(
            text(
                "SELECT id, uid FROM companies "
                "WHERE source = 'uid' AND uid_detail_fetched_at IS NULL AND id > :last_id "
                "ORDER BY id ASC LIMIT :limit"
            ),
            {"last_id": last_id, "limit": batch_size},
        ).fetchall()

        if not rows:
            break

        for row_id, uid_str in rows:
            try:
                entity = get_by_uid(uid_str)
            except Exception as exc:
                stats["api_errors"] += 1
                stats["errors"].append(f"[{uid_str}] GetByUID: {exc}")
                # Mark as fetched so we don't keep hammering a broken entry.
                db.execute(
                    text("UPDATE companies SET uid_detail_fetched_at = now() WHERE id = :id"),
                    {"id": row_id},
                )
                last_id = row_id
                processed += 1
                continue

            if entity:
                update = detail_to_update(entity)
                if update:
                    db.execute(
                        text(
                            "UPDATE companies SET "
                            + ", ".join(f"{k} = :{k}" for k in update)
                            + ", uid_detail_fetched_at = now() WHERE id = :id"
                        ),
                        {**update, "id": row_id},
                    )
                    stats["updated"] += 1
                    last_id = row_id
                    processed += 1
                    continue

            # No entity or no fields to update — still mark as fetched.
            stats["skipped_no_detail"] += 1
            db.execute(
                text("UPDATE companies SET uid_detail_fetched_at = now() WHERE id = :id"),
                {"id": row_id},
            )
            last_id = row_id
            processed += 1

        db.commit()

        if progress_cb:
            progress_cb(processed, total, stats)

    return stats
