#!/usr/bin/env python
"""LLM-based boilerplate candidate labelling.

Sends un-labelled boilerplate candidates to Claude for classification, then
writes the labels back and optionally promotes high-confidence BOILERPLATE terms
to active boilerplate_patterns.

This is a one-time bootstrap script; run it quarterly to catch new candidates
that weren't captured by the auto-promote threshold during cluster pipeline runs.

Usage:
    # Dry-run: print what would be sent, no API calls or DB writes
    python scripts/seed_boilerplate_llm.py --dry-run --limit 50

    # Real run (requires ANTHROPIC_API_KEY env var or --api-key)
    python scripts/seed_boilerplate_llm.py --limit 500

    # Override DB URL (for running outside the container)
    python scripts/seed_boilerplate_llm.py \\
        --db-url postgresql://user:pass@localhost:5432/zefix_analyzer \\
        --limit 500

    # Use a specific model (default: claude-haiku-4-5-20251001)
    python scripts/seed_boilerplate_llm.py --model claude-sonnet-4-6

    # Adjust auto-promote threshold (default: 0.85)
    python scripts/seed_boilerplate_llm.py --promote-threshold 0.80

Output:
    Prints per-batch progress and a final summary.
    Results are written to boilerplate_candidates.llm_label.
    BOILERPLATE candidates above --promote-threshold are promoted to boilerplate_patterns.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ── DB session helper ─────────────────────────────────────────────────────────

@contextmanager
def _make_session(db_url: str | None):
    if db_url:
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        engine = create_engine(db_url)
        Session = sessionmaker(bind=engine)
        db = Session()
    else:
        from app.database import SessionLocal
        db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ── Claude prompt ─────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
You are a Swiss company registry data quality expert.

Your task: classify a list of German/French/Italian terms extracted from Swiss company purpose texts.
Each term will either be:
  - BOILERPLATE: generic legal/administrative language that appears in almost every company and carries
    no discriminative industry information. Examples: "gesellschaft", "bezweckt", "insbesondere",
    "erwerb", "verwaltung", "betrieb", "alle damit", "sowie", "im in- und ausland"
  - SIGNAL: industry-specific or activity-specific vocabulary that meaningfully distinguishes
    one company type from another. Examples: "softwareentwicklung", "immobilien", "maschinenbau",
    "treuhand", "finanzberatung", "logistik", "gastronomie"
  - AMBIGUOUS: the term could be either, or you are uncertain. Use sparingly.

Respond ONLY with a JSON object of the form:
{
  "labels": {
    "term1": "BOILERPLATE",
    "term2": "SIGNAL",
    "term3": "AMBIGUOUS"
  }
}

Rules:
- Every term in the input must appear exactly once in "labels".
- No extra keys, no explanations, no markdown — only the JSON object.
- Common Swiss legal boilerplate markers: "bezweckt", "gesellschaft", "sowie", "insbesondere",
  "erwerb", "betrieb", "verwaltung", "gmbh", "ag", "im in- und ausland", "alle damit",
  "aller art", "tätigkeiten", "dienstleistungen" (when extremely generic).
"""

_USER_TEMPLATE = """\
Classify the following {n} terms. Return JSON only.

Terms:
{terms_json}
"""


def _classify_batch(terms: list[str], *, api_key: str, model: str, retries: int = 3) -> dict[str, str]:
    """Call Claude to classify a batch of terms. Returns term → label mapping."""
    from app.services.scoring.claude import claude_call

    user_msg = _USER_TEMPLATE.format(n=len(terms), terms_json=json.dumps(terms, ensure_ascii=False, indent=2))

    for attempt in range(1, retries + 1):
        try:
            data, _ = claude_call(
                _SYSTEM_PROMPT,
                user_msg,
                api_key=api_key,
                model=model,
                max_tokens=2048,
                cache_system=True,
                parse_json=True,
            )
            labels: dict[str, str] = data.get("labels", {})
            allowed = {"BOILERPLATE", "SIGNAL", "AMBIGUOUS"}
            return {k: v for k, v in labels.items() if v in allowed}
        except Exception as exc:
            if attempt == retries:
                print(f"  [WARN] Batch classification failed after {retries} attempts: {exc}", file=sys.stderr)
                return {}
            wait = 2 ** attempt
            print(f"  [WARN] Attempt {attempt} failed ({exc}), retrying in {wait}s…", file=sys.stderr)
            time.sleep(wait)
    return {}


# ── DB operations ─────────────────────────────────────────────────────────────

def _load_unlabelled_candidates(db, *, limit: int) -> list[Any]:
    """Return BoilerplateCandidate rows that haven't been LLM-labelled yet."""
    from app.models.boilerplate_candidate import BoilerplateCandidate

    return (
        db.query(BoilerplateCandidate)
        .filter(
            BoilerplateCandidate.llm_label.is_(None),
            BoilerplateCandidate.promoted.is_(False),
        )
        .order_by(BoilerplateCandidate.confidence.desc())
        .limit(limit)
        .all()
    )


