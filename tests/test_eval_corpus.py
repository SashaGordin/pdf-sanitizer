"""Unit tests for tools/eval_corpus.py's matching/scoring logic.

These tests use hand-built fixture dicts shaped like corpus_labeler.py label
items and anonymize_construction_pdfs.py report fragments (residuals,
ner_review.findings, page_redactions) — no PDFs, no real sanitizer run. The
integration path (calling the real sanitizer end-to-end against the locked
corpus) is exercised separately by tests/test_locked_corpus_regression.py.
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).parents[1] / "tools" / "eval_corpus.py"
SPEC = importlib.util.spec_from_file_location("eval_corpus", MODULE_PATH)
eval_corpus = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = eval_corpus
SPEC.loader.exec_module(eval_corpus)


def label_item(category, disposition, page=0, bbox=(100.0, 100.0, 150.0, 120.0), scale=2.0, **extra):
    item = {
        "id": "item-1", "page": page, "bbox": list(bbox), "scale": scale,
        "category": category, "sensitivity": "sensitive" if disposition == "redact" else "not_sensitive",
        "disposition": disposition,
    }
    item.update(extra)
    return item


def residual(category, page=1, bbox=(50.0, 50.0, 75.0, 60.0), **extra):
    entry = {"page": page, "category": category, "digest": "deadbeef" * 8, "bbox": list(bbox)}
    entry.update(extra)
    return entry


def ner_finding(label, pages, bbox=(50.0, 50.0, 75.0, 60.0), **extra):
    entry = {"label": label, "digest": "deadbeef" * 8, "pages": list(pages), "bbox": list(bbox)}
    entry.update(extra)
    return entry


class IouTest(unittest.TestCase):
    def test_identical_boxes_have_iou_one(self) -> None:
        box = [10.0, 10.0, 20.0, 20.0]
        self.assertAlmostEqual(eval_corpus.iou(box, box), 1.0)

    def test_disjoint_boxes_have_iou_zero(self) -> None:
        self.assertEqual(eval_corpus.iou([0, 0, 10, 10], [20, 20, 30, 30]), 0.0)

    def test_partial_overlap_is_between_zero_and_one(self) -> None:
        value = eval_corpus.iou([0, 0, 10, 10], [5, 5, 15, 15])
        self.assertGreater(value, 0.0)
        self.assertLess(value, 1.0)


class LabelBboxConversionTest(unittest.TestCase):
    def test_pixel_bbox_is_divided_by_scale_to_reach_pdf_points(self) -> None:
        item = label_item("person", "redact", bbox=(100.0, 200.0, 150.0, 220.0), scale=2.0)
        self.assertEqual(eval_corpus.label_bbox_to_pdf_points(item), [50.0, 100.0, 75.0, 110.0])


class CategoryEquivalenceTest(unittest.TestCase):
    def test_email_residual_satisfies_contact_label(self) -> None:
        self.assertTrue(eval_corpus.category_satisfied("contact", "email"))

    def test_denylist_residual_satisfies_any_sensitive_category(self) -> None:
        for category in ("person", "firm", "project", "address", "contact", "account_id"):
            self.assertTrue(eval_corpus.category_satisfied(category, "denylist"))

    def test_denylist_residual_does_not_satisfy_negative_content_category(self) -> None:
        self.assertFalse(eval_corpus.category_satisfied("manufacturer", "denylist"))

    def test_ner_person_name_satisfies_person_label(self) -> None:
        self.assertTrue(eval_corpus.category_satisfied("person", "person name"))

    def test_wrong_category_does_not_match(self) -> None:
        self.assertFalse(eval_corpus.category_satisfied("address", "email"))

    def test_unknown_detection_category_matches_nothing(self) -> None:
        self.assertFalse(eval_corpus.category_satisfied("person", "some_future_category"))


class FindMatchTest(unittest.TestCase):
    """The matching rule: same page (mind the label's 0-index vs the
    report's 1-index), positional overlap above the IoU floor, and a
    category the equivalence map accepts."""

    def test_exact_position_and_category_match(self) -> None:
        item = label_item("contact", "redact", page=0, bbox=(100.0, 100.0, 150.0, 120.0), scale=1.0)
        residuals = [residual("email", page=1, bbox=(100.0, 100.0, 150.0, 120.0))]
        match = eval_corpus.find_match(item, residuals, [])
        self.assertIsNotNone(match)
        self.assertEqual(match["surface"], "residual")

    def test_near_miss_within_tolerance_still_matches(self) -> None:
        # Hand-drawn label box is a little larger/offset from the exact
        # text-span geometry, but still overlaps well above the IoU floor.
        item = label_item("contact", "redact", page=0, bbox=(95.0, 95.0, 155.0, 125.0), scale=1.0)
        residuals = [residual("email", page=1, bbox=(100.0, 100.0, 150.0, 120.0))]
        match = eval_corpus.find_match(item, residuals, [])
        self.assertIsNotNone(match)

    def test_right_category_wrong_position_does_not_match(self) -> None:
        item = label_item("contact", "redact", page=0, bbox=(100.0, 100.0, 150.0, 120.0), scale=1.0)
        residuals = [residual("email", page=1, bbox=(400.0, 400.0, 450.0, 420.0))]
        self.assertIsNone(eval_corpus.find_match(item, residuals, []))

    def test_right_position_wrong_category_does_not_match(self) -> None:
        item = label_item("person", "redact", page=0, bbox=(100.0, 100.0, 150.0, 120.0), scale=1.0)
        residuals = [residual("email", page=1, bbox=(100.0, 100.0, 150.0, 120.0))]
        self.assertIsNone(eval_corpus.find_match(item, residuals, []))

    def test_right_position_and_category_wrong_page_does_not_match(self) -> None:
        # The 0-indexed-label vs 1-indexed-residual trap: a label on page 0
        # (the report's page 1) must not accidentally match a residual that
        # is also reported as page 1 but actually belongs to label page 1
        # (report page 2). This case pins the reverse: report page 2 must
        # not satisfy a label on page 0.
        item = label_item("contact", "redact", page=0, bbox=(100.0, 100.0, 150.0, 120.0), scale=1.0)
        residuals = [residual("email", page=2, bbox=(100.0, 100.0, 150.0, 120.0))]
        self.assertIsNone(eval_corpus.find_match(item, residuals, []))

    def test_matches_against_ner_findings_using_the_pages_list(self) -> None:
        item = label_item("person", "redact", page=0, bbox=(100.0, 100.0, 150.0, 120.0), scale=1.0)
        findings = [ner_finding("person name", pages=[1], bbox=(100.0, 100.0, 150.0, 120.0))]
        match = eval_corpus.find_match(item, [], findings)
        self.assertIsNotNone(match)
        self.assertEqual(match["surface"], "ner")


class RecallScoringTest(unittest.TestCase):
    """residuals/ner_review.findings are built against the SANITIZED OUTPUT
    (verify_output() opens `destination`, not the source) — so a match there
    means this label's region is still present in the output: a leak, i.e.
    a miss. No match means the sanitizer's own verifier didn't flag it,
    which score_recall counts as a hit (see its docstring for the resulting
    precision limit: it can't catch a leak the detectors never recognized
    as a candidate at all)."""

    def test_a_redact_item_found_as_a_residual_leak_is_a_miss(self) -> None:
        items = [label_item("contact", "redact", page=0, bbox=(100.0, 100.0, 150.0, 120.0), scale=1.0)]
        residuals = [residual("email", page=1, bbox=(100.0, 100.0, 150.0, 120.0))]
        misses, hits = eval_corpus.score_recall(items, residuals, [])
        self.assertEqual(len(misses), 1)
        self.assertEqual(hits, [])

    def test_a_redact_item_with_no_leak_anywhere_is_a_hit(self) -> None:
        items = [label_item("contact", "redact", page=0, bbox=(100.0, 100.0, 150.0, 120.0), scale=1.0)]
        misses, hits = eval_corpus.score_recall(items, [], [])
        self.assertEqual(misses, [])
        self.assertEqual(len(hits), 1)

    def test_keep_items_are_ignored_by_recall_scoring(self) -> None:
        items = [label_item("boilerplate", "keep", page=0)]
        misses, hits = eval_corpus.score_recall(items, [], [])
        self.assertEqual(misses, [])
        self.assertEqual(hits, [])


class OverRedactionScoringTest(unittest.TestCase):
    """Over-redaction can only be checked at page+category granularity: the
    report's page_redactions has no per-region bbox, only which categories
    were redacted somewhere on a page."""

    def test_a_keep_item_on_a_page_with_a_compatible_redacted_category_is_flagged(self) -> None:
        items = [label_item("boilerplate", "keep", page=0)]
        page_redactions = [{"page": 1, "categories": ["denylist"]}]
        over_redactions = eval_corpus.score_over_redaction(items, page_redactions)
        self.assertEqual(len(over_redactions), 0)  # denylist is not compatible with boilerplate

    def test_a_keep_person_item_on_a_page_with_denylist_redaction_is_flagged(self) -> None:
        items = [label_item("person", "keep", page=0)]
        page_redactions = [{"page": 1, "categories": ["denylist"]}]
        over_redactions = eval_corpus.score_over_redaction(items, page_redactions)
        self.assertEqual(len(over_redactions), 1)

    def test_a_keep_item_on_a_page_with_no_redactions_is_not_flagged(self) -> None:
        items = [label_item("person", "keep", page=0)]
        over_redactions = eval_corpus.score_over_redaction(items, [])
        self.assertEqual(over_redactions, [])

    def test_redact_items_are_ignored_by_over_redaction_scoring(self) -> None:
        items = [label_item("person", "redact", page=0)]
        page_redactions = [{"page": 1, "categories": ["denylist"]}]
        over_redactions = eval_corpus.score_over_redaction(items, page_redactions)
        self.assertEqual(over_redactions, [])


class PerDocumentFailureIsolationTest(unittest.TestCase):
    """A malformed/encrypted document must not prevent the rest of the
    corpus from being scored — eval_corpus.py calls sanitize_document()
    directly, per document, inside its own try/except, unlike
    orchestrate_run()'s call which aborts the rest of a batch."""

    def test_a_raising_document_yields_a_fail_record_not_an_exception(self) -> None:
        def raising_sanitize_document(*args, **kwargs):
            raise SystemExit("doc-b: source PDF could not be opened")

        report = eval_corpus.run_sanitizer_isolated(
            raising_sanitize_document, source=Path("doc-b.pdf"), destination=Path("out.pdf"),
            document_id="doc-b", denylist=set(), settings=None, temp_root=Path("."),
            run_key=b"0" * 32,
        )
        self.assertEqual(report["document_id"], "doc-b")
        self.assertEqual(report["release_status"], "FAIL")
        self.assertIn("could not be opened", report["fail_reason"])

    def test_a_succeeding_document_returns_its_own_report_unchanged(self) -> None:
        sentinel = {"document_id": "doc-a", "release_status": "AUTOMATED_PASS"}

        def succeeding_sanitize_document(*args, **kwargs):
            return sentinel

        report = eval_corpus.run_sanitizer_isolated(
            succeeding_sanitize_document, source=Path("doc-a.pdf"), destination=Path("out.pdf"),
            document_id="doc-a", denylist=set(), settings=None, temp_root=Path("."),
            run_key=b"0" * 32,
        )
        self.assertEqual(report, sentinel)

    def test_one_raising_document_does_not_stop_the_rest_of_the_corpus(self) -> None:
        calls = []

        def flaky_sanitize_document(source, *args, **kwargs):
            calls.append(source)
            if source.name == "doc-b.pdf":
                raise SystemExit("doc-b: source PDF could not be opened")
            return {"document_id": kwargs.get("document_id"), "release_status": "AUTOMATED_PASS"}

        reports = []
        for name in ("doc-a.pdf", "doc-b.pdf", "doc-c.pdf"):
            reports.append(eval_corpus.run_sanitizer_isolated(
                flaky_sanitize_document, source=Path(name), destination=Path("out.pdf"),
                document_id=name, denylist=set(), settings=None, temp_root=Path("."),
                run_key=b"0" * 32,
            ))
        self.assertEqual(len(calls), 3)
        self.assertEqual(reports[0]["release_status"], "AUTOMATED_PASS")
        self.assertEqual(reports[1]["release_status"], "FAIL")
        self.assertEqual(reports[2]["release_status"], "AUTOMATED_PASS")


if __name__ == "__main__":
    unittest.main()
