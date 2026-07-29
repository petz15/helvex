#!/usr/bin/env python
"""Validate whether a trigger word (kann/peut/può) reliably precedes generic
boilerplate, using semantic similarity instead of exact-string frequency.

Frequency-based discovery (scripts/analyze_boilerplate.py) can only find
sentences that recur near-verbatim across many companies. It cannot tell
whether a *uniquely worded* sentence following "kann"/"peut" is still
boilerplate in substance -- which is exactly the gap this script fills: it
embeds each candidate sentence (the trigger sentence + the next couple) and
scores it against a small set of known-generic exemplar sentences, so
paraphrases count as boilerplate too, not just verbatim clones.

For each company where the trigger word appears outside the first sentence,
this reports:
  - the highest similarity found in the trigger sentence + next 2 sentences
  - the offset (0 = trigger sentence itself, 1/2 = the next couple) where
    that peak occurs
  - a proposed semantic truncation point, for comparison against the
    hand-picked regex-anchor rules

Usage:
    python scripts/validate_boilerplate_similarity.py --lang de
    python scripts/validate_boilerplate_similarity.py --lang fr --full --out scored_fr.csv
    python scripts/validate_boilerplate_similarity.py --lang de --limit 5000 --threshold 0.70

Dependencies (if running outside the container): the same ones the app
already needs for NOGA/purpose embeddings — sentence-transformers, torch.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import Counter
from contextlib import contextmanager
from pathlib import Path

import numpy as np

# Make sure the app package is importable when running from project root
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models.company import Company
from app.services.ml.boilerplate_analysis import _split_sentences  # reuse production sentence splitter
from app.services.ml.embeddings import embed_texts


@contextmanager
def _make_session(db_url: str | None):
    """Yield a SQLAlchemy Session, optionally overriding the URL from .env."""
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


TRIGGER_PATTERNS: dict[str, re.Pattern] = {
    "de": re.compile(r"\bkann\b", re.IGNORECASE),
    "fr": re.compile(r"\b(?:peut|peuvent)\b", re.IGNORECASE),
    "it": re.compile(r"\b(?:può|possono)\b", re.IGNORECASE),
}

# Highest-confidence generic ancillary-powers sentences validated manually
# during the reverse-sweep analysis. Paraphrases of these (different wording,
# same content) should score high similarity too -- that's the whole point.
EXEMPLARS: dict[str, list[tuple[str, str]]] = {
    "de": [
        ("branch/subsidiary",
         "Die Gesellschaft kann Zweigniederlassungen und Tochtergesellschaften im In- und Ausland "
         "errichten und sich an anderen Unternehmen im In- und Ausland beteiligen sowie alle "
         "Geschäfte tätigen, die direkt oder indirekt mit ihrem Zweck in Zusammenhang stehen."),
        ("real estate",
         "Die Gesellschaft kann im In- und Ausland Grundeigentum erwerben, belasten, veräussern "
         "und verwalten."),
        ("financing/guarantees",
         "Sie kann auch Finanzierungen für eigene oder fremde Rechnung vornehmen sowie Garantien "
         "und Bürgschaften für Tochtergesellschaften und Dritte eingehen."),
    ],
    "fr": [
        ("branch/subsidiary",
         "La société peut créer des succursales en Suisse et à l'étranger, participer à d'autres "
         "entreprises en Suisse et à l'étranger, acquérir des entreprises visant un but identique "
         "ou analogue, ou fusionner avec de telles entreprises, faire toutes opérations et conclure "
         "tous contrats propres à développer et à étendre son but ou s'y rapportant directement ou "
         "indirectement."),
        ("financing/guarantees",
         "La société peut accorder des prêts ou des garanties à des associés ou des tiers, si cela "
         "favorise ses intérêts."),
    ],
    "it": [
        ("real estate/LAFE",
         "Beni immobili in Svizzera o partecipazioni a società e imprese immobiliari con beni "
         "immobili in Svizzera possono tuttavia essere acquisiti (senza procedura di assoggettamento "
         "LAFE) solo a condizione che gli immobili siano destinati all'esercizio di un'attività "
         "economica."),
        ("financing",
         "I mezzi finanziari necessari al compimento dello scopo sociale possono essere procurati "
         "anche mediante mutui ipotecari o prestiti semplici."),
        ("branch offices",
         "Possono essere costituite succursali sia in Svizzera sia all'estero."),
    ],
}

# Trigger sentence + next couple, per the observed pattern of "immediately or
# within the next couple of sentences".
WINDOW_SIZE = 3

# Batch size for the --full path. A single unbatched query over the whole
# language population blows past Postgres's statement_timeout on a 700k-row
# table -- keyset-paginate instead (same pattern as recalculate_google_scores).
BATCH_SIZE = 2000


def iter_companies(db, lang: str, *, full: bool, limit: int, ids: list[int] | None = None):
    """Yield (id, name, purpose) rows, streaming in batches for --full."""
    if ids:
        rows = (
            db.query(Company.id, Company.name, Company.purpose)
            .filter(Company.id.in_(ids))
            .all()
        )
        yield from rows
        return

    if not full:
        from sqlalchemy import func
        rows = (
            db.query(Company.id, Company.name, Company.purpose)
            .filter(Company.purpose_language == lang, Company.purpose.isnot(None))
            .order_by(func.random())
            .limit(limit)
            .all()
        )
        yield from rows
        return

    last_id = 0
    while True:
        batch = (
            db.query(Company.id, Company.name, Company.purpose)
            .filter(
                Company.purpose_language == lang,
                Company.purpose.isnot(None),
                Company.id > last_id,
            )
            .order_by(Company.id)
            .limit(BATCH_SIZE)
            .all()
        )
        if not batch:
            break
        yield from batch
        last_id = batch[-1][0]


def find_trigger_window(sentences: list[str], trigger_re: re.Pattern) -> tuple[int, list[str]] | None:
    """Return (trigger_idx, window_sentences) for the first trigger match outside
    sentence 1 (0-based index >= 1), or None if the trigger never appears there."""
    for i, s in enumerate(sentences):
        if i == 0:
            continue
        if trigger_re.search(s):
            return i, sentences[i: i + WINDOW_SIZE]
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--lang", required=True, choices=["de", "fr", "it"])
    parser.add_argument("--limit", type=int, default=3000, help="Random sample size (ignored with --full)")
    parser.add_argument("--full", action="store_true", help="Run over the entire language population instead of a sample")
    parser.add_argument("--threshold", type=float, default=0.72, help="Cosine similarity threshold for 'boilerplate-like'")
    parser.add_argument("--out", default=None, help="CSV output path (default: scored_<lang>.csv)")
    parser.add_argument(
        "--ids",
        default=None,
        help="Comma-separated company IDs to score directly, bypassing --limit/--full sampling entirely",
    )
    parser.add_argument(
        "--db-url",
        default=None,
        help="Override DB connection URL. If omitted, reads from .env via app.config.",
    )
    args = parser.parse_args()

    out_path = args.out or f"scored_{args.lang}.csv"
    id_list = [int(x) for x in args.ids.split(",")] if args.ids else None
    trigger_re = TRIGGER_PATTERNS[args.lang]
    exemplar_labels, exemplar_texts = zip(*EXEMPLARS[args.lang])

    print(f"Embedding {len(exemplar_texts)} reference exemplars for '{args.lang}'…")
    exemplar_vecs = embed_texts(list(exemplar_texts))

    with _make_session(args.db_url) as db:
        total_loaded = 0
        candidates: list[tuple[int, str, list[str], int, list[str]]] = []
        for cid, name, purpose in iter_companies(db, args.lang, full=args.full, limit=args.limit, ids=id_list):
            total_loaded += 1
            sentences = _split_sentences(purpose)
            found = find_trigger_window(sentences, trigger_re)
            if found:
                trigger_idx, window = found
                candidates.append((cid, name, sentences, trigger_idx, window))
            if total_loaded % 20000 == 0:
                print(f"  …scanned {total_loaded} companies so far ({len(candidates)} candidates)")

        print(f"Loaded {total_loaded} companies with purpose_language='{args.lang}'.")

        if not total_loaded:
            print("No companies found for this language.")
            return

        print(
            f"{len(candidates)} companies have the trigger outside sentence 1 "
            f"({100 * len(candidates) / total_loaded:.1f}% of the sample)."
        )
        if not candidates:
            print("Nothing to score.")
            return

        # Flatten all window sentences for one batched embedding call.
        flat_sentences: list[str] = []
        owner_index: list[int] = []
        for ci, (_, _, _, _, window) in enumerate(candidates):
            flat_sentences.extend(window)
            owner_index.extend([ci] * len(window))

        print(f"Embedding {len(flat_sentences)} candidate sentences…")
        sent_vecs = embed_texts(flat_sentences, show_progress=True)

        # Both matrices are L2-normalized -> dot product = cosine similarity.
        sims_matrix = sent_vecs @ exemplar_vecs.T  # (n_sentences, n_exemplars)
        max_sims = sims_matrix.max(axis=1)
        best_exemplar_idx = sims_matrix.argmax(axis=1)

        results: list[dict] = []
        ptr = 0
        for cid, name, sentences, trigger_idx, window in candidates:
            n = len(window)
            window_sims = max_sims[ptr: ptr + n]
            window_best_exemplar = best_exemplar_idx[ptr: ptr + n]
            ptr += n

            cutoff_offset = None
            for offset, sim in enumerate(window_sims):
                if sim >= args.threshold:
                    cutoff_offset = offset
                    break

            peak_offset = int(np.argmax(window_sims))
            cutoff_idx = trigger_idx + cutoff_offset if cutoff_offset is not None else None

            results.append({
                "id": cid,
                "name": name,
                "total_sentences": len(sentences),
                "trigger_idx": trigger_idx,
                "max_similarity_in_window": float(window_sims[peak_offset]),
                "best_exemplar": exemplar_labels[window_best_exemplar[peak_offset]],
                "cutoff_offset": cutoff_offset,
                "cutoff_idx": cutoff_idx,
                "kept_text": " ".join(sentences[:cutoff_idx]) if cutoff_idx else "",
                "removed_text": " ".join(sentences[cutoff_idx:]) if cutoff_idx else "",
            })

        n_cutoff = sum(1 for r in results if r["cutoff_idx"] is not None)
        print(
            f"\n{n_cutoff}/{len(results)} ({100 * n_cutoff / len(results):.1f}%) scored "
            f"above threshold {args.threshold} somewhere in the window "
            f"(trigger sentence + next {WINDOW_SIZE - 1})."
        )

        offsets = [r["cutoff_offset"] for r in results if r["cutoff_offset"] is not None]
        if offsets:
            print("Offset distribution (0 = trigger sentence itself):", dict(Counter(offsets)))

        sims_all = sorted(r["max_similarity_in_window"] for r in results)
        if sims_all:
            print(
                f"Similarity percentiles: "
                f"p10={sims_all[len(sims_all) // 10]:.3f}  "
                f"p50={sims_all[len(sims_all) // 2]:.3f}  "
                f"p90={sims_all[int(len(sims_all) * 0.9)]:.3f}"
            )

        with open(out_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(results[0].keys()))
            writer.writeheader()
            writer.writerows(results)
        print(f"\nWrote {len(results)} rows to {out_path}")


if __name__ == "__main__":
    main()
