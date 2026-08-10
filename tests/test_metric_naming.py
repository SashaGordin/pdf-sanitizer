"""tools/eval_sanitizer.py scores "policy-vocabulary recall" (a string check,
never opens a PDF); tools/eval_corpus.py scores "document-level recall" (the
real sanitizer run end-to-end against the locked corpus). They are not the
same number and must never be printed, documented, or logged as an
unqualified "recall" — this is the lint-style check ticket 05 asks for,
expressed as a plain unit test rather than a separate lint tool, matching
how this repo already enforces invariants (e.g. assertNotIn("shape", ...)).
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

TOOLS_DIR = Path(__file__).parents[1] / "tools"
BARE_RECALL = re.compile(r"\brecall\b", re.IGNORECASE)
QUALIFIERS = ("policy-vocabulary ", "document-level ")


def unqualified_recall_occurrences(text: str) -> list[str]:
    occurrences = []
    for match in BARE_RECALL.finditer(text):
        prefix = text[max(0, match.start() - 20):match.start()].casefold()
        if not any(prefix.endswith(qualifier) for qualifier in QUALIFIERS):
            line_start = text.rfind("\n", 0, match.start()) + 1
            line_end = text.find("\n", match.end())
            occurrences.append(text[line_start:line_end if line_end != -1 else None].strip())
    return occurrences


class MetricNamingTest(unittest.TestCase):
    def test_eval_sanitizer_never_uses_a_bare_recall(self) -> None:
        text = (TOOLS_DIR / "eval_sanitizer.py").read_text(encoding="utf-8")
        self.assertEqual(unqualified_recall_occurrences(text), [])

    def test_eval_corpus_never_uses_a_bare_recall(self) -> None:
        text = (TOOLS_DIR / "eval_corpus.py").read_text(encoding="utf-8")
        self.assertEqual(unqualified_recall_occurrences(text), [])

    def test_guard_itself_catches_a_bare_recall(self) -> None:
        # Pins the guard's own behavior: it must not be a no-op that always
        # passes regardless of content.
        self.assertEqual(unqualified_recall_occurrences("the recall number"), ["the recall number"])
        self.assertEqual(unqualified_recall_occurrences("the document-level recall number"), [])


if __name__ == "__main__":
    unittest.main()
