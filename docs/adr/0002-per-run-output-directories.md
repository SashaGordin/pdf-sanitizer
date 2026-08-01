---
status: accepted
---

# Per-run-ID output directories, not shared fixed filenames

Every sanitizer run publishes to its own directory,
`output/runs/<run_id>/{sanitized.pdf, report.json, manifest.json}`, written to
a temp location and atomically renamed into place. Older run directories are
never overwritten; a rerun always gets a new run ID and a new directory.

Chosen over keeping the current shared `output/pdf/` directory with fixed
filenames (with or without a run-ID suffix), because the current-state
assessment found exactly the failure this prevents: nine days of reruns left
a `PASS` report describing hashes that no longer existed at their reported
paths, with nothing in the workflow enforcing the report-to-output pairing. A
filename suffix still relies on a human or a naming convention to pair a
report with its PDF correctly; a directory boundary makes the pairing
structurally inescapable — there is only one PDF and one report to find in
any given run directory.

Existing tooling that assumes fixed paths under `output/pdf/` (e.g.
`tools/verify_output_text.py` invocations, any ad hoc scripts) needs updating
to take a run directory instead.
