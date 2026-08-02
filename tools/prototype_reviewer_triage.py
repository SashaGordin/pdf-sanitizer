#!/usr/bin/env python3
"""Throwaway prototype: a CLI reviewer-triage flow for Phase 4.

Resolves issue #7 ("Prototype the human reviewer workflow"). This is a
cheap, rough artifact to react to the *shape* of PRODUCTION-READINESS-PLAN.md
Phase 4's reviewer workflow (walk findings, disposition each, preview
promotions, remind about rerun/final-review) — not a real build. Delete once
the real reviewer tooling ships.

Reads the exact residual/ner_review.findings schema `verify_output()` already
writes into a run's report.json (tools/anonymize_construction_pdfs.py), so
disposition decisions can be reacted to against real finding shapes without
inventing new ones.

    .venv-anonymizer/bin/python tools/prototype_reviewer_triage.py --demo
    .venv-anonymizer/bin/python tools/prototype_reviewer_triage.py --report output/runs/<run_id>/report.json
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

DISPOSITIONS = {"1": "sensitive", "2": "safe", "3": "duplicate", "4": "escalate"}

# Synthetic, schema-accurate stand-in for a real report.json — no run fixture
# exists in this repo to load instead. Shapes use masked-shape-style
# placeholders (A/a/9), matching masked_shape() in anonymize_construction_pdfs.py.
DEMO_REPORT = {
    "documents": [
        {
            "document_id": "sanitized_document_01",
            "residuals": [
                {
                    "page": 3,
                    "category": "denylist",
                    "shape": "Aaaaa Aaaaa Aaaaaaaaaaa LLC",
                    "crop": "triage/sanitized_document_01/residual_0001_page0003_denylist.png",
                },
                {
                    "page": 12,
                    "category": "address",
                    "shape": "999 Aaaaa Aa, Aaaaaaa, XX 99999",
                    "crop": "triage/sanitized_document_01/residual_0002_page0012_address.png",
                },
            ],
            "ner_review": {
                "findings": [
                    {
                        "label": "PERSON",
                        "shape": "Aaaa A. Aaaaaaa",
                        "occurrences": 7,
                        "pages": [1, 4, 9],
                        "score_max": 0.91,
                        "zone": "title_block",
                        "evidence": [],
                        "crop": "triage/sanitized_document_01/ner/ner_0001_page0001_person.png",
                    },
                    {
                        "label": "ORG",
                        "shape": "Aaaaaaa Aaaaaaaaxx, Aac.",
                        "occurrences": 3,
                        "pages": [2, 5],
                        "score_max": 0.77,
                        "zone": None,
                        "evidence": ["manufacturer_context"],
                        "crop": "triage/sanitized_document_01/ner/ner_0002_page0002_org.png",
                    },
                ]
            },
        }
    ]
}


def load_findings(report: dict) -> list[dict]:
    findings = []
    for doc in report.get("documents", []):
        doc_id = doc.get("document_id")
        for residual in doc.get("residuals", []):
            findings.append({
                "document_id": doc_id,
                "source": "residual",
                "id": f"{doc_id}:residual:{residual['page']}:{residual['category']}",
                "category": residual["category"],
                "shape": residual["shape"],
                "pages": [residual["page"]],
                "occurrences": 1,
                "score_max": None,
                "crop": residual.get("crop"),
            })
        for finding in doc.get("ner_review", {}).get("findings", []):
            findings.append({
                "document_id": doc_id,
                "source": "ner",
                "id": f"{doc_id}:ner:{finding['label']}:{finding['shape']}",
                "category": finding["label"],
                "shape": finding["shape"],
                "pages": finding.get("pages", []),
                "occurrences": finding.get("occurrences", 1),
                "score_max": finding.get("score_max"),
                "crop": finding.get("crop"),
            })
    return findings


def prompt_disposition(finding: dict, run_dir: Path | None) -> str:
    print()
    print(f"[{finding['source']}] {finding['category']} — pages {finding['pages']}")
    print(f"  shape: {finding['shape']}")
    if finding["occurrences"] > 1:
        score = f", max score {finding['score_max']}" if finding["score_max"] else ""
        print(f"  occurrences: {finding['occurrences']}{score}")
    if finding["crop"]:
        crop_path = (run_dir / finding["crop"]) if run_dir else Path(finding["crop"])
        note = "" if crop_path.exists() else "  (not found — demo data)"
        print(f"  crop: {crop_path}{note}")
    while True:
        choice = input("  disposition [1=sensitive 2=safe 3=duplicate 4=escalate]: ").strip()
        if choice in DISPOSITIONS:
            return DISPOSITIONS[choice]
        print("  enter 1-4")


def summarize_promotions(decisions: list[dict], findings_by_id: dict[str, dict]) -> None:
    # Preview only: reviewer decisions must never modify global policy
    # automatically (PRODUCTION-READINESS-PLAN.md Phase 4) — actual promotion
    # is separate tooling with its own tests and code review.
    duplicates = [d for d in decisions if d["disposition"] == "duplicate"]
    safes = [d for d in decisions if d["disposition"] == "safe"]
    if duplicates:
        print("\nWould propose adding to config/denylist.local.json (confirmed repeated identifiers):")
        for decision in duplicates:
            finding = findings_by_id[decision["finding_id"]]
            print(f"  - {finding['category']}: {finding['shape']}")
    if safes:
        print("\nWould propose adding to a scoped rule / config/lexicons/*.json (confirmed false positives):")
        for decision in safes:
            finding = findings_by_id[decision["finding_id"]]
            print(f"  - {finding['category']}: {finding['shape']}")
    print("\n(Nothing written to policy files by this prototype — promotion is separate tooling.)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--report", type=Path, help="path to a run's report.json")
    parser.add_argument("--demo", action="store_true", help="use built-in synthetic findings instead of --report")
    parser.add_argument("--reviewer", default=None, help="reviewer name/id; prompted if omitted")
    parser.add_argument(
        "--decisions-out", type=Path, default=None,
        help="where to write decisions.json (default: alongside --report, or ./decisions.json in --demo mode)",
    )
    args = parser.parse_args()

    if not args.demo and not args.report:
        parser.error("pass --report PATH or --demo")

    if args.demo:
        report = DEMO_REPORT
        run_dir = None
        decisions_out = args.decisions_out or Path("decisions.json")
    else:
        report = json.loads(args.report.read_text())
        run_dir = args.report.parent
        decisions_out = args.decisions_out or run_dir / "decisions.json"

    findings = load_findings(report)
    if not findings:
        print("No findings to review.")
        return

    print(f"{len(findings)} finding(s) to review.")
    reviewer = args.reviewer or input("Reviewer name: ").strip()
    decisions = []
    for finding in findings:
        disposition = prompt_disposition(finding, run_dir)
        decisions.append({
            "finding_id": finding["id"],
            "disposition": disposition,
            "reviewer": reviewer,
            "decided_at": datetime.now(timezone.utc).isoformat(),
        })

    decisions_out.write_text(json.dumps({"reviewer": reviewer, "decisions": decisions}, indent=2) + "\n")
    print(f"\nWrote {len(decisions)} decision(s) to {decisions_out}")

    findings_by_id = {finding["id"]: finding for finding in findings}
    summarize_promotions(decisions, findings_by_id)

    print("\nWould record in manifest.json:")
    print(json.dumps({
        "review": {
            "status": "complete",
            "reviewer": reviewer,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        },
    }, indent=2))

    print("\nNext (not automated by this prototype):")
    print("  5. Rerun the complete pipeline after any policy change.")
    print("  6. Perform a final page-by-page visual review before RELEASED.")


if __name__ == "__main__":
    main()
