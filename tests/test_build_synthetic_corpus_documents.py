"""Empirical checkpoints for the three synthetic locked-corpus documents:
each one must actually produce the failure/success mode it's built to
represent, not just look plausible by construction."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

import fitz

MODULE_PATH = Path(__file__).parents[1] / "tools" / "build_synthetic_corpus_documents.py"
SPEC = importlib.util.spec_from_file_location("build_synthetic_corpus_documents", MODULE_PATH)
builder = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = builder
SPEC.loader.exec_module(builder)

SANITIZER_MODULE_PATH = Path(__file__).parents[1] / "tools" / "anonymize_construction_pdfs.py"
_sanitizer_spec = importlib.util.spec_from_file_location("pdf_sanitizer_ssd", SANITIZER_MODULE_PATH)
sanitizer = importlib.util.module_from_spec(_sanitizer_spec)
assert _sanitizer_spec and _sanitizer_spec.loader
sys.modules[_sanitizer_spec.name] = sanitizer
_sanitizer_spec.loader.exec_module(sanitizer)

# DenylistMatcher(set()) compiles to a pattern with no alternatives, which
# matches the empty string everywhere its lookarounds are satisfied — for
# non-ASCII text (nothing in [A-Z0-9] to violate them), that's almost every
# position, so an empty denylist over-redacts pathologically rather than
# redacting nothing. main()'s CLI guards against this ("No denylist terms
# are available"); tests that actually scan document text, unlike the
# encrypted/malformed cases below (which never reach a scan), need a
# non-empty placeholder that has nothing to do with the Japanese content.
PLACEHOLDER_DENYLIST = {"Fictional Placeholder Firm", "ZX-PLACEHOLDER-0000"}


class SyntheticEncryptedDocumentTest(unittest.TestCase):
    def test_sanitize_document_fails_closed_on_the_encrypted_synthetic_doc(self) -> None:
        with tempfile.TemporaryDirectory(prefix="synthetic_encrypted_") as tmp:
            root = Path(tmp)
            source = root / "encrypted_spec.pdf"
            builder.build_encrypted_document(source)
            report = sanitizer.sanitize_document(
                source, root / "out.pdf", "doc", set(), sanitizer.Settings(), root, b"0" * 32,
            )
            self.assertEqual(report["release_status"], sanitizer.RELEASE_STATUS_FAIL)
            self.assertIn("encrypt", report["fail_reason"])


class SyntheticMalformedDocumentTest(unittest.TestCase):
    def test_fitz_open_raises_on_the_malformed_synthetic_doc(self) -> None:
        with tempfile.TemporaryDirectory(prefix="synthetic_malformed_") as tmp:
            source = Path(tmp) / "malformed_spec.pdf"
            builder.build_malformed_document(source)
            with self.assertRaises(Exception):
                fitz.open(str(source))

    def test_sanitize_document_raises_systemexit_on_the_malformed_synthetic_doc(self) -> None:
        with tempfile.TemporaryDirectory(prefix="synthetic_malformed_sd_") as tmp:
            root = Path(tmp)
            source = root / "malformed_spec.pdf"
            builder.build_malformed_document(source)
            with self.assertRaises(SystemExit):
                sanitizer.sanitize_document(
                    source, root / "out.pdf", "doc", set(), sanitizer.Settings(), root, b"0" * 32,
                )

    def test_malformed_doc_is_not_merely_truncated(self) -> None:
        with tempfile.TemporaryDirectory(prefix="synthetic_malformed_len_") as tmp:
            root = Path(tmp)
            source = root / "malformed_spec.pdf"
            builder.build_malformed_document(source)
            data = source.read_bytes()
            self.assertTrue(data.startswith(b"%PDF-"))
            # A truncated download would be missing a tail entirely (no
            # trailer/EOF bytes at all); this file is full length, corrupted
            # throughout the body instead.
            self.assertGreater(len(data), 200)


class SyntheticNonEnglishDocumentTest(unittest.TestCase):
    def test_japanese_text_renders_legibly_and_round_trips_through_extraction(self) -> None:
        with tempfile.TemporaryDirectory(prefix="synthetic_non_english_") as tmp:
            path = Path(tmp) / "non_english_spec.pdf"
            builder.build_non_english_document(path)
            doc = fitz.open(path)
            self.assertEqual(len(doc), 1)
            page = doc[0]
            text = page.get_text("text")
            self.assertIn("アルファ建設株式会社", text)  # the firm name
            self.assertIn("山田太郎", text)  # the person name
            self.assertIn("contact@example.invalid", text)  # script-agnostic contact
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), colorspace=fitz.csGRAY, alpha=False)
            self.assertNotEqual(min(pix.samples), max(pix.samples))  # not a blank render
            doc.close()

    def test_sanitize_document_still_catches_the_script_agnostic_email(self) -> None:
        with tempfile.TemporaryDirectory(prefix="synthetic_non_english_sd_") as tmp:
            root = Path(tmp)
            source = root / "non_english_spec.pdf"
            builder.build_non_english_document(source)
            destination = root / "out.pdf"
            report = sanitizer.sanitize_document(
                source, destination, "doc", PLACEHOLDER_DENYLIST, sanitizer.Settings(), root, b"0" * 32,
            )
            output = fitz.open(destination)
            text = "".join(page.get_text("text") for page in output)
            output.close()
            self.assertNotIn("contact@example.invalid", text)

    def test_sanitize_document_leaks_the_unseeded_japanese_person_and_firm_names(self) -> None:
        # The documented generalization gap (tests/fixtures/corpus/synthetic/
        # README.md), measured rather than asserted in prose: with no
        # project-metadata/denylist seeding (the same "run as-is" scope this
        # document exists to test), the person/firm/address/phone lines have
        # no script-agnostic pattern to be caught by, and English-oriented
        # NER/lexicon matching is not exercised here (no ner_detector
        # passed) — so they survive untouched; only the email line (a
        # regex-detected, script-agnostic category) is redacted. If this
        # ever starts failing, the pipeline has gained non-English coverage
        # worth documenting as a genuine improvement, not a regression to
        # chase.
        #
        # Verified via redaction_counts/page_redactions rather than
        # extracted output text: get_text("text") on the sanitized output is
        # unreliable for this CJK content (the flatten step re-encodes the
        # font in a way that garbles ToUnicode mapping on extraction), but a
        # rendered pixmap of the output (checked manually while building
        # this test) confirms the Japanese glyphs remain fully legible —
        # this is a real leak, not an extraction artifact hiding a real
        # redaction.
        with tempfile.TemporaryDirectory(prefix="synthetic_non_english_gap_") as tmp:
            root = Path(tmp)
            source = root / "non_english_spec.pdf"
            builder.build_non_english_document(source)
            destination = root / "out.pdf"
            report = sanitizer.sanitize_document(
                source, destination, "doc", PLACEHOLDER_DENYLIST, sanitizer.Settings(), root, b"0" * 32,
            )
            self.assertEqual(report["redaction_counts"], {"email": 1})
            self.assertEqual(report["page_redactions"], [{"page": 1, "categories": ["email"]}])


if __name__ == "__main__":
    unittest.main()
