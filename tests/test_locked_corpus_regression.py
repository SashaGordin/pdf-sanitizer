"""The locked-corpus release gate, enforced. Mirrors
tests/test_anonymize_construction_pdfs.py's GoldenSetTest shape exactly:
one cached harness run in setUpClass, thin assertion methods per failure
category.

Scored only against documents that currently have a label file (see
tools/eval_corpus.py's unlabeled_documents branch and
.scratch/corpus/labels/README.md) — most of the real corpus is still
unlabeled, left for the user to label with tools/corpus_labeler.py. This
test cannot and does not claim "zero false negatives on the whole locked
corpus" — only on the labeled subset. See tools/eval_corpus.py's
score_recall docstring for a further, real precision limit even within
that labeled subset: a hit means "not flagged as a leftover by the
sanitizer's own verifier," not an independently-confirmed redaction.

Runs the real sanitizer end-to-end (OCR included for scanned pages), so
this is slow relative to the rest of the suite — same trade-off
GoldenSetTest already makes for policy-vocabulary recall, just against
real documents instead of a string list.
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


class LockedCorpusRegressionTest(unittest.TestCase):
    """The release gate for the labeled slice of the locked corpus."""

    @classmethod
    def setUpClass(cls) -> None:
        root = Path(__file__).parents[1]
        scratch = root / "tmp" / "eval_corpus_test"
        scratch.mkdir(parents=True, exist_ok=True)
        cls.result = eval_corpus.evaluate_corpus(
            root / ".scratch/corpus/manifest.json",
            root / ".scratch/corpus/labels",
            root / ".scratch/corpus/intake_seeds",
            root / "config/denylist.local.json",
            root / "config/lexicons",
            root / "config/allowlist.shared.json",
            scratch,
        )

    def test_at_least_one_document_is_labeled(self) -> None:
        # A guard against this whole test class silently, vacuously passing
        # if .scratch/corpus/labels/ is ever emptied by accident — every
        # assertion below is trivially true with zero labeled documents.
        self.assertGreaterEqual(len(self.result["per_document"]), 1)

    def test_no_known_leaks_on_the_labeled_must_redact_set(self) -> None:
        misses = self.result["recall_misses"]
        self.assertEqual(misses, [], f"document-level recall miss(es): {misses}")

    def test_no_unexplained_over_redaction_on_the_labeled_must_survive_set(self) -> None:
        over_redactions = self.result["over_redactions"]
        self.assertEqual(over_redactions, [], f"unexplained over-redaction(s): {over_redactions}")

    def test_metric_is_labeled_document_level_recall(self) -> None:
        self.assertEqual(self.result["metric_label"], eval_corpus.DOCUMENT_LEVEL_RECALL_LABEL)


class SyntheticDocumentDispositionTest(unittest.TestCase):
    """The two document-level-only synthetic corpus members (no per-item
    labels — see tests/fixtures/corpus/synthetic/README.md): their expected
    outcome is a document-level release_status, checked directly here
    rather than through eval_corpus.py's item-level scoring."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).parents[1]
        module_path = cls.root / "tools" / "anonymize_construction_pdfs.py"
        spec = importlib.util.spec_from_file_location("pdf_sanitizer_lcr", module_path)
        cls.sanitizer = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = cls.sanitizer
        spec.loader.exec_module(cls.sanitizer)

        builder_path = cls.root / "tools" / "build_synthetic_corpus_documents.py"
        builder_spec = importlib.util.spec_from_file_location("build_synthetic_corpus_documents_lcr", builder_path)
        cls.builder = importlib.util.module_from_spec(builder_spec)
        sys.modules[builder_spec.name] = cls.builder
        builder_spec.loader.exec_module(cls.builder)

    def test_encrypted_synthetic_document_fails_closed(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory(prefix="lcr_encrypted_") as tmp:
            root = Path(tmp)
            source = root / "encrypted_spec.pdf"
            self.builder.build_encrypted_document(source)
            report = self.sanitizer.sanitize_document(
                source, root / "out.pdf", "doc", set(), self.sanitizer.Settings(), root, b"0" * 32,
            )
            self.assertEqual(report["release_status"], self.sanitizer.RELEASE_STATUS_FAIL)

    def test_malformed_synthetic_document_raises(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory(prefix="lcr_malformed_") as tmp:
            root = Path(tmp)
            source = root / "malformed_spec.pdf"
            self.builder.build_malformed_document(source)
            with self.assertRaises(SystemExit):
                self.sanitizer.sanitize_document(
                    source, root / "out.pdf", "doc", set(), self.sanitizer.Settings(), root, b"0" * 32,
                )


if __name__ == "__main__":
    unittest.main()
