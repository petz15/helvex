"""Phase-0 experiment: determine the multi-token search semantics of the
Swiss UID V5.0 PublicServices `Search` operation.

Background
----------
We empirically proved that `organisationName` matching is *exact word-token*
(tokenize by spaces, match each token exactly, case-insensitive, accent-folded)
— NOT SQL `LIKE '%term%'` contains. The single open question that decides the
whole sweep redesign is:

    When you pass TWO tokens in `organisationName`, does the API
      (A) AND them    — every result must contain BOTH words,
      (B) OR them     — results contain EITHER word, or
      (C) ignore all  — only the first token (or none) matters?

If AND: we can refine any capped 30-result word bucket by pairing it with a
second discriminating token, escaping the hard cap legitimately.
If OR / ignored: ultra-common words are unrecoverable beyond 30 and the
word-dictionary sweep degrades to "every company must own one rare-enough word".

This script runs three controlled probes and prints a verdict. It reuses the
production SOAP client (`app.clients.uid_client._search_page`), so it honors the
same 2s inter-call delay and rate-limit backoff.

Run from project root:
    python scripts/uid_phase0_experiment.py
    python scripts/uid_phase0_experiment.py --common MÜLLER --domA TREUHAND --domB GARAGE

The probes (read the comments — the design is the experiment):

  Probe 1  baseline single tokens          establishes per-word result sets + whether each caps at 30
  Probe 2  common + NONSENSE token         AND ⇒ 0 results; OR/ignored ⇒ ≈ common's set      (separates AND from {OR,ignored})
  Probe 3  two real disjoint-domain words  AND ⇒ all results contain both; OR ⇒ mix of both domains;
                                           ignored ⇒ identical to first token alone           (separates OR from ignored)
  Probe 4  order swap (B A vs A B)         detects whether token order changes results
"""
from __future__ import annotations

import argparse
import os
import sys
import unicodedata

# Running this file directly puts scripts/ on sys.path, not the project root.
# Add the repo root so `app.*` imports resolve.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Windows consoles default to cp1252 and can't encode the box-drawing chars /
# umlauts used below. Force UTF-8 so output (and the MÜLLER token) renders.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except Exception:
        pass

# A token that should match no real company name (used to force the AND test).
NONSENSE_TOKEN = "ZQXJWK"


def _fold(s: str) -> str:
    """Lowercase + strip accents, mirroring the API's accent-folding behavior."""
    nfkd = unicodedata.normalize("NFKD", s.lower())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _tokens(name: str) -> set[str]:
    """Word tokens of a company name, folded and split on non-alphanumerics."""
    folded = _fold(name)
    out: set[str] = set()
    cur = []
    for ch in folded:
        if ch.isalnum():
            cur.append(ch)
        elif cur:
            out.add("".join(cur))
            cur = []
    if cur:
        out.add("".join(cur))
    return out


def _query(term: str) -> tuple[list[dict], bool]:
    """Run one Search call. Returns (entities, capped). Empty list on failure."""
    from app.clients.uid_client import search_entities

    try:
        return search_entities(name=term)
    except Exception as exc:  # rate-limit-after-retries, transport, etc.
        print(f"    !! query {term!r} failed: {exc}")
        return [], False


def _names(entities: list[dict]) -> list[str]:
    return [e.get("name") or "" for e in entities]


def _uidset(entities: list[dict]) -> set[str]:
    return {e["uid"] for e in entities if e.get("uid")}


def _classify(entities: list[dict], tok_a: str, tok_b: str) -> dict[str, int]:
    """Count results by which of the two query tokens they actually contain."""
    fa, fb = _fold(tok_a), _fold(tok_b)
    both = only_a = only_b = neither = 0
    for e in entities:
        toks = _tokens(e.get("name") or "")
        ha, hb = fa in toks, fb in toks
        if ha and hb:
            both += 1
        elif ha:
            only_a += 1
        elif hb:
            only_b += 1
        else:
            neither += 1
    return {"both": both, "only_a": only_a, "only_b": only_b, "neither": neither}