def _apply_labels(
    db,
    candidates: list[Any],
    labels: dict[str, str],
    *,
    promote_threshold: float,
    dry_run: bool,
) -> dict[str, int]:
    """Write llm_label back and optionally promote BOILERPLATE candidates."""
    from app.crud.boilerplate import promote_boilerplate_candidate

    stats = {"labelled": 0, "boilerplate": 0, "signal": 0, "ambiguous": 0, "missed": 0, "promoted": 0}

    for candidate in candidates:
        label = labels.get(candidate.term)
        if not label:
            stats["missed"] += 1
            continue

        stats["labelled"] += 1
        if label == "BOILERPLATE":
            stats["boilerplate"] += 1
        elif label == "SIGNAL":
            stats["signal"] += 1
        else:
            stats["ambiguous"] += 1

        if dry_run:
            continue

        candidate.llm_label = label
        candidate.source_type = "llm_seeded"
        # If Claude says BOILERPLATE, nudge confidence up toward promote threshold
        if label == "BOILERPLATE" and (candidate.confidence or 0) < promote_threshold:
            candidate.confidence = max(candidate.confidence or 0.0, promote_threshold)
        db.flush()

        if label == "BOILERPLATE" and (candidate.confidence or 0) >= promote_threshold:
            pattern = promote_boilerplate_candidate(db, candidate, confidence_threshold=promote_threshold)
            if pattern:
                stats["promoted"] += 1

    if not dry_run:
        db.commit()

    return stats


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--limit", type=int, default=500,
        help="Max candidates to process (default 500)",
    )
    parser.add_argument(
        "--batch-size", type=int, default=50,
        help="Terms per Claude API call (default 50; keep ≤100 for best accuracy)",
    )
    parser.add_argument(
        "--model", default="claude-haiku-4-5-20251001",
        help="Claude model ID (default: claude-haiku-4-5-20251001 — cheapest and fast enough)",
    )
    parser.add_argument(
        "--api-key", default=None,
        help="Anthropic API key. Falls back to ANTHROPIC_API_KEY env var.",
    )
    parser.add_argument(
        "--promote-threshold", type=float, default=0.85,
        help="Confidence threshold above which BOILERPLATE labels are auto-promoted (default 0.85)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print what would be sent; do not call the API or write to DB",
    )
    parser.add_argument(
        "--db-url", default=None,
        help="Override DB connection URL. If omitted, reads from .env via app.config.",
    )
    args = parser.parse_args()

    api_key = args.api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key and not args.dry_run:
        print("ERROR: ANTHROPIC_API_KEY env var is required (or pass --api-key).", file=sys.stderr)
        sys.exit(1)

    with _make_session(args.db_url) as db:
        print(f"Loading up to {args.limit} unlabelled candidates…")
        candidates = _load_unlabelled_candidates(db, limit=args.limit)
        print(f"  Found {len(candidates)} candidates to label\n")

        if not candidates:
            print("Nothing to do — all candidates already have llm_label set.")
            return

        if args.dry_run:
            print("[DRY RUN] Would send these terms to Claude:")
            for c in candidates[:20]:
                print(f"  {c.confidence:.3f}  {c.term}")
            if len(candidates) > 20:
                print(f"  … and {len(candidates) - 20} more")
            print(f"\n[DRY RUN] Total API calls: {len(candidates) // args.batch_size + 1}")
            return

        # ── Process in batches ─────────────────────────────────────────────
        total_stats: dict[str, int] = {
            "labelled": 0, "boilerplate": 0, "signal": 0,
            "ambiguous": 0, "missed": 0, "promoted": 0,
        }
        n_batches = (len(candidates) + args.batch_size - 1) // args.batch_size

        for batch_i in range(n_batches):
            batch = candidates[batch_i * args.batch_size : (batch_i + 1) * args.batch_size]
            terms = [c.term for c in batch]

            print(f"  Batch {batch_i + 1}/{n_batches} ({len(terms)} terms)…", end=" ", flush=True)
            labels = _classify_batch(terms, api_key=api_key, model=args.model)
            print(f"got {len(labels)} labels")

            batch_stats = _apply_labels(
                db, batch, labels,
                promote_threshold=args.promote_threshold,
                dry_run=False,
            )
            for k, v in batch_stats.items():
                total_stats[k] = total_stats.get(k, 0) + v

            # Polite rate-limiting between batches
            if batch_i < n_batches - 1:
                time.sleep(1.0)

        # ── Summary ───────────────────────────────────────────────────────
        print("\n── Summary ─────────────────────────────────────────────")
        print(f"  Labelled:     {total_stats['labelled']}")
        print(f"  BOILERPLATE:  {total_stats['boilerplate']}")
        print(f"  SIGNAL:       {total_stats['signal']}")
        print(f"  AMBIGUOUS:    {total_stats['ambiguous']}")
        print(f"  Missed:       {total_stats['missed']}  (term not returned by Claude)")
        print(f"  Auto-promoted:{total_stats['promoted']}  (added to boilerplate_patterns)")
        print("\nDone. Re-run keyword extraction + clustering to apply new patterns.")


if __name__ == "__main__":
    main()
