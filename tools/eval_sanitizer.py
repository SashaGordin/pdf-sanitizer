#!/usr/bin/env python3
"""Score the sanitizer's decisions against a labelled golden set.

Answers three questions that eyeballing a report cannot:

  RECALL          of the identifiers that MUST be redacted, how many are?
                  A miss is an NDA breach. This is the number that gates release.
  OVER-REDACTION  of the things that must survive, how many get destroyed?
                  This is technical content lost from the deliverable.
  NOISE           of the things that must survive, how many still reach the
                  reviewer as candidates? This is reviewer minutes.

Every tuning change should be judged by the delta on these three, not by
whether a spot-check looks better.

Local-only and read-only: no PDFs are opened and nothing is written.

    .venv-anonymizer/bin/python tools/eval_sanitizer.py
    .venv-anonymizer/bin/python tools/eval_sanitizer.py --golden tests/golden/mlk_labels.json
"""

from __future__ import annotations

import argparse
import collections
import importlib.util
import json
import sys
from pathlib import Path

MODULE_PATH = Path(__file__).with_name("anonymize_construction_pdfs.py")
_spec = importlib.util.spec_from_file_location("pdf_sanitizer", MODULE_PATH)
sanitizer = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
sys.modules[_spec.name] = sanitizer
_spec.loader.exec_module(sanitizer)

MUST_REDACT = {"party"}
MUST_SURVIVE = {"manufacturer", "boilerplate", "structural", "garbage", "hard_negative"}


def classify(term: str, matcher, lexicons) -> tuple[str, str]:
    """What the pipeline does with this string: redact, suppress, or pass."""
    suppressed = lexicons.suppression_reason(term) if lexicons else None
    hit = next(matcher.finditer(term), None)
    if hit is not None:
        # The denylist is authoritative and is never suppressed.
        return "redacted", f"denylist:{hit.group()[:24]}"
    if suppressed:
        return "suppressed", suppressed
    return "passed", ""


