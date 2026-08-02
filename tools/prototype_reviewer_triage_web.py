#!/usr/bin/env python3
"""Throwaway prototype: opens the reviewer-triage web prototype (issue #7, Phase 4).

Web-app iteration of tools/prototype_reviewer_triage.py, built after reacting
to the CLI version: the CLI's text-prompt fidelity was too low to judge the
reviewer workflow against, so this raises fidelity to a clickable page with
three structurally different variants of the review screen (full-screen
one-at-a-time stack, master-detail inbox, kanban board), switchable via
?variant=A/B/C.

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