def _print_sample(entities: list[dict], n: int = 8) -> None:
    for nm in _names(entities)[:n]:
        print(f"      · {nm}")
    if len(entities) > n:
        print(f"      … (+{len(entities) - n} more)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--common", default="MÜLLER",
                    help="A common token (likely caps at 30 alone).")
    ap.add_argument("--domA", default="TREUHAND",
                    help="Real token, domain A (caps or near-cap alone).")
    ap.add_argument("--domB", default="GARAGE",
                    help="Real token, domain B — disjoint from domain A.")
    args = ap.parse_args()

    common, dom_a, dom_b = args.common, args.domA, args.domB

    print("=" * 72)
    print("UID Phase-0 experiment: multi-token search semantics (AND / OR / ignored)")
    print("=" * 72)
    print(f"tokens:  common={common!r}  domainA={dom_a!r}  domainB={dom_b!r}")
    print(f"nonsense token: {NONSENSE_TOKEN!r}")
    print("(each call sleeps the production inter-call delay; this takes ~1 min)\n")

    # ── Probe 1: single-token baselines ──────────────────────────────────────
    print("── Probe 1: single-token baselines " + "-" * 36)
    base: dict[str, tuple[list[dict], bool]] = {}
    for tok in (common, dom_a, dom_b, NONSENSE_TOKEN):
        ents, capped = _query(tok)
        base[tok] = (ents, capped)
        flag = "CAPPED@30" if capped else "complete"
        print(f"  {tok:<12} → {len(ents):>3} results [{flag}]")
    print()

    if not base[common][0]:
        print("ABORT: the 'common' token returned nothing — pick a token that exists.")
        return 1
    if base[NONSENSE_TOKEN][0]:
        print(f"WARN: nonsense token {NONSENSE_TOKEN!r} returned results — pick another "
              f"and re-run; the AND/OR test below is unreliable otherwise.")

    # ── Probe 2: common + NONSENSE  (AND ⇒ 0; OR/ignored ⇒ ≈ common) ─────────
    print("── Probe 2: common + NONSENSE token " + "-" * 35)
    q2 = f"{common} {NONSENSE_TOKEN}"
    e2, capped2 = _query(q2)
    common_uids = _uidset(base[common][0])
    overlap2 = len(_uidset(e2) & common_uids)
    print(f"  query {q2!r} → {len(e2)} results"
          + (" [CAPPED@30]" if capped2 else ""))
    print(f"  overlap with bare {common!r} set: {overlap2}/{len(common_uids)}")
    if len(e2) == 0:
        probe2 = "AND"           # adding an impossible token killed all results
    elif overlap2 >= max(1, int(0.5 * len(common_uids))):
        probe2 = "NOT_AND"       # nonsense token had no effect ⇒ OR or ignored
    else:
        probe2 = "INCONCLUSIVE"
    print(f"  ⇒ probe-2 reading: {probe2}\n")

    # ── Probe 3: two real disjoint-domain words ──────────────────────────────
    print("── Probe 3: two real disjoint-domain words " + "-" * 28)
    q3 = f"{dom_a} {dom_b}"
    e3, capped3 = _query(q3)
    cls = _classify(e3, dom_a, dom_b)
    a_uids = _uidset(base[dom_a][0])
    same_as_a = len(_uidset(e3) & a_uids) == len(e3) == len(a_uids) and len(e3) > 0
    print(f"  query {q3!r} → {len(e3)} results"
          + (" [CAPPED@30]" if capped3 else ""))
    print(f"  contains BOTH={cls['both']}  only-{dom_a}={cls['only_a']}  "
          f"only-{dom_b}={cls['only_b']}  neither={cls['neither']}")
    _print_sample(e3)
    a_alone = len(base[dom_a][0])
    b_alone = len(base[dom_b][0])
    if len(e3) and cls["both"] == len(e3):
        probe3 = "AND"                         # every result has both tokens
    elif len(e3) == 0 and a_alone and b_alone:
        probe3 = "AND"                         # each word exists alone, none share both ⇒ AND narrowed to 0
    elif cls["only_a"] and cls["only_b"]:
        probe3 = "OR"
    elif same_as_a or (cls["only_a"] and not cls["only_b"] and not cls["both"]):
        probe3 = "IGNORED(first-token-only)"
    else:
        probe3 = "INCONCLUSIVE"
    print(f"  ⇒ probe-3 reading: {probe3}\n")

    # ── Probe 4: order sensitivity ───────────────────────────────────────────
    print("── Probe 4: token order (B A vs A B) " + "-" * 34)
    q4 = f"{dom_b} {dom_a}"
    e4, _ = _query(q4)
    same_order = _uidset(e3) == _uidset(e4)
    print(f"  query {q4!r} → {len(e4)} results;  identical UID set to {q3!r}? "
          f"{same_order}\n")

    # ── Verdict ──────────────────────────────────────────────────────────────
    print("=" * 72)
    print("VERDICT")
    print("=" * 72)
    readings = {probe2, probe3}
    if probe3 == "AND" and probe2 == "AND":
        verdict = "AND — multi-token narrows results. Capped buckets ARE refinable."
        impl = ("GO: build the word-dictionary sweep with second-token refinement "
                "for any bucket that hits 30. Completeness is recoverable per word.")
    elif probe3 == "OR":
        verdict = "OR — multi-token widens results."
        impl = ("Cannot refine a capped bucket by adding a token. Sweep relies on "
                "every company owning at least one <30 (rare-enough) word.")
    elif probe3.startswith("IGNORED"):
        verdict = "IGNORED — only the first token matters; extra tokens are dropped."
        impl = ("Same consequence as OR for refinement: no escape from the 30-cap. "
                "Lean hard on a rare-word vocabulary (surnames, place+trade combos).")
    else:
        verdict = f"INCONCLUSIVE (probe2={probe2}, probe3={probe3})."
        impl = ("Re-run with different tokens — ideally a 'common' word that caps at "
                "30 and two real words from clearly disjoint domains.")
    print(f"  semantics : {verdict}")
    print(f"  order     : {'order-insensitive' if same_order else 'ORDER MATTERS — investigate'}")
    print(f"  implication: {impl}")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