def evaluate(golden_path: Path, denylist_path: Path, lexicon_dir: Path, allowlist_path: Path) -> dict:
    """Score the golden set. Returns metrics plus per-entry rows.

    Two independent paths are scored, because a term can reach the pipeline
    two ways and the correct answer differs:

      detector path   -- a located candidate. Scored by suppression_reason.
      derivation path -- a term --propose-denylist would suggest. Scored by
                         rejects_proposed_term, which is stricter, because a
                         proposal that gets confirmed becomes a global
                         destructive redaction rule.
    """
    golden = json.loads(golden_path.read_text(encoding="utf-8"))
    lexicons = sanitizer.load_lexicons(lexicon_dir, allowlist_path)
    matcher = sanitizer.DenylistMatcher(sanitizer.load_denylist(denylist_path))

    outcomes: dict[str, collections.Counter[str]] = collections.defaultdict(collections.Counter)
    rows: list[dict] = []
    failures: list[tuple[str, str, str, str]] = []

    for entry in golden["entries"]:
        term, verdict = entry["term"], entry["verdict"]
        action, why = classify(term, matcher, lexicons)
        proposal_block = lexicons.rejects_proposed_term(term)
        outcomes[verdict][action] += 1
        if proposal_block:
            outcomes[verdict]["derivation_rejected"] += 1
        rows.append({
            "verdict": verdict, "action": action, "why": why,
            "derivation": proposal_block or "proposed", "term": term,
        })
        if verdict in MUST_REDACT and action != "redacted":
            failures.append(("LEAK", verdict, term, f"{action} ({why})" if why else action))
        elif verdict in MUST_SURVIVE and action == "redacted":
            failures.append(("OVER-REDACTED", verdict, term, why))
        elif verdict == "hard_negative" and action == "suppressed":
            failures.append(("WRONGLY SUPPRESSED", verdict, term, why))
        elif verdict in MUST_REDACT and proposal_block:
            failures.append(("DERIVATION WOULD DROP", verdict, term, proposal_block))

    party_total = sum(v for k, v in outcomes["party"].items() if k != "derivation_rejected")
    survive_total = sum(
        v for verdict in MUST_SURVIVE for k, v in outcomes[verdict].items()
        if k != "derivation_rejected"
    )
    noisy = {"manufacturer", "boilerplate", "structural", "garbage"}
    noisy_total = sum(
        v for verdict in noisy for k, v in outcomes[verdict].items() if k != "derivation_rejected"
    )
    return {
        "entries": len(golden["entries"]),
        "denylist_terms": len(matcher.terms),
        "lexicons": lexicons,
        "outcomes": outcomes,
        "rows": rows,
        "failures": failures,
        "party_total": party_total,
        "party_redacted": outcomes["party"]["redacted"],
        "survive_total": survive_total,
        "survive_redacted": sum(outcomes[v]["redacted"] for v in MUST_SURVIVE),
        "noisy_total": noisy_total,
        "noisy_handled": sum(
            outcomes[v]["suppressed"] + min(
                outcomes[v]["derivation_rejected"], outcomes[v]["passed"],
            )
            for v in noisy
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    root = Path(__file__).parents[1]
    parser.add_argument("--golden", type=Path, default=root / "tests/golden/mlk_labels.json")
    parser.add_argument("--denylist", type=Path, default=root / "config/denylist.local.json")
    parser.add_argument("--lexicons", type=Path, default=root / "config/lexicons")
    parser.add_argument("--allowlist", type=Path, default=root / "config/allowlist.shared.json")
    parser.add_argument("--verbose", action="store_true", help="list every entry, not just failures")
    args = parser.parse_args()

    result = evaluate(args.golden, args.denylist, args.lexicons, args.allowlist)
    outcomes, lexicons = result["outcomes"], result["lexicons"]

    print(f"golden set: {result['entries']} entries from {args.golden}")
    print(f"denylist  : {result['denylist_terms']} terms")
    print(f"lexicons  : {len(lexicons.defined_terms)} defined, {len(lexicons.structural)} structural, "
          f"{len(lexicons.boilerplate)} boilerplate, {len(lexicons.allowlist)} allowlist\n")

    header = (f"{'verdict':<15}{'n':>4}{'redacted':>10}{'suppressed':>12}{'passed':>8}"
              f"{'deriv.blocked':>15}")
    print(header)
    print("-" * len(header))
    for verdict in ("party", "manufacturer", "boilerplate", "structural", "garbage", "hard_negative"):
        counts = outcomes.get(verdict)
        if not counts:
            continue
        total = sum(v for k, v in counts.items() if k != "derivation_rejected")
        print(f"{verdict:<15}{total:>4}{counts['redacted']:>10}{counts['suppressed']:>12}"
              f"{counts['passed']:>8}{counts['derivation_rejected']:>15}")

    def pct(n: int, d: int) -> str:
        return f"{100 * n / d:5.1f}%" if d else "    n/a"

    print(f"\n  RECALL           {pct(result['party_redacted'], result['party_total'])}   "
          f"({result['party_redacted']}/{result['party_total']} identifiers redacted)   <- release gate")
    print(f"  OVER-REDACTION   {pct(result['survive_redacted'], result['survive_total'])}   "
          f"({result['survive_redacted']}/{result['survive_total']} must-survive entries destroyed)")
    print(f"  NOISE HANDLED    {pct(result['noisy_handled'], result['noisy_total'])}   "
          f"({result['noisy_handled']}/{result['noisy_total']} kept out of the review queue, "
          f"by suppression or by derivation refusing to propose them)")

    if args.verbose:
        print("\nall entries:")
        for row in sorted(result["rows"], key=lambda r: (r["verdict"], r["term"])):
            print(f"  {row['verdict']:<14}{row['action']:<12}{row['why'][:30]:<32}"
                  f"{row['derivation'][:26]:<28}{row['term'][:40]}")

    if result["failures"]:
        print(f"\n{len(result['failures'])} FAILURE(S):")
        for kind, verdict, term, why in result["failures"]:
            print(f"  [{kind}] ({verdict}) {term[:60]}  {why}")
        return 1
    print("\nAll golden entries behave as labelled.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
