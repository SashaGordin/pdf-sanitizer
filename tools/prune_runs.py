#!/usr/bin/env python3
"""Delete published run directories older than a configured retention window,
and delete any reviewed run's triage/ crop directory.

An explicit maintenance step, never run automatically by a normal sanitizer
invocation. Startup recovery for abandoned/crashed runs (staging directories
that never completed their atomic rename) is explicitly out of scope and is
left untouched by this step.

    .venv-anonymizer/bin/python tools/prune_runs.py
    .venv-anonymizer/bin/python tools/prune_runs.py --output-dir output/runs --retention-days 30
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path
from typing import Sequence

MODULE_PATH = Path(__file__).with_name("anonymize_construction_pdfs.py")
_spec = importlib.util.spec_from_file_location("pdf_sanitizer", MODULE_PATH)
sanitizer = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
sys.modules[_spec.name] = sanitizer
_spec.loader.exec_module(sanitizer)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--output-dir", type=Path, default=Path("output/runs"),
        help="root directory under which sanitizer runs are published",
    )
    parser.add_argument(
        "--retention-days", type=int, default=sanitizer.ResourceLimits().retention_days,
        help="run directories older than this many days are deleted",
    )
    args = parser.parse_args(argv)
    if args.retention_days <= 0:
        raise SystemExit("--retention-days must be a positive integer")
    removed = sanitizer.prune_expired_runs(args.output_dir, args.retention_days)
    if removed:
        for path in removed:
            print(f"removed {path}", file=sys.stderr)
    print(f"pruned {len(removed)} run director{'y' if len(removed) == 1 else 'ies'} "
          f"older than {args.retention_days} day(s)", file=sys.stderr)

    reviewed_triage_removed = sanitizer.prune_reviewed_triage(args.output_dir)
    if reviewed_triage_removed:
        for path in reviewed_triage_removed:
            print(f"removed {path}", file=sys.stderr)
    print(f"pruned {len(reviewed_triage_removed)} reviewed triage director"
          f"{'y' if len(reviewed_triage_removed) == 1 else 'ies'}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
