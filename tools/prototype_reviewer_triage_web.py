#!/usr/bin/env python3
"""Throwaway prototype: opens the reviewer-triage web prototype (issue #7, Phase 4).

Third iteration. After the CLI (too low fidelity to judge the workflow) and
a 3-variant web comparison (full-screen stack / inbox / kanban board), the
reaction was: inbox layout wins, but strip out anything a non-technical
reviewer doesn't need -- no residual/NER labels, no occurrence counts or
match scores, no denylist/lexicon/manifest previews. A reviewer's only job
is "is this sensitive or not", with an optional free-text note; the four
dispositions (sensitive/safe/duplicate/escalate) stay, just relabeled in
plain language.

Everything is client-side (synthetic findings baked into the HTML, matching
the same residual/ner_review.findings shapes as the CLI's --demo mode) --
no server, no persistence, nothing here writes real policy or manifest
files. Delete once the real reviewer tooling ships.

    .venv-anonymizer/bin/python tools/prototype_reviewer_triage_web.py
"""

import pathlib
import webbrowser

HTML_PATH = pathlib.Path(__file__).parent / "prototype_reviewer_triage_web.html"

if __name__ == "__main__":
    url = HTML_PATH.resolve().as_uri()
    opened = webbrowser.open(url)
    print(f"{'Opened' if opened else 'Open this in your browser:'} {url}")
    print("Flip between variants with the floating bar at the bottom (or arrow keys).")
