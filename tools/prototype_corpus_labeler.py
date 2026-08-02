#!/usr/bin/env python3
"""Throwaway prototype: opens the corpus labeling tool prototype (issue #11, Phase 3).

Round 1: a standalone bbox-drawing sketch. Draw a box on the synthetic
placeholder page, tag it with a category, a sensitivity decision, and an
expected disposition -- the schema locked in issue #5 (bbox + category +
sensitivity + disposition per item), a step up from the flat-string
tests/golden/mlk_labels.json entries used today.

Everything is client-side and in-memory (no server, no persistence, nothing
here writes real files); the export preview textarea is the only output, for
reacting to the shape, not for real labeling. Delete once the real labeling
tool ships.

    .venv-anonymizer/bin/python tools/prototype_corpus_labeler.py
"""

import pathlib
import webbrowser

HTML_PATH = pathlib.Path(__file__).parent / "prototype_corpus_labeler.html"

if __name__ == "__main__":
    url = HTML_PATH.resolve().as_uri()
    opened = webbrowser.open(url)
    print(f"{'Opened' if opened else 'Open this in your browser:'} {url}")
    print("Drag a box on the placeholder page to tag a labeled item.")
