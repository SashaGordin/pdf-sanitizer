from __future__ import annotations

import datetime as dt
import hashlib
import importlib.util
import json
import os
import re
import signal
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import subprocess
import threading
import urllib.error
import urllib.parse
import urllib.request

import fitz
from PIL import Image
from reportlab.graphics import renderPDF
from reportlab.graphics.barcode import qr
from reportlab.graphics.shapes import Drawing
from reportlab.lib.pagesizes import letter
from reportlab.lib.pdfencrypt import StandardEncryption
from reportlab.pdfgen import canvas


MODULE_PATH = Path(__file__).parents[1] / "tools" / "anonymize_construction_pdfs.py"
SPEC = importlib.util.spec_from_file_location("pdf_sanitizer", MODULE_PATH)
sanitizer = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = sanitizer
SPEC.loader.exec_module(sanitizer)

VERIFY_MODULE_PATH = Path(__file__).parents[1] / "tools" / "verify_output_text.py"
VERIFY_SPEC = importlib.util.spec_from_file_location("verify_existing", VERIFY_MODULE_PATH)
verify_existing = importlib.util.module_from_spec(VERIFY_SPEC)
assert VERIFY_SPEC and VERIFY_SPEC.loader
sys.modules[VERIFY_SPEC.name] = verify_existing
VERIFY_SPEC.loader.exec_module(verify_existing)

REVIEWER_MODULE_PATH = Path(__file__).parents[1] / "tools" / "reviewer_triage.py"
REVIEWER_SPEC = importlib.util.spec_from_file_location("reviewer_triage", REVIEWER_MODULE_PATH)
reviewer_triage = importlib.util.module_from_spec(REVIEWER_SPEC)
assert REVIEWER_SPEC and REVIEWER_SPEC.loader
sys.modules[REVIEWER_SPEC.name] = reviewer_triage
REVIEWER_SPEC.loader.exec_module(reviewer_triage)


FAKE_TERMS = {
    "Fictional Owner Holdings",
    "Example Confidential Project",
    "Imaginary Architecture Studio",
    "Fabricated Engineering Group",
    "ZX-FAKE-2048",
}


def add_qr(pdf: canvas.Canvas, value: str, x: float, y: float, size: float = 72) -> None:
    widget = qr.QrCodeWidget(value)
    x0, y0, x1, y1 = widget.getBounds()
    drawing = Drawing(size, size, transform=[size / (x1 - x0), 0, 0, size / (y1 - y0), 0, 0])
    drawing.add(widget)
    renderPDF.draw(drawing, pdf, x, y)


def create_searchable_pdf(path: Path) -> None:
    pdf = canvas.Canvas(str(path), pagesize=letter)
    pdf.setTitle("Synthetic confidential title")
    pdf.setAuthor("Synthetic Person")
    pdf.setFont("Helvetica", 12)
    lines = [
        "OWNER: Fictional Owner Holdings",
        "PROJECT: Example Confidential Project",
        "ARCHITECT: Imaginary Architecture Studio",
        "ENGINEER: Fabricated Engineering Group",
        "PROJECT NUMBER: ZX-FAKE-2048",
        "contact@example.invalid  (415) 555-0199  https://example.invalid",
        "1234 Example Avenue, Sampletown CA 99999",
        "TECHNICAL: ASTM A36 structural steel; 2x6 studs at 16 inches O.C.",
    ]
    y = 730
    for line in lines:
        pdf.drawString(45, y, line)
        y -= 24
    add_qr(pdf, "https://fabricated.invalid/project/ZX-FAKE-2048", 450, 620, 90)
    pdf.saveState()
    pdf.translate(580, 150)
    pdf.rotate(90)
    pdf.drawString(0, 0, "CHECKED BY: FAKE INITIALS")
    pdf.restoreState()
    pdf.drawString(45, 420, "hidden.person@example.invalid")
    pdf.setFillColorRGB(1, 1, 1)
    pdf.rect(40, 415, 280, 25, fill=1, stroke=0)
    pdf.setFillColorRGB(0, 0, 0)
    pdf.showPage()

    pdf.setFont("Helvetica", 12)
    pdf.drawString(45, 730, "REVISION BLOCK")
    pdf.drawString(45, 705, "APPROVED BY: Synthetic Approver")
    pdf.drawString(45, 680, "Ordinary technical text remains searchable")
    pdf.showPage()
    pdf.save()

    doc = fitz.open(path)
    doc.set_metadata({"title": "Synthetic hidden title", "author": "Synthetic Person", "keywords": "fictional owner"})
    doc.set_toc([[1, "Synthetic confidential bookmark", 1]])
    page = doc[0]
    page.add_text_annot((300, 300), "Synthetic annotation author and note")
    page.insert_link({"kind": fitz.LINK_URI, "from": fitz.Rect(45, 235, 250, 255), "uri": "https://example.invalid"})
    widget = fitz.Widget()
    widget.field_name = "synthetic_secret_field"
    widget.field_type = fitz.PDF_WIDGET_TYPE_TEXT
    widget.field_value = "Synthetic form value"
    widget.rect = fitz.Rect(300, 320, 500, 345)
    page.add_widget(widget)
    page.insert_text((45, 470), "invisible.person@example.invalid", render_mode=3)
    doc.embfile_add("neutral_attachment.bin", b"synthetic attachment")
    doc.save(path.with_suffix(".augmented.pdf"), garbage=4, deflate=True)
    doc.close()
    path.unlink()
    path.with_suffix(".augmented.pdf").rename(path)


def create_scanned_pdf(path: Path) -> None:
    vector = path.with_suffix(".vector.pdf")
    pdf = canvas.Canvas(str(vector), pagesize=letter)
    pdf.setFont("Helvetica", 20)
    pdf.drawString(45, 700, "OWNER: Fictional Owner Holdings")
    pdf.drawString(45, 660, "scan.person@example.invalid")
    pdf.drawString(45, 620, "9876 Fabricated Road")
    pdf.drawString(45, 560, "TECHNICAL NOTE: 5/8 inch Type X gypsum board")
    pdf.save()
    doc = fitz.open(vector)
    pix = doc[0].get_pixmap(matrix=fitz.Matrix(200 / 72, 200 / 72), colorspace=fitz.csRGB)
    image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    doc.close()
    vector.unlink()
    buffer = tempfile.SpooledTemporaryFile()
    image.save(buffer, format="PNG", dpi=(200, 200))
    buffer.seek(0)
    scan = fitz.open()
    page = scan.new_page(width=letter[0], height=letter[1])
    page.insert_image(page.rect, stream=buffer.read())
    buffer.close()
    scan.save(path)
    scan.close()


def create_scanned_pdf_pages(path: Path, pages: list[list[str]]) -> None:
    """Multi-page scanned/rasterized PDF: each page is one full-page image
    baking in that page's lines. Generalizes create_scanned_pdf to accept
    per-page line lists (needed by parity and per-page-containment tests)."""
    scan = fitz.open()
    for page_index, lines in enumerate(pages):
        vector = path.with_suffix(f".vector{page_index}.pdf")
        pdf = canvas.Canvas(str(vector), pagesize=letter)
        pdf.setFont("Helvetica", 20)
        y = 700
        for line in lines:
            pdf.drawString(45, y, line)
            # Wide enough that a redaction box's own quiet-zone padding
            # (apply_image_boxes pads ~18% of a box's own width/height in
            # every direction) can never bleed vertically into the next
            # line, even when this line is a long denylist/address match.
            y -= 150
        pdf.save()
        doc = fitz.open(vector)
        pix = doc[0].get_pixmap(matrix=fitz.Matrix(200 / 72, 200 / 72), colorspace=fitz.csRGB)
        image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        doc.close()
        vector.unlink()
        buffer = tempfile.SpooledTemporaryFile()
        image.save(buffer, format="PNG", dpi=(200, 200))
        buffer.seek(0)
        page = scan.new_page(width=letter[0], height=letter[1])
        page.insert_image(page.rect, stream=buffer.read())
        buffer.close()
    scan.save(path)
    scan.close()


def render_stamp_image(
    path: Path, lines: list[str], canvas_size: tuple[float, float] = (300, 260),
    font_size: int = 22, dpi: int = 200,
) -> bytes:
    """PNG bytes baking in `lines` onto a small canvas, via the same
    vector-canvas -> fitz pixmap -> PIL pipeline create_scanned_pdf uses,
    generalized so a caller can place the result as one embedded image on an
    otherwise-vector page. `path` is scratch space only."""
    vector = path.with_suffix(".stamp.pdf")
    pdf = canvas.Canvas(str(vector), pagesize=canvas_size)
    pdf.setFont("Helvetica", font_size)
    y = canvas_size[1] - font_size * 1.8
    for line in lines:
        pdf.drawString(10, y, line)
        y -= font_size * 1.8
    pdf.save()
    doc = fitz.open(vector)
    scale = dpi / 72
    pix = doc[0].get_pixmap(matrix=fitz.Matrix(scale, scale), colorspace=fitz.csRGB)
    image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    doc.close()
    vector.unlink()
    buffer = tempfile.SpooledTemporaryFile()
    image.save(buffer, format="PNG", dpi=(dpi, dpi))
    buffer.seek(0)
    data = buffer.read()
    buffer.close()
    return data


def create_cross_line_pdf(path: Path) -> None:
    """Shapes mirroring the audit's masked real-run residuals: label/value,
    label-alone/value, name/credential, and wrapped phrases split across
    consecutive lines. All values are synthetic."""
    pdf = canvas.Canvas(str(path), pagesize=letter)
    pdf.setFont("Helvetica", 12)
    lines = [
        (730, "PROJECT NO.:"),
        (714, "ZX-SPLIT-7788"),
        (670, "OWNER:"),
        (654, "Unlisted Example Firm LLC"),
        (610, "Reviewed by Fakename Persona,"),
        (594, "PE"),
        (550, "Site located at 4321 Fabricated Industrial"),
        (534, "Parkway"),
        (490, "Prepared for Fictional Owner"),
        (474, "Holdings"),
        (414, "TECHNICAL: concrete strength 4000 psi with ASTM A615 rebar"),
    ]
    for y, text in lines:
        pdf.drawString(45, y, text)
    pdf.showPage()
    pdf.save()


def create_rotated_text_pdf(path: Path) -> None:
    """Portrait page whose text is entirely rotated 90 degrees — the shape
    that makes Ghostscript's AutoRotatePages re-orient the page. Content is
    purely technical so nothing is redacted."""
    pdf = canvas.Canvas(str(path), pagesize=letter)
    pdf.setFont("Helvetica", 14)
    pdf.translate(306, 396)
    pdf.rotate(90)
    for index, text in enumerate([
        "ROTATED SCHEDULE OF INTERIOR FINISHES",
        "GYPSUM BOARD ON METAL STUDS THROUGHOUT",
        "CONTINUOUS VAPOR RETARDER MEMBRANE",
        "MECHANICAL FASTENERS AT PANEL PERIMETER",
    ]):
        pdf.drawString(-250, -60 + index * 30, text)
    pdf.showPage()
    pdf.save()


def create_encrypted_pdf(path: Path) -> None:
    pdf = canvas.Canvas(
        str(path), pagesize=letter,
        encrypt=StandardEncryption("userpw", ownerPassword="ownerpw", canPrint=1, canModify=0),
    )
    pdf.setFont("Helvetica", 12)
    pdf.drawString(45, 700, "Fictional Owner Holdings")
    pdf.showPage()
    pdf.save()


class SanitizerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="sanitizer_test_")
        self.root = Path(self.temp.name)
        self.settings = sanitizer.Settings(
            ocr_dpi=220,
            barcode_dpi=72,
            min_vector_text_chars=20,
            progress_every_pages=0,
            detect_barcodes=True,
            redact_repeated_margin_images=False,
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_sanitizer(self, source: Path, settings=None, run_key: bytes | None = None):
        destination = self.root / "sanitized_document_01.pdf"
        report = sanitizer.sanitize_document(
            source, destination, "sanitized_document_01", FAKE_TERMS,
            settings or self.settings, self.root, run_key or os.urandom(32),
        )
        return destination, report

    def test_searchable_rotated_hidden_and_interactive_content(self) -> None:
        source = self.root / "source.pdf"
        create_searchable_pdf(source)
        destination, report = self.run_sanitizer(source)
        self.assertEqual(report["release_status"], sanitizer.RELEASE_STATUS_AUTOMATED_PASS)
        self.assertTrue(report["checks"]["no_render_remediation_required"])
        self.assertIn("barcode_or_qr", {c for item in report["page_redactions"] for c in item["categories"]})
        output = fitz.open(destination)
        extracted = "\n".join(page.get_text("text") for page in output)
        folded = extracted.casefold()
        for term in FAKE_TERMS:
            self.assertNotIn(term.casefold(), folded)
        self.assertNotIn("@", extracted)
        self.assertIn("ASTM A36", extracted)
        self.assertIn("Ordinary technical text remains searchable", extracted)
        self.assertFalse(output.metadata.get("title"))
        self.assertFalse(output.get_toc())
        self.assertFalse(output.embfile_names())
        self.assertFalse(output.get_ocgs())
        self.assertFalse(any(page.first_annot or page.first_widget or page.get_links() for page in output))
        output.close()
        raw = destination.read_bytes().lower()
        self.assertNotIn(b"hidden.person", raw)
        self.assertNotIn(b"fictional owner holdings", raw)

    def test_image_only_page_is_redacted_then_rebuilt_with_sanitized_ocr(self) -> None:
        source = self.root / "scan.pdf"
        create_scanned_pdf(source)
        destination, report = self.run_sanitizer(source)
        self.assertEqual(report["release_status"], sanitizer.RELEASE_STATUS_AUTOMATED_PASS)
        self.assertEqual(report["rasterized_pages"], [1])
        output = fitz.open(destination)
        text = output[0].get_text("text")
        self.assertNotIn("@", text)
        self.assertNotIn("Fictional Owner", text)
        self.assertIn("TECHNICAL", text)
        self.assertIn("gypsum", text)
        output.close()

    def test_configurable_title_and_approval_rectangles(self) -> None:
        source = self.root / "regions.pdf"
        pdf = canvas.Canvas(str(source), pagesize=letter)
        pdf.drawString(50, 700, "Technical schedule: concrete strength 4000 psi")
        pdf.drawString(450, 100, "UNLABELLED APPROVAL MARK")
        pdf.save()
        settings = sanitizer.Settings(
            ocr_dpi=220, barcode_dpi=120, progress_every_pages=0,
            detect_barcodes=True, redact_repeated_margin_images=False,
            regions=[sanitizer.Region("approval", "approval_area", (0.70, 0.80, 1.0, 1.0), "all")],
        )
        destination, report = self.run_sanitizer(source, settings)
        output = fitz.open(destination)
        text = output[0].get_text("text")
        self.assertNotIn("UNLABELLED APPROVAL MARK", text)
        self.assertIn("concrete strength 4000 psi", text)
        self.assertEqual(report["release_status"], sanitizer.RELEASE_STATUS_AUTOMATED_PASS)
        output.close()

    def test_fails_closed_with_page_number_when_ocr_is_unavailable(self) -> None:
        source = self.root / "scan.pdf"
        create_scanned_pdf(source)
        settings = sanitizer.Settings(
            ocr_dpi=220, barcode_dpi=120, progress_every_pages=0,
            tesseract_executable="definitely-not-a-real-ocr-command",
            detect_barcodes=True, redact_repeated_margin_images=False,
        )
        with self.assertRaises(sanitizer.PageProcessingError) as caught:
            self.run_sanitizer(source, settings)
        self.assertEqual(caught.exception.page_number, 1)
        self.assertNotIn("Fictional", str(caught.exception))

    def test_encrypted_source_fails_closed_without_traceback_or_decryption(self) -> None:
        source = self.root / "encrypted.pdf"
        create_encrypted_pdf(source)
        destination, report = self.run_sanitizer(source)
        self.assertEqual(report["release_status"], sanitizer.RELEASE_STATUS_FAIL)
        self.assertIn("encrypt", report["fail_reason"].lower())
        self.assertFalse(destination.exists())
        # No password argument or decryption call exists anywhere in the tool.
        source_text = Path(sanitizer.__file__).read_text(encoding="utf-8")
        self.assertNotIn("--password", source_text)
        self.assertNotIn(".authenticate(", source_text)

    def test_render_remediation_forces_fail_even_when_second_pass_is_clean(self) -> None:
        # A value that only ever surfaced in final-output rendered OCR must
        # never be allowed to reach AUTOMATED_PASS silently just because an
        # automatic patch-and-reverify happened to leave the page clean.
        source = self.root / "source.pdf"
        create_searchable_pdf(source)
        with mock.patch.object(
            sanitizer, "remediate_rendered_output", return_value={1: ["denylist"]},
        ):
            destination, report = self.run_sanitizer(source)
        self.assertFalse(report["checks"]["no_render_remediation_required"])
        self.assertEqual(report["release_status"], sanitizer.RELEASE_STATUS_FAIL)
        self.assertEqual(
            report["rendered_output_remediation"], [{"page": 1, "categories": ["denylist"]}],
        )

    def test_tesseract_timeout_fails_closed_per_page(self) -> None:
        source = self.root / "scan.pdf"
        create_scanned_pdf(source)
        real_run = subprocess.run

        def timing_out(cmd, *args, **kwargs):
            if Path(cmd[0]).name == "tesseract" or cmd[0] == self.settings.tesseract_executable:
                raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout"))
            return real_run(cmd, *args, **kwargs)

        with mock.patch.object(subprocess, "run", side_effect=timing_out):
            with self.assertRaises(sanitizer.PageProcessingError) as caught:
                self.run_sanitizer(source)
        self.assertEqual(caught.exception.page_number, 1)
        self.assertNotIn("Fictional", str(caught.exception))

    def test_ghostscript_timeout_fails_closed_without_hanging(self) -> None:
        source = self.root / "source.pdf"
        create_searchable_pdf(source)
        real_run = subprocess.run

        def timing_out(cmd, *args, **kwargs):
            if Path(cmd[0]).name in {"gs", "ghostscript"}:
                raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout"))
            return real_run(cmd, *args, **kwargs)

        with mock.patch.object(subprocess, "run", side_effect=timing_out):
            with self.assertRaises(sanitizer.PageProcessingError) as caught:
                self.run_sanitizer(source)
        self.assertIn("ghostscript", caught.exception.reason.lower())
        self.assertIn("timeout", caught.exception.reason.lower())

    def test_derive_release_status_state_machine(self) -> None:
        AP, RR, RI, F, R = (
            sanitizer.RELEASE_STATUS_AUTOMATED_PASS, sanitizer.RELEASE_STATUS_REVIEW_REQUIRED,
            sanitizer.RELEASE_STATUS_REVIEW_INCOMPLETE, sanitizer.RELEASE_STATUS_FAIL,
            sanitizer.RELEASE_STATUS_RELEASED,
        )
        self.assertEqual(sanitizer.derive_release_status(F), F)
        self.assertEqual(sanitizer.derive_release_status(F, {"status": "complete"}), F)
        self.assertEqual(sanitizer.derive_release_status(AP), RR)
        self.assertEqual(sanitizer.derive_release_status(AP, {}), RR)
        self.assertEqual(sanitizer.derive_release_status(AP, {"status": "incomplete"}), RI)
        self.assertEqual(sanitizer.derive_release_status(AP, {"status": "complete"}), R)
        # A truncated residual/NER review list (or an unresolved intake gate)
        # is the same kind of incompleteness signal as an unfinished review:
        # it blocks RELEASED even once a human has signed off as "complete".
        self.assertEqual(
            sanitizer.derive_release_status(AP, {"status": "complete"}, incompleteness_reasons=["x"]), RI,
        )
        # Irrelevant when the run would not have reached RELEASED anyway.
        self.assertEqual(
            sanitizer.derive_release_status(AP, incompleteness_reasons=["x"]), RR,
        )
        self.assertEqual(
            sanitizer.derive_release_status(F, {"status": "complete"}, incompleteness_reasons=["x"]), F,
        )

    def test_denylist_matches_terms_wrapped_across_lines(self) -> None:
        matcher = sanitizer.DenylistMatcher({"Fictional Owner Holdings"})
        self.assertEqual(matcher.count("Prepared for Fictional Owner\nHoldings today"), 1)
        self.assertEqual(matcher.count("Fictional Owner Holdings"), 1)
        self.assertEqual(matcher.count("Fictional OwnerHoldings"), 0)

    def test_cross_line_identifiers_are_detected_and_redacted(self) -> None:
        source = self.root / "crossline.pdf"
        create_cross_line_pdf(source)
        destination, report = self.run_sanitizer(source)
        self.assertEqual(report["release_status"], sanitizer.RELEASE_STATUS_AUTOMATED_PASS)
        output = fitz.open(destination)
        folded = "\n".join(page.get_text("text") for page in output).casefold()
        output.close()
        for leaked in (
            "zx-split-7788",          # value under a "PROJECT NO.:" label line
            "unlisted example firm",  # value under a label-alone "OWNER:" line
            "fakename",               # name with PE credential on the next line
            "persona",
            "4321 fabricated",        # street address wrapped across two lines
            "parkway",
            "fictional owner",        # denylist term wrapped across two lines
            "holdings",
        ):
            self.assertNotIn(leaked, folded)
        self.assertIn("4000 psi", folded)
        self.assertIn("astm a615", folded)

    def test_masked_shape_hides_letters_and_digits(self) -> None:
        self.assertEqual(sanitizer.masked_shape("Acme Corp 42, LLC"), "Aaaa Aaaa 99, AAA")
        self.assertEqual(sanitizer.masked_shape("PROJECT NO.:\n12345"), "AAAAAAA AA.:\n99999")
        long_shape = sanitizer.masked_shape("x" * 300)
        self.assertLessEqual(len(long_shape), 81)
        self.assertTrue(long_shape.endswith("…"))

    def test_verifier_lists_masked_residuals_with_local_crops(self) -> None:
        source = self.root / "leaky.pdf"
        pdf = canvas.Canvas(str(source), pagesize=letter)
        pdf.setFont("Helvetica", 12)
        pdf.drawString(45, 700, "Contact leak.person@example.invalid for access")
        pdf.drawString(45, 560, "Prepared for Fictional Owner")
        pdf.drawString(45, 544, "Holdings")
        pdf.showPage()
        pdf.save()
        doc = fitz.open(source)
        sizes = [(round(page.rect.width, 3), round(page.rect.height, 3)) for page in doc]
        doc.close()
        triage_dir = self.root / "triage" / "sanitized_document_01"
        run_key = os.urandom(32)
        result = sanitizer.verify_output(
            source, sizes, sanitizer.DenylistMatcher(FAKE_TERMS), set(), triage_dir, run_key,
        )
        self.assertEqual(result["release_status"], sanitizer.RELEASE_STATUS_FAIL)
        residuals = result["residuals"]
        self.assertGreaterEqual(len(residuals), 2)
        self.assertEqual({residual["page"] for residual in residuals}, {1})
        categories = {residual["category"] for residual in residuals}
        self.assertIn("email", categories)
        self.assertIn("denylist", categories)
        for residual in residuals:
            self.assertNotIn("shape", residual)
            digest = residual["digest"]
            self.assertEqual(len(digest), 64)
            self.assertTrue(all(ch in "0123456789abcdef" for ch in digest))
            crop = self.root / residual["crop"]
            self.assertTrue(crop.is_file())
            with Image.open(crop) as image:
                self.assertGreater(image.width, 10)

    def test_verifier_residuals_carry_a_bbox_in_pdf_point_space(self) -> None:
        source = self.root / "leaky_bbox.pdf"
        pdf = canvas.Canvas(str(source), pagesize=letter)
        pdf.setFont("Helvetica", 12)
        pdf.drawString(45, 700, "Contact leak.person@example.invalid for access")
        pdf.showPage()
        pdf.save()
        doc = fitz.open(source)
        sizes = [(round(page.rect.width, 3), round(page.rect.height, 3)) for page in doc]
        page_rect = doc[0].rect
        doc.close()
        triage_dir = self.root / "triage" / "sanitized_document_01"
        result = sanitizer.verify_output(
            source, sizes, sanitizer.DenylistMatcher(FAKE_TERMS), set(), triage_dir, os.urandom(32),
        )
        residuals = result["residuals"]
        self.assertGreaterEqual(len(residuals), 1)
        for residual in residuals:
            bbox = residual["bbox"]
            self.assertEqual(len(bbox), 4)
            x0, y0, x1, y1 = bbox
            self.assertLess(x0, x1)
            self.assertLess(y0, y1)
            self.assertGreaterEqual(x0, 0)
            self.assertGreaterEqual(y0, 0)
            self.assertLessEqual(x1, page_rect.width)
            self.assertLessEqual(y1, page_rect.height)

    def two_page_denylist_source(self) -> tuple[Path, list[tuple[float, float]]]:
        source = self.root / "repeated.pdf"
        pdf = canvas.Canvas(str(source), pagesize=letter)
        for _ in range(2):
            pdf.setFont("Helvetica", 12)
            pdf.drawString(45, 700, "Fictional Owner Holdings")
            pdf.showPage()
        pdf.save()
        doc = fitz.open(source)
        sizes = [(round(page.rect.width, 3), round(page.rect.height, 3)) for page in doc]
        doc.close()
        return source, sizes

    def test_repeated_value_gets_the_same_digest_within_one_run(self) -> None:
        source, sizes = self.two_page_denylist_source()
        run_key = os.urandom(32)
        result = sanitizer.verify_output(
            source, sizes, sanitizer.DenylistMatcher(FAKE_TERMS), set(),
            self.root / "triage" / "sanitized_document_01", run_key,
        )
        residuals = [r for r in result["residuals"] if r["category"] == "denylist"]
        self.assertEqual(len(residuals), 2)
        self.assertEqual(residuals[0]["digest"], residuals[1]["digest"])

    def test_same_value_gets_a_different_digest_across_runs(self) -> None:
        source, sizes = self.two_page_denylist_source()
        triage_dir = self.root / "triage" / "sanitized_document_01"
        first = sanitizer.verify_output(
            source, sizes, sanitizer.DenylistMatcher(FAKE_TERMS), set(), triage_dir, os.urandom(32),
        )
        second = sanitizer.verify_output(
            source, sizes, sanitizer.DenylistMatcher(FAKE_TERMS), set(), triage_dir, os.urandom(32),
        )
        first_digest = next(r["digest"] for r in first["residuals"] if r["category"] == "denylist")
        second_digest = next(r["digest"] for r in second["residuals"] if r["category"] == "denylist")
        self.assertNotEqual(first_digest, second_digest)

    def test_digest_cannot_be_narrowed_down_without_the_run_key(self) -> None:
        # masked_shape() leaks a value's length and character classes even
        # without the run key: a report + output PDF pair let an attacker
        # match distinctive shapes against surrounding text. keyed_digest()
        # must not have that property — the digest for a guessed value only
        # matches the report's digest if the guesser also has the run key.
        source, sizes = self.two_page_denylist_source()
        run_key = os.urandom(32)
        result = sanitizer.verify_output(
            source, sizes, sanitizer.DenylistMatcher(FAKE_TERMS), set(),
            self.root / "triage" / "sanitized_document_01", run_key,
        )
        report_digest = next(r["digest"] for r in result["residuals"] if r["category"] == "denylist")
        self.assertEqual(report_digest, sanitizer.keyed_digest(run_key, "Fictional Owner Holdings"))
        for guessed_key in (b"\x00" * 32, b"\xff" * 32, os.urandom(32), os.urandom(32)):
            self.assertNotEqual(
                report_digest, sanitizer.keyed_digest(guessed_key, "Fictional Owner Holdings"),
            )

    def test_digest_hides_length_and_character_class_unlike_masked_shape(self) -> None:
        # masked_shape()'s length equals the original value's length and its
        # character classes (letters vs digits) mirror the original, so
        # shape length/composition alone narrows candidates when matched
        # against surrounding output text. keyed_digest() must not carry
        # that signal: values of very different length and composition
        # collapse to the same fixed-length hex digest.
        short_value = "ZX-FAKE-2048"
        long_value = "Fabricated Engineering Group of North America, LLC"
        self.assertNotEqual(len(short_value), len(long_value))
        self.assertNotEqual(
            len(sanitizer.masked_shape(short_value)), len(sanitizer.masked_shape(long_value)),
        )
        run_key = os.urandom(32)
        short_digest = sanitizer.keyed_digest(run_key, short_value)
        long_digest = sanitizer.keyed_digest(run_key, long_value)
        self.assertEqual(len(short_digest), len(long_digest))
        self.assertNotEqual(short_digest, long_digest)

    def test_masked_shape_output_never_appears_in_the_serialized_report(self) -> None:
        source, sizes = self.two_page_denylist_source()
        result = sanitizer.verify_output(
            source, sizes, sanitizer.DenylistMatcher(FAKE_TERMS), set(),
            self.root / "triage" / "sanitized_document_01", os.urandom(32),
        )
        serialized = json.dumps(result)
        self.assertNotIn('"shape"', serialized)
        self.assertNotIn(sanitizer.masked_shape("Fictional Owner Holdings"), serialized)

    def test_cross_block_page_stream_artifact_is_not_flagged(self) -> None:
        # Two far-apart blocks whose concatenation in the old whole-page text
        # stream forms an address-shaped string ("3 PROVIDE MICRO DUCT" +
        # "WAY ...") — the audit's spec-clause false-positive class.
        source = self.root / "artifact.pdf"
        pdf = canvas.Canvas(str(source), pagesize=letter)
        pdf.setFont("Helvetica", 12)
        pdf.drawString(45, 700, "3 PROVIDE MICRO DUCT")
        pdf.drawString(45, 470, "WAY FINDING SIGNAGE PER SCHEDULE")
        pdf.showPage()
        pdf.save()
        destination, report = self.run_sanitizer(source)
        self.assertEqual(report["release_status"], sanitizer.RELEASE_STATUS_AUTOMATED_PASS)
        self.assertEqual(report.get("residuals", []), [])
        output = fitz.open(destination)
        text = output[0].get_text("text")
        output.close()
        self.assertIn("MICRO DUCT", text)
        self.assertIn("WAY FINDING", text)

    def test_rotated_text_page_keeps_its_geometry_through_flattening(self) -> None:
        source = self.root / "rotated.pdf"
        create_rotated_text_pdf(source)
        destination, report = self.run_sanitizer(source)
        self.assertEqual(report["release_status"], sanitizer.RELEASE_STATUS_AUTOMATED_PASS)
        self.assertEqual(report["size_mismatch_pages"], [])
        # Fixed at the Ghostscript layer, not by falling back to raster.
        self.assertEqual(report["rasterized_pages"], [])
        output = fitz.open(destination)
        self.assertEqual(
            (round(output[0].rect.width), round(output[0].rect.height)),
            (round(letter[0]), round(letter[1])),
        )
        text = output[0].get_text("text")
        output.close()
        self.assertIn("GYPSUM", text)
        self.assertIn("VAPOR", text)

    def test_reconcile_page_geometry_restores_rotation_flags(self) -> None:
        reference = fitz.open()
        reference.new_page(width=612, height=792)
        # Same MediaBox, rotation flag flipped: lossless restore expected.
        flagged = fitz.open()
        page = flagged.new_page(width=612, height=792)
        page.set_rotation(90)
        self.assertEqual(sanitizer.reconcile_page_geometry(reference, flagged), set())
        self.assertEqual(
            (round(flagged[0].rect.width), round(flagged[0].rect.height)), (612, 792),
        )
        # Genuinely different geometry: unreconcilable, must be reported.
        resized = fitz.open()
        resized.new_page(width=792, height=612)
        self.assertEqual(sanitizer.reconcile_page_geometry(reference, resized), {1})
        reference.close()
        flagged.close()
        resized.close()

    def test_post_flatten_sweep_redacts_text_first_visible_after_flattening(self) -> None:
        # Ghostscript rewrites content streams, so text that never extracts
        # contiguously from the source can extract contiguously from the
        # flattened output. Simulate that deterministically: a flatten stub
        # that injects a denylist term into the flattened document.
        def injecting_flatten(source, destination, settings):
            doc = fitz.open(source)
            doc[0].insert_text((72, 300), "Fictional Owner Holdings", fontsize=12)
            doc.save(destination)
            doc.close()
            return set()

        source = self.root / "asymmetry.pdf"
        pdf = canvas.Canvas(str(source), pagesize=letter)
        pdf.setFont("Helvetica", 12)
        pdf.drawString(45, 700, "Ordinary technical text: 4000 psi concrete mix")
        pdf.save()
        with mock.patch.object(sanitizer, "flatten_with_ghostscript", injecting_flatten):
            destination, report = self.run_sanitizer(source)
        self.assertEqual(report["release_status"], sanitizer.RELEASE_STATUS_AUTOMATED_PASS)
        self.assertGreaterEqual(report["post_flatten_redactions"].get("denylist", 0), 1)
        output = fitz.open(destination)
        text = output[0].get_text("text")
        output.close()
        self.assertNotIn("Fictional", text)
        self.assertIn("4000 psi", text)

    def test_normalize_case_for_ner_preserves_length_and_offsets(self) -> None:
        all_caps = "PROVIDE ACCESS PANELS PER UNLISTED FABRICATED CONSULTANTS LLC\nDETAIL 4/A-501"
        recased = sanitizer.normalize_case_for_ner(all_caps)
        self.assertEqual(len(recased), len(all_caps))
        self.assertNotEqual(recased, all_caps)
        self.assertTrue(recased.startswith("Provide Access Panels"))
        # Non-alpha characters and positions are untouched.
        for original, transformed in zip(all_caps, recased):
            if not original.isalpha():
                self.assertEqual(original, transformed)
        # Mixed-case text is left alone entirely.
        mixed = "Provide access panels per approved shop drawings"
        self.assertEqual(sanitizer.normalize_case_for_ner(mixed), mixed)
        self.assertEqual(sanitizer.normalize_case_for_ner("4/A-501 §2.3"), "4/A-501 §2.3")

    def test_ner_chunks_split_at_line_boundaries_and_reassemble(self) -> None:
        lines = [f"LINE {index} " + "x" * 40 for index in range(30)]
        text = "\n".join(lines)
        chunks = list(sanitizer.ner_chunks(text, max_chars=200))
        self.assertGreater(len(chunks), 1)
        for offset, chunk in chunks:
            self.assertEqual(text[offset:offset + len(chunk)], chunk)
            self.assertLessEqual(len(chunk), 200)
            self.assertFalse(chunk.startswith("\n"))
            self.assertFalse(chunk.endswith("\n"))
        # Chunks cover the text without losing any line.
        joined = "\n".join(chunk for _, chunk in chunks)
        self.assertEqual(joined, text)
        # A single overlong line is yielded whole, not split mid-line.
        long_line = "y" * 500
        self.assertEqual(list(sanitizer.ner_chunks(long_line, max_chars=200)), [(0, long_line)])

    @staticmethod
    def stub_ner_predict(target: str, label: str, score: float):
        def predict(texts, labels, threshold):
            results = []
            for text in texts:
                entities = []
                cursor = 0
                while (found := text.find(target, cursor)) >= 0:
                    entities.append({
                        "start": found, "end": found + len(target),
                        "label": label, "score": score,
                    })
                    cursor = found + 1
                results.append(entities)
            return results
        return predict

    def test_ner_review_is_report_only_with_keyed_findings_and_crops(self) -> None:
        source = self.root / "unlisted.pdf"
        pdf = canvas.Canvas(str(source), pagesize=letter)
        pdf.setFont("Helvetica", 12)
        # An unlisted firm: no label, no denylist term, no direct pattern —
        # the audit's structural recall hole. It must survive sanitization
        # untouched and then be flagged by the report-only NER layer.
        pdf.drawString(45, 700, "Coordinate ductwork with Unlisted Fabricated Consultants prior to rough-in")
        pdf.drawString(45, 660, "TECHNICAL: galvanized sheet metal per SMACNA standards")
        pdf.showPage()
        pdf.save()
        detector = sanitizer.NerDetector(
            self.stub_ner_predict("Unlisted Fabricated Consultants", "company name", 0.91),
            ("company name",), 0.5, "stub-model",
        )
        destination = self.root / "sanitized_document_01.pdf"
        report = sanitizer.sanitize_document(
            source, destination, "sanitized_document_01", FAKE_TERMS,
            self.settings, self.root, os.urandom(32), ner_detector=detector,
        )
        # Report-only: findings never change the verdict or the checks.
        self.assertEqual(report["release_status"], sanitizer.RELEASE_STATUS_AUTOMATED_PASS)
        self.assertTrue(all(report["checks"].values()))
        review = report["ner_review"]
        self.assertEqual(review["mode"], "report_only")
        self.assertEqual(review["finding_counts"], {"company name": 1})
        self.assertEqual(review["findings_truncated"], 0)
        self.assertEqual(review["distinct_form_counts"], {"company name": 1})
        finding = review["findings"][0]
        self.assertEqual(finding["pages"], [1])
        self.assertEqual(finding["occurrences"], 1)
        self.assertEqual(finding["label"], "company name")
        self.assertAlmostEqual(finding["score_max"], 0.91)
        self.assertNotIn("shape", finding)
        self.assertEqual(len(finding["digest"]), 64)
        self.assertTrue(all(ch in "0123456789abcdef" for ch in finding["digest"]))
        crop = self.root / finding["crop"]
        self.assertTrue(crop.is_file())
        with Image.open(crop) as image:
            self.assertGreater(image.width, 10)
        # The firm name itself must appear nowhere in the serialized report.
        self.assertNotIn("Unlisted", json.dumps(report))
        # The output document is unchanged by the review layer.
        output = fitz.open(destination)
        text = output[0].get_text("text")
        output.close()
        self.assertIn("Unlisted Fabricated Consultants", text)
        self.assertIn("SMACNA", text)

    def test_ner_finding_carries_a_bbox_in_pdf_point_space(self) -> None:
        source = self.root / "unlisted_bbox.pdf"
        pdf = canvas.Canvas(str(source), pagesize=letter)
        pdf.setFont("Helvetica", 12)
        pdf.drawString(45, 700, "Coordinate ductwork with Unlisted Fabricated Consultants prior to rough-in")
        pdf.showPage()
        pdf.save()
        doc = fitz.open(source)
        page_rect = doc[0].rect
        doc.close()
        detector = sanitizer.NerDetector(
            self.stub_ner_predict("Unlisted Fabricated Consultants", "company name", 0.91),
            ("company name",), 0.5, "stub-model",
        )
        destination = self.root / "sanitized_document_01.pdf"
        report = sanitizer.sanitize_document(
            source, destination, "sanitized_document_01", FAKE_TERMS,
            self.settings, self.root, os.urandom(32), ner_detector=detector,
        )
        finding = report["ner_review"]["findings"][0]
        bbox = finding["bbox"]
        self.assertEqual(len(bbox), 4)
        x0, y0, x1, y1 = bbox
        self.assertLess(x0, x1)
        self.assertLess(y0, y1)
        self.assertGreaterEqual(x0, 0)
        self.assertGreaterEqual(y0, 0)
        self.assertLessEqual(x1, page_rect.width)
        self.assertLessEqual(y1, page_rect.height)

    def test_ner_findings_dedupe_and_truncate_without_affecting_counts(self) -> None:
        source = self.root / "two_firms.pdf"
        pdf = canvas.Canvas(str(source), pagesize=letter)
        pdf.setFont("Helvetica", 12)
        pdf.drawString(45, 700, "Alpha Fictitious Builders shall coordinate with field staff")
        pdf.drawString(45, 660, "Alpha Fictitious Builders retains record documents on site")
        pdf.showPage()
        pdf.save()
        doc = fitz.open(source)
        sizes = [(round(page.rect.width, 3), round(page.rect.height, 3)) for page in doc]
        doc.close()

        base = self.stub_ner_predict("Alpha Fictitious Builders", "company name", 0.6)

        def overlapping_predict(texts, labels, threshold):
            results = base(texts, labels, threshold)
            # Same spans again under another label with a higher score:
            # dedupe must keep one finding per span, the higher-scored label.
            for entities in results:
                for entity in list(entities):
                    entities.append({**entity, "label": "organization", "score": 0.85})
            return results

        detector = sanitizer.NerDetector(
            overlapping_predict, ("company name", "organization"), 0.5, "stub-model",
            max_findings={"_default": 1},
        )
        result = sanitizer.verify_output(
            source, sizes, sanitizer.DenylistMatcher(FAKE_TERMS), set(),
            self.root / "triage" / "sanitized_document_01", os.urandom(32), ner_detector=detector,
        )
        review = result["ner_review"]
        # Two occurrences, each span deduped to the higher-scored label...
        self.assertEqual(review["finding_counts"], {"organization": 2})
        # ...and both collapse to ONE surface form, so the reviewer sees one
        # decision carrying its occurrence count rather than two identical
        # entries. Nothing is truncated: the cap now bounds distinct forms.
        self.assertEqual(review["distinct_form_counts"], {"organization": 1})
        self.assertEqual(len(review["findings"]), 1)
        self.assertEqual(review["findings_truncated"], 0)
        self.assertEqual(review["findings"][0]["label"], "organization")
        self.assertEqual(review["findings"][0]["occurrences"], 2)
        self.assertEqual(review["findings"][0]["pages"], [1])
        # The NER layer is absent from reports when no detector is supplied.
        without = sanitizer.verify_output(
            source, sizes, sanitizer.DenylistMatcher(FAKE_TERMS), set(),
            self.root / "triage" / "sanitized_document_01", os.urandom(32),
        )
        self.assertNotIn("ner_review", without)

    @staticmethod
    def stub_multi_ner_predict(targets_by_label: dict[str, list[str]]):
        def predict(texts, labels, threshold):
            results = []
            for text in texts:
                entities = []
                for label, targets in targets_by_label.items():
                    if label not in labels:
                        continue
                    for target in targets:
                        cursor = 0
                        while (found := text.find(target, cursor)) >= 0:
                            entities.append({
                                "start": found, "end": found + len(target),
                                "label": label, "score": 0.9,
                            })
                            cursor = found + 1
                results.append(entities)
            return results
        return predict

    def test_per_label_cap_does_not_crowd_out_a_low_volume_label(self) -> None:
        source = self.root / "many_firms.pdf"
        pdf = canvas.Canvas(str(source), pagesize=letter)
        pdf.setFont("Helvetica", 12)
        pdf.drawString(45, 700, "Coordinate with Fictitious Firm Alpha on rough-in")
        pdf.drawString(45, 680, "Coordinate with Fictitious Firm Beta on rough-in")
        pdf.drawString(45, 660, "Coordinate with Fictitious Firm Gamma on rough-in")
        pdf.drawString(45, 640, "Site access via 742 Evergreen Terrace Fictional Lane")
        pdf.showPage()
        pdf.save()
        doc = fitz.open(source)
        sizes = [(round(page.rect.width, 3), round(page.rect.height, 3)) for page in doc]
        doc.close()

        predict = self.stub_multi_ner_predict({
            "organization": [
                "Fictitious Firm Alpha", "Fictitious Firm Beta", "Fictitious Firm Gamma",
            ],
            "street address": ["742 Evergreen Terrace Fictional Lane"],
        })
        detector = sanitizer.NerDetector(
            predict, ("organization", "street address"), 0.5, "stub-model",
            # A single shared budget would let the three organization forms
            # exhaust it before the one street address is ever seen.
            max_findings={"_default": 1, "street address": 10},
        )
        result = sanitizer.verify_output(
            source, sizes, sanitizer.DenylistMatcher(FAKE_TERMS), set(),
            self.root / "triage" / "sanitized_document_02", os.urandom(32), ner_detector=detector,
        )
        review = result["ner_review"]
        # Only one of the three organization forms fits under its cap of 1...
        self.assertEqual(review["distinct_form_counts"].get("organization"), 1)
        # ...but the low-volume street address is never crowded out by it.
        self.assertEqual(review["distinct_form_counts"].get("street address"), 1)
        labels_seen = {finding["label"] for finding in review["findings"]}
        self.assertIn("street address", labels_seen)
        self.assertEqual(review["findings_truncated"], 2)

    def test_findings_tie_break_order_is_stable_across_runs(self) -> None:
        # Two distinct findings tied on occurrences and label. The sort's
        # tie-break must not depend on the digest (keyed by a fresh random
        # secret each run) or the same document would order its findings
        # differently from run to run for no reason tied to its content.
        source = self.root / "two_unlisted_firms.pdf"
        pdf = canvas.Canvas(str(source), pagesize=letter)
        pdf.setFont("Helvetica", 12)
        pdf.drawString(45, 700, "Alpha Fictitious Builders shall coordinate with field staff")
        pdf.drawString(45, 660, "Zeta Imaginary Contractors retains record documents")
        pdf.showPage()
        pdf.save()
        doc = fitz.open(source)
        sizes = [(round(page.rect.width, 3), round(page.rect.height, 3)) for page in doc]
        doc.close()

        def two_firm_predict(texts, labels, threshold):
            results = []
            for text in texts:
                entities = []
                for target in ("Alpha Fictitious Builders", "Zeta Imaginary Contractors"):
                    found = text.find(target)
                    if found >= 0:
                        entities.append({
                            "start": found, "end": found + len(target),
                            "label": "company name", "score": 0.7,
                        })
                results.append(entities)
            return results

        detector = sanitizer.NerDetector(two_firm_predict, ("company name",), 0.5, "stub-model")
        triage_dir = self.root / "triage" / "sanitized_document_01"

        # Spy on keyed_digest to learn, for this test only, which original
        # text produced which digest — the report itself never carries this
        # mapping. This lets the test verify the sort order follows the
        # deterministic surface form, not the random per-run digest.
        real_keyed_digest = sanitizer.keyed_digest
        digest_to_text: dict[str, str] = {}

        def spy_keyed_digest(key: bytes, value: str) -> str:
            digest = real_keyed_digest(key, value)
            digest_to_text[digest] = value
            return digest

        with mock.patch.object(sanitizer, "keyed_digest", side_effect=spy_keyed_digest):
            result = sanitizer.verify_output(
                source, sizes, sanitizer.DenylistMatcher(FAKE_TERMS), set(), triage_dir, os.urandom(32),
                ner_detector=detector,
            )
        findings = result["ner_review"]["findings"]
        self.assertEqual(len(findings), 2)
        texts_in_order = [digest_to_text[f["digest"]] for f in findings]
        self.assertEqual(texts_in_order, sorted(texts_in_order, key=str.casefold))

    def test_review_report_serializes_without_sensitive_values(self) -> None:
        source = self.root / "source.pdf"
        create_searchable_pdf(source)
        _, report = self.run_sanitizer(source)
        serialized = json.dumps(report)
        for term in FAKE_TERMS:
            self.assertNotIn(term, serialized)
        self.assertIn('"page"', serialized)
        self.assertIn('"categories"', serialized)


REPO_LEXICONS = Path(__file__).parents[1] / "config" / "lexicons"
REPO_ALLOWLIST = Path(__file__).parents[1] / "config" / "allowlist.shared.json"


class LexiconTest(unittest.TestCase):
    """The shipped lexicons are the shared suppression vocabulary for every
    detector layer, so their scoping is asserted against the real files."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.lex = sanitizer.load_lexicons(REPO_LEXICONS, REPO_ALLOWLIST)

    def test_missing_lexicon_directory_yields_no_suppression(self) -> None:
        empty = sanitizer.load_lexicons(Path("does/not/exist"), Path("also/missing.json"))
        self.assertIsNone(empty.suppression_reason("Owner"))
        self.assertIsNone(empty.rejects_proposed_term("END OF SECTION"))

    def test_defined_terms_match_whole_span_only(self) -> None:
        # The bare defined term is boilerplate on every project...
        for bare in ("Owner", "owner", "Owner's", "Owner\u2019s", "Contractor", "Bidder", "City"):
            with self.subTest(bare=bare):
                reason = self.lex.suppression_reason(bare)
                self.assertIsNotNone(reason, f"{bare!r} should be suppressed")
                self.assertTrue(reason.startswith("contract_defined_term:"))
        # ...but a compound name containing one is a real identifier.
        for compound in ("City of Springfield", "City of Panama City",
                         "Owner Builders LLC", "Contractor Supply Company"):
            with self.subTest(compound=compound):
                self.assertIsNone(self.lex.suppression_reason(compound))

    def test_structural_patterns_suppress_document_artifacts(self) -> None:
        cases = {
            "32 13 13": "csi_masterformat_code",
            "28 46 21.11": "csi_masterformat_code",
            "ASTM A36": "standards_designation",
            "3 CIR": "panel_circuit_reference",
            "16 inches o.c.": "dimension_callout",
            "PROJECT NORTH": "north_arrow_label",
            "S H E E T  T I T L E": "letter_spaced_label",
        }
        for span, expected in cases.items():
            with self.subTest(span=span):
                self.assertEqual(self.lex.suppression_reason(span), f"structural:{expected}")

    def test_real_identifiers_are_never_suppressed(self) -> None:
        for span in ("Magnum Engineering", "CCR-21109", "MLK RECREATION CENTER",
                     "East 14th Street", "James T. Vickers", "705 East 14th Court"):
            with self.subTest(span=span):
                self.assertIsNone(self.lex.suppression_reason(span))

    def test_shared_allowlist_suppresses_manufacturers(self) -> None:
        for span in ("Cooper Lighting", "Jay R Smith", "The American Institute of Architects"):
            with self.subTest(span=span):
                reason = self.lex.suppression_reason(span)
                self.assertTrue(reason and reason.startswith("shared_allowlist:"))

    def test_proposal_rules_keep_long_identifiers_and_reject_boilerplate(self) -> None:
        # Long by construction: a full address, a CAD save-path, a digit-heavy
        # project code. None may be rejected by the length or prose rules.
        keep = [
            "705 14th Court East, Panama City, Florida 32401",
            "C:\\Users\\tamaraf\\Documents\\21109 Panama City MLK - Scheme 2v20.rvt",
            "CCR-21109",
            "City of Panama City, Florida",
            "Mar\u019fn Luther King Jr. Recrea\u019fon Center",
            "Cohen Carnaggio Reynolds, Inc.",
        ]
        for term in keep:
            with self.subTest(term=term):
                self.assertIsNone(self.lex.rejects_proposed_term(term))
        reject = [
            "END OF SECTION", "General Contractor", "Treat plywood indicated on Drawings.",
            "Institute of Electrical and Electronics Engineers",
            "\u00a9 ADVANCE TABCO, MARCH 2017", "rTlfld ifiefti",
            "Subcontractors, W.._orkby.", "supplier of equipment.",
            "report copyright violations, e-mail copyright@aia.org.",
        ]
        for term in reject:
            with self.subTest(term=term):
                self.assertIsNotNone(self.lex.rejects_proposed_term(term))


class DenylistSeedingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_project_metadata_seeds_every_listed_variant(self) -> None:
        path = self.root / "project.json"
        path.write_text(json.dumps({
            "_about": {"ignored": "underscore keys are skipped"},
            "project_name": ["Example Confidential Project", "ECP Phase Two"],
            "architect": "Imaginary Architecture Studio",
            "project_number": ["ZX-FAKE-2048"],
        }), encoding="utf-8")
        terms = sanitizer.load_project_metadata(path)
        self.assertEqual(terms, {
            "Example Confidential Project", "ECP Phase Two",
            "Imaginary Architecture Studio", "ZX-FAKE-2048",
        })

    def test_project_metadata_rejects_unusable_input(self) -> None:
        empty = self.root / "empty.json"
        empty.write_text(json.dumps({"owner": []}), encoding="utf-8")
        with self.assertRaises(SystemExit):
            sanitizer.load_project_metadata(empty)
        with self.assertRaises(SystemExit):
            sanitizer.load_project_metadata(self.root / "absent.json")

    def test_intake_empty_fields_none_path_is_every_field(self) -> None:
        self.assertEqual(
            sanitizer.intake_empty_fields(None), list(sanitizer.PROJECT_METADATA_FIELDS),
        )

    def test_intake_empty_fields_reports_exactly_the_blank_ones(self) -> None:
        path = self.root / "partial.json"
        path.write_text(json.dumps({
            "project_name": "Example Confidential Project",
            "project_number": "",
            "project_address": None,
            "owner": [],
            "architect": ["   "],
            "personnel": ["Jane Doe"],
        }), encoding="utf-8")
        empty = sanitizer.intake_empty_fields(path)
        self.assertEqual(sorted(empty), sorted([
            "project_number", "project_address", "site_address", "owner",
            "architect", "engineers", "contractors", "consultants",
            "other_identifiers",
        ]))
        self.assertNotIn("project_name", empty)
        self.assertNotIn("personnel", empty)

    def test_proposals_are_filtered_and_never_written_as_a_denylist(self) -> None:
        source = self.root / "source.pdf"
        create_searchable_pdf(source)
        lex = sanitizer.load_lexicons(REPO_LEXICONS, REPO_ALLOWLIST)
        candidates = self.root / "candidates.json"
        count = sanitizer.write_denylist_candidates([source], candidates, lex)
        payload = json.loads(candidates.read_text(encoding="utf-8"))
        self.assertEqual(count, len(payload["candidates"]))
        self.assertIn("UNCONFIRMED", payload["_about"]["status"])
        # Every proposal carries why it was proposed and how often it occurred.
        for entry in payload["candidates"]:
            self.assertTrue(entry["reasons"])
            self.assertGreaterEqual(entry["occurrences"], 1)
        # The candidates file must never be loadable as a live denylist: its
        # only list holds objects, so load_denylist finds no usable terms.
        with self.assertRaises(SystemExit):
            sanitizer.load_denylist(candidates)

    def test_proposals_strip_role_label_prefixes(self) -> None:
        lex = sanitizer.load_lexicons(REPO_LEXICONS, REPO_ALLOWLIST)
        source = self.root / "labelled.pdf"
        pdf = canvas.Canvas(str(source), pagesize=letter)
        pdf.setFont("Helvetica", 11)
        pdf.drawString(72, 700, "Architect: Imaginary Architecture Studio")
        pdf.drawString(72, 680, "Architect/Engineer: Fabricated Engineering Group")
        pdf.save()
        doc = fitz.open(source)
        proposed = set(sanitizer.derive_term_candidates(doc, source, lex))
        doc.close()
        # The bare firm name, not "Architect: <firm>", which would only ever
        # match where the label shares a line with the value.
        self.assertIn("Imaginary Architecture Studio", proposed)
        self.assertIn("Fabricated Engineering Group", proposed)
        for term in proposed:
            self.assertNotIn("Architect:", term)


class PageScopeTest(unittest.TestCase):
    """A denylist term split across MuPDF blocks must still be found.

    Block-scoping the denylist kept a firm name on every sheet of a real
    drawing set while the run reported PASS, because the title block put
    "CCR Architecture &" and "Interiors" in different blocks and neither
    detection nor verification could see the phrase.
    """

    @staticmethod
    def line(block: int, index: int, text: str, top: float) -> object:
        return sanitizer.Line(
            block=block, line=index, text=text, words=(),
            rect=fitz.Rect(40, top, 400, top + 12),
        )

    def setUp(self) -> None:
        # Separate MuPDF blocks, as a real title block produces, and
        # geometrically interleaved: the second half of the firm name sits
        # far above the first with an unrelated line between them.
        self.lines = [
            self.line(0, 0, "Imaginary Architecture &", 700),
            self.line(1, 0, "Studio", 100),
            self.line(4, 0, "AS BEING NECESSARY TO PRODUCE", 400),
        ]
        self.matcher = sanitizer.DenylistMatcher({"Imaginary Architecture & Studio"})

    def test_page_block_uses_reading_order_not_geometry(self) -> None:
        text = sanitizer.page_text_block(self.lines).text
        self.assertIn("Imaginary Architecture &\nStudio", text)
        # Geometric order would have separated them.
        geometric = "\n".join(
            line.text for line in sorted(self.lines, key=lambda l: (l.rect.y0, l.rect.x0))
        )
        self.assertNotIn("Imaginary Architecture &\nStudio", geometric)
        self.assertEqual(self.matcher.count(geometric), 0)

    def test_the_split_term_is_matched_at_page_scope_only(self) -> None:
        blocks = sanitizer.text_blocks(self.lines)
        block_only = [c for c, _b, _m in sanitizer.block_matches(blocks, self.matcher)]
        self.assertNotIn("denylist", block_only)
        page_scoped = [
            c for c, _b, _m in
            sanitizer.block_matches(blocks, self.matcher, sanitizer.page_text_block(self.lines))
        ]
        self.assertIn("denylist", page_scoped)

    def test_direct_patterns_stay_block_scoped(self) -> None:
        # The cross-block stitching risk that keeps patterns narrow: two
        # unrelated blocks must not combine into a phantom street address.
        lines = [
            self.line(0, 0, "3 PROVIDE MICRO DUCT", 700),
            self.line(7, 0, "WAY FINDING SIGNAGE", 120),
        ]
        blocks = sanitizer.text_blocks(lines)
        page_block = sanitizer.page_text_block(lines)
        found = [
            c for c, _b, _m in sanitizer.block_matches(blocks, self.matcher, page_block)
        ]
        self.assertNotIn("street_address", found)


class FilePathPatternTest(unittest.TestCase):
    """CAD/BIM save paths in drawing revision footers leak the drafter's OS
    username and often the project name. Deterministic, so it belongs in the
    regex layer rather than depending on a denylist entry per project."""

    def setUp(self) -> None:
        self.pattern = sanitizer.DIRECT_PATTERNS["file_path"]

    def test_matches_save_paths_from_any_project(self) -> None:
        for path in (
            r"C:\Users\raheal\Documents\Panama City MLK - V",
            r"C:\Users\jdoe\OneDrive\2031 Riverside Library_Mech.rvt",
            r"\\cadserver\projects\job1042\sheets.dwg",
            "/Users/jsmith/Projects/site-plan.dwg",
            "/home/drafter/cad/level1.dxf",
        ):
            with self.subTest(path=path):
                self.assertIsNotNone(self.pattern.search(path))

    def test_does_not_match_ordinary_specification_text(self) -> None:
        for text in (
            "Section 32 13 13 - Concrete Paving", "ASTM A36 structural steel",
            "Scale 1:2 at the ratio shown", "See item C: below",
            "Provide 3/4 inch conduit", "Type II Portland cement",
        ):
            with self.subTest(text=text):
                self.assertIsNone(self.pattern.search(text))

    def test_a_save_path_is_redacted_without_any_denylist_entry(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        source = root / "sheet.pdf"
        pdf = canvas.Canvas(str(source), pagesize=letter)
        pdf.setFont("Helvetica", 8)
        pdf.drawString(40, 60, r"C:\Users\anotherdrafter\Documents\9988 Elsewhere Center_M.rvt")
        pdf.drawString(40, 700, "GALVANIZED SHEET METAL PER SMACNA STANDARDS")
        pdf.showPage()
        pdf.save()
        sanitizer.sanitize_document(
            source, root / "out.pdf", "sanitized_document_01",
            FAKE_TERMS, sanitizer.Settings(), root, os.urandom(32),
            lexicons=sanitizer.load_lexicons(REPO_LEXICONS, REPO_ALLOWLIST),
        )
        output = fitz.open(root / "out.pdf")
        text = output[0].get_text("text")
        output.close()
        self.assertNotIn("anotherdrafter", text)
        self.assertNotIn("Elsewhere", text)
        self.assertIn("SMACNA", text)


class GoldenSetTest(unittest.TestCase):
    """The release gate, enforced. Recall on real identifiers must stay at
    100% and over-redaction of must-survive content at 0%, measured against
    tests/golden/mlk_labels.json rather than judged by eye."""

    @classmethod
    def setUpClass(cls) -> None:
        path = Path(__file__).parents[1] / "tools" / "eval_sanitizer.py"
        spec = importlib.util.spec_from_file_location("eval_sanitizer", path)
        cls.evaluator = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = cls.evaluator
        spec.loader.exec_module(cls.evaluator)
        root = Path(__file__).parents[1]
        cls.result = cls.evaluator.evaluate(
            root / "tests/golden/mlk_labels.json",
            root / "config/denylist.local.json",
            root / "config/lexicons",
            root / "config/allowlist.shared.json",
        )

    def test_every_known_identifier_is_still_redacted(self) -> None:
        leaks = [f for f in self.result["failures"] if f[0] == "LEAK"]
        self.assertEqual(leaks, [], f"identifiers no longer redacted: {leaks}")
        self.assertEqual(self.result["party_redacted"], self.result["party_total"])

    def test_nothing_that_must_survive_is_redacted(self) -> None:
        over = [f for f in self.result["failures"] if f[0] == "OVER-REDACTED"]
        self.assertEqual(over, [], f"technical content destroyed: {over}")
        self.assertEqual(self.result["survive_redacted"], 0)

    def test_hard_negatives_are_not_suppressed(self) -> None:
        wrong = [f for f in self.result["failures"] if f[0] == "WRONGLY SUPPRESSED"]
        self.assertEqual(wrong, [], f"real identifiers suppressed: {wrong}")

    def test_derivation_would_not_drop_a_known_identifier(self) -> None:
        dropped = [f for f in self.result["failures"] if f[0] == "DERIVATION WOULD DROP"]
        self.assertEqual(dropped, [], f"proposal rules reject real identifiers: {dropped}")

    def test_noise_stays_out_of_the_review_queue(self) -> None:
        # A regression here is reviewer minutes, not a breach, so it is a
        # floor rather than an equality.
        handled = self.result["noisy_handled"] / max(self.result["noisy_total"], 1)
        self.assertGreaterEqual(handled, 0.90, f"noise handling fell to {handled:.0%}")


class SuppressionSymmetryTest(unittest.TestCase):
    """Detection, the post-flatten sweep, and verification share one
    suppression decision. If they ever disagree the run fails on its own
    suppressed noise, which is the asymmetry the audit called out."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.settings = sanitizer.Settings()
        self.lex = sanitizer.load_lexicons(REPO_LEXICONS, REPO_ALLOWLIST)

    # A ruled panel schedule, the way MEP sheets actually draw one, so
    # find_tables() has the geometry it needs.
    COLUMNS = (45, 80, 125, 285, 320, 355, 500)
    ROWS = (
        ("CKT", "TRIP", "DESCRIPTION", "CKT", "TRIP", "DESCRIPTION"),
        ("1", "20 A", "OUTDOOR CONCESSION PAD", "24", "21", "BASKETBALL COURT"),
        ("3", "20 A", "PLAYGROUND LIGHTS", "20", "17", "BASKETBALL COURT"),
    )

    def build(self) -> Path:
        source = self.root / "schedule.pdf"
        pdf = canvas.Canvas(str(source), pagesize=letter)
        pdf.setFont("Helvetica", 9)
        top, height = 720, 18
        for index, row in enumerate(self.ROWS):
            baseline = top - index * height
            for column, value in zip(self.COLUMNS, row):
                pdf.drawString(column + 2, baseline + 5, value)
        bottom = top - len(self.ROWS) * height
        for index in range(len(self.ROWS) + 1):
            y = top - index * height + height
            pdf.line(self.COLUMNS[0], y, self.COLUMNS[-1], y)
        for column in self.COLUMNS:
            pdf.line(column, top + height, column, bottom + height)
        # Outside the table: a MasterFormat code and a circuit cell, both
        # handled by the structural lexicon rather than by table geometry.
        pdf.setFont("Helvetica", 11)
        pdf.drawString(45, 640, "3 CIR")
        pdf.drawString(45, 620, "32 13 13 Concrete Paving")
        # A genuine address must still be redacted.
        pdf.drawString(45, 590, "Site office at 4120 Sycamore Boulevard")
        pdf.showPage()
        pdf.save()
        return source

    def run_with(self, lexicons) -> dict:
        source = self.build()
        return sanitizer.sanitize_document(
            source, self.root / "out.pdf", "sanitized_document_01",
            FAKE_TERMS, self.settings, self.root, os.urandom(32), lexicons=lexicons,
        )

    def test_schedule_cells_survive_and_the_run_still_passes(self) -> None:
        report = self.run_with(self.lex)
        self.assertEqual(report["release_status"], sanitizer.RELEASE_STATUS_AUTOMATED_PASS)
        # Suppression is counted and attributed, never silent.
        self.assertTrue(report["suppressed_by_rule"])
        self.assertIn("structural", report["suppressed_by_rule"])
        self.assertIn("street_address", report["suppressed_by_category"])
        # The verifier agreed: nothing it saw became a residual.
        self.assertEqual(report["residual_match_counts"], {})
        output = fitz.open(self.root / "out.pdf")
        text = output[0].get_text("text")
        output.close()
        # Technical content preserved...
        self.assertIn("3 CIR", text)
        self.assertIn("BASKETBALL COURT", text)
        self.assertIn("32 13 13", text)
        # ...while the real address is still destroyed.
        self.assertNotIn("Sycamore", text)

    def test_without_lexicons_the_schedule_cells_are_redacted(self) -> None:
        report = self.run_with(None)
        self.assertEqual(report["suppressed_by_rule"], {})
        output = fitz.open(self.root / "out.pdf")
        text = output[0].get_text("text")
        output.close()
        # The pre-lexicon behaviour: table content eaten by the address regex.
        self.assertNotIn("BASKETBALL COURT", text)
        self.assertNotIn("Sycamore", text)


class FingerprintTest(unittest.TestCase):
    """build_fingerprint() hashes match on-disk content, are stable across
    repeated calls, and changing one input never touches another's hash."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="fingerprint_test_")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

        self.denylist_path = self.root / "denylist.json"
        self.denylist_path.write_text(json.dumps({"identifiers": ["Fake Denylist Term"]}))
        self.config_path = self.root / "config.json"
        self.config_path.write_text(json.dumps({"ocr_dpi": 300}))
        self.allowlist_path = self.root / "allowlist.json"
        self.allowlist_path.write_text(json.dumps({"entries": []}))
        self.project_metadata_path = self.root / "project-metadata.json"
        self.project_metadata_path.write_text(json.dumps({"project_name": ["Fake Project"]}))
        self.lexicon_dir = self.root / "lexicons"
        self.lexicon_dir.mkdir()
        for name in sanitizer.LEXICON_FILENAMES:
            (self.lexicon_dir / name).write_text("{}")
        self.script_path = self.root / "script.py"
        self.script_path.write_text("# fake sanitizer script v1\n")

    def build(self, **overrides) -> dict:
        kwargs = dict(
            script_path=self.script_path,
            repo_root=self.root,
            denylist_path=self.denylist_path,
            project_metadata_path=self.project_metadata_path,
            config_path=self.config_path,
            allowlist_path=self.allowlist_path,
            lexicon_dir=self.lexicon_dir,
        )
        kwargs.update(overrides)
        return sanitizer.build_fingerprint(**kwargs)

    def test_hashes_match_actual_on_disk_content(self) -> None:
        fp = self.build()
        self.assertEqual(fp["denylist_sha256"], hashlib.sha256(self.denylist_path.read_bytes()).hexdigest())
        self.assertEqual(
            fp["project_metadata_sha256"],
            hashlib.sha256(self.project_metadata_path.read_bytes()).hexdigest(),
        )
        self.assertEqual(fp["config_sha256"], hashlib.sha256(self.config_path.read_bytes()).hexdigest())
        self.assertEqual(fp["allowlist_sha256"], hashlib.sha256(self.allowlist_path.read_bytes()).hexdigest())
        for name in sanitizer.LEXICON_FILENAMES:
            self.assertEqual(
                fp["lexicon_sha256"][name],
                hashlib.sha256((self.lexicon_dir / name).read_bytes()).hexdigest(),
            )
        self.assertEqual(
            fp["code"]["build_digest_sha256"],
            hashlib.sha256(self.script_path.read_bytes()).hexdigest(),
        )

    def test_repeated_calls_are_identical(self) -> None:
        self.assertEqual(self.build(), self.build())

    def test_changing_config_changes_only_config_hash(self) -> None:
        before = self.build()
        self.config_path.write_text(json.dumps({"ocr_dpi": 999}))
        after = self.build()
        self.assertNotEqual(before["config_sha256"], after["config_sha256"])
        for key in ("denylist_sha256", "project_metadata_sha256", "allowlist_sha256", "lexicon_sha256", "code"):
            self.assertEqual(before[key], after[key], key)

    def test_changing_one_lexicon_file_changes_only_that_entry(self) -> None:
        before = self.build()
        target = sanitizer.LEXICON_FILENAMES[0]
        (self.lexicon_dir / target).write_text(json.dumps({"terms": ["changed"]}))
        after = self.build()
        self.assertNotEqual(before["lexicon_sha256"][target], after["lexicon_sha256"][target])
        for name in sanitizer.LEXICON_FILENAMES:
            if name != target:
                self.assertEqual(before["lexicon_sha256"][name], after["lexicon_sha256"][name])
        for key in ("denylist_sha256", "project_metadata_sha256", "config_sha256", "allowlist_sha256", "code"):
            self.assertEqual(before[key], after[key], key)

    def test_missing_optional_inputs_hash_to_none(self) -> None:
        fp = self.build(
            project_metadata_path=None,
            allowlist_path=self.root / "missing_allowlist.json",
            denylist_path=self.root / "missing_denylist.json",
        )
        self.assertIsNone(fp["project_metadata_sha256"])
        self.assertIsNone(fp["allowlist_sha256"])
        self.assertIsNone(fp["denylist_sha256"])

    def test_missing_lexicon_directory_yields_empty_dict(self) -> None:
        fp = self.build(lexicon_dir=self.root / "does-not-exist")
        self.assertEqual(fp["lexicon_sha256"], {})

    def test_stray_lexicon_file_is_ignored(self) -> None:
        # A file in the lexicon directory that isn't one of the fixed
        # LEXICON_FILENAMES has zero effect on sanitizer behaviour (per
        # load_lexicons) and must not spuriously change the fingerprint.
        before = self.build()
        (self.lexicon_dir / "unrelated_notes.json").write_text(json.dumps({"scratch": True}))
        after = self.build()
        self.assertEqual(before, after)
        self.assertNotIn("unrelated_notes.json", after["lexicon_sha256"])


class CodeIdentityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="code_identity_test_")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_build_digest_matches_script_bytes_and_changes_on_edit(self) -> None:
        script = self.root / "script.py"
        script.write_text("v1\n")
        first = sanitizer.code_identity(script, self.root)
        self.assertEqual(first["build_digest_sha256"], hashlib.sha256(b"v1\n").hexdigest())
        script.write_text("v2\n")
        second = sanitizer.code_identity(script, self.root)
        self.assertEqual(second["build_digest_sha256"], hashlib.sha256(b"v2\n").hexdigest())
        self.assertNotEqual(first["build_digest_sha256"], second["build_digest_sha256"])

    def test_no_git_repo_yields_no_commit(self) -> None:
        script = self.root / "script.py"
        script.write_text("no git here\n")
        # Merge into the existing environment rather than replacing it —
        # replacing wholesale drops PATH and makes git itself unresolvable,
        # which would pass this test for the wrong reason.
        with mock.patch.dict(os.environ, {"GIT_CEILING_DIRECTORIES": str(self.root)}):
            identity = sanitizer.code_identity(script, self.root)
        self.assertIsNone(identity["commit"])
        self.assertIsNone(identity["commit_dirty"])

    def test_real_repo_reports_commit_and_dirty_flag(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        identity = sanitizer.code_identity(MODULE_PATH, repo_root)
        self.assertRegex(identity["commit"], r"^[0-9a-f]{40}$")
        self.assertIn(identity["commit_dirty"], (True, False))

    def test_nested_directory_without_its_own_git_does_not_inherit_ancestor_head(self) -> None:
        # Build this fixture's own throwaway repo with a hermetic
        # environment: if this test happens to run under a git hook (e.g.
        # this project's own pre-push hook invoked from a linked worktree),
        # ambient GIT_DIR/GIT_WORK_TREE would otherwise redirect these git
        # commands at *that* repository instead of the fresh one below.
        env = sanitizer._hermetic_git_env()
        outer = self.root / "outer"
        outer.mkdir()
        subprocess.run(["git", "init", "--quiet"], cwd=outer, env=env, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "test@example.invalid"], cwd=outer, env=env, check=True, capture_output=True,
        )
        subprocess.run(["git", "config", "user.name", "Test"], cwd=outer, env=env, check=True, capture_output=True)
        (outer / "file.txt").write_text("content\n")
        subprocess.run(["git", "add", "file.txt"], cwd=outer, env=env, check=True, capture_output=True)
        subprocess.run(["git", "commit", "--quiet", "-m", "initial"], cwd=outer, env=env, check=True, capture_output=True)
        inner = outer / "inner"
        inner.mkdir()
        script = inner / "script.py"
        script.write_text("nested\n")
        identity = sanitizer.code_identity(script, inner)
        self.assertIsNone(identity["commit"])
        self.assertIsNone(identity["commit_dirty"])


class BuildRunPayloadTest(unittest.TestCase):
    FAKE_FINGERPRINT = {
        "code": {"commit": "abc123", "commit_dirty": False, "build_digest_sha256": "deadbeef"},
        "denylist_sha256": "aaa",
        "project_metadata_sha256": None,
        "config_sha256": "bbb",
        "allowlist_sha256": "ccc",
        "lexicon_sha256": {"boilerplate.json": "ddd"},
    }

    def test_all_pass_carries_fingerprint_and_bumps_schema(self) -> None:
        reports = [{"release_status": sanitizer.RELEASE_STATUS_AUTOMATED_PASS}]
        payload = sanitizer.build_run_payload(reports, self.FAKE_FINGERPRINT, ner_enabled=False)
        self.assertEqual(payload["fingerprint"], self.FAKE_FINGERPRINT)
        self.assertEqual(payload["schema_version"], 4)
        self.assertTrue(payload["all_automated_checks_pass"])
        self.assertEqual(payload["release_status"], sanitizer.RELEASE_STATUS_REVIEW_REQUIRED)
        self.assertFalse(any("NER" in note for note in payload["notes"]))

    def test_a_failing_document_fails_the_run(self) -> None:
        reports = [
            {"release_status": sanitizer.RELEASE_STATUS_AUTOMATED_PASS},
            {"release_status": sanitizer.RELEASE_STATUS_FAIL},
        ]
        payload = sanitizer.build_run_payload(reports, self.FAKE_FINGERPRINT, ner_enabled=False)
        self.assertFalse(payload["all_automated_checks_pass"])
        self.assertEqual(payload["release_status"], sanitizer.RELEASE_STATUS_FAIL)

    def test_ner_enabled_appends_a_note(self) -> None:
        reports = [{"release_status": sanitizer.RELEASE_STATUS_AUTOMATED_PASS}]
        payload = sanitizer.build_run_payload(reports, self.FAKE_FINGERPRINT, ner_enabled=True)
        self.assertTrue(any("NER" in note for note in payload["notes"]))

    def test_truncated_residuals_add_an_incompleteness_note(self) -> None:
        reports = [{
            "document_id": "sanitized_document_01",
            "release_status": sanitizer.RELEASE_STATUS_AUTOMATED_PASS,
            "residuals_truncated": 1,
        }]
        payload = sanitizer.build_run_payload(reports, self.FAKE_FINGERPRINT, ner_enabled=False)
        # Still an internal AUTOMATED_PASS/REVIEW_REQUIRED outcome today (no
        # code path here ever supplies a completed review), but the report
        # must already say why this run could never become RELEASED as-is.
        self.assertEqual(payload["release_status"], sanitizer.RELEASE_STATUS_REVIEW_REQUIRED)
        self.assertTrue(any("truncat" in note.casefold() for note in payload["notes"]))

    def test_truncated_ner_findings_add_an_incompleteness_note(self) -> None:
        reports = [{
            "document_id": "sanitized_document_01",
            "release_status": sanitizer.RELEASE_STATUS_AUTOMATED_PASS,
            "ner_review": {"findings_truncated": 2},
        }]
        payload = sanitizer.build_run_payload(reports, self.FAKE_FINGERPRINT, ner_enabled=True)
        self.assertTrue(any("truncat" in note.casefold() for note in payload["notes"]))

    def test_incomplete_intake_without_waiver_adds_a_note(self) -> None:
        reports = [{"document_id": "sanitized_document_01", "release_status": sanitizer.RELEASE_STATUS_AUTOMATED_PASS}]
        payload = sanitizer.build_run_payload(
            reports, self.FAKE_FINGERPRINT, ner_enabled=False,
            intake_status={"project_metadata_supplied": False, "empty_fields": ["owner"], "waiver": None},
        )
        self.assertTrue(any("intake" in note.casefold() for note in payload["notes"]))

    def test_incomplete_intake_with_waiver_adds_no_note(self) -> None:
        reports = [{"document_id": "sanitized_document_01", "release_status": sanitizer.RELEASE_STATUS_AUTOMATED_PASS}]
        payload = sanitizer.build_run_payload(
            reports, self.FAKE_FINGERPRINT, ner_enabled=False,
            intake_status={
                "project_metadata_supplied": False, "empty_fields": ["owner"],
                "waiver": "no PM system record for this small project",
            },
        )
        self.assertFalse(any("intake" in note.casefold() for note in payload["notes"]))

    def test_no_truncation_adds_no_incompleteness_note(self) -> None:
        reports = [{
            "document_id": "sanitized_document_01",
            "release_status": sanitizer.RELEASE_STATUS_AUTOMATED_PASS,
            "residuals_truncated": 0,
            "ner_review": {"findings_truncated": 0},
        }]
        payload = sanitizer.build_run_payload(reports, self.FAKE_FINGERPRINT, ner_enabled=True)
        self.assertFalse(any("truncat" in note.casefold() for note in payload["notes"]))


class FingerprintCliWiringTest(unittest.TestCase):
    """main() actually computes and writes the fingerprint (catches a wrong
    args.* passed through, or a Path(__file__) misuse the pure-function tests
    above can't see)."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="fingerprint_cli_test_")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

        self.source = self.root / "source.pdf"
        pdf = canvas.Canvas(str(self.source), pagesize=letter)
        pdf.setFont("Helvetica", 12)
        pdf.drawString(45, 700, "OWNER: Fictional Owner Holdings")
        pdf.drawString(45, 670, "Ordinary technical text remains searchable")
        pdf.save()

        self.denylist_path = self.root / "denylist.json"
        self.denylist_path.write_text(json.dumps({"identifiers": ["Fictional Owner Holdings"]}))
        self.config_path = self.root / "config.json"
        self.config_path.write_text(json.dumps({
            "detect_barcodes": False,
            "redact_repeated_margin_images": False,
            "min_text_chars": 5,
            "progress_every_pages": 0,
        }))
        self.allowlist_path = self.root / "allowlist.json"
        self.allowlist_path.write_text(json.dumps({"entries": []}))
        self.lexicon_dir = self.root / "lexicons"
        self.lexicon_dir.mkdir()
        for name in sanitizer.LEXICON_FILENAMES:
            (self.lexicon_dir / name).write_text("{}")

    def run_main(self) -> tuple[Path, dict]:
        output_root = self.root / "output"
        before = set(output_root.iterdir()) if output_root.exists() else set()
        argv = [
            str(self.source),
            "--output-dir", str(output_root),
            "--config", str(self.config_path),
            "--denylist", str(self.denylist_path),
            "--lexicons", str(self.lexicon_dir),
            "--allowlist", str(self.allowlist_path),
        ]
        exit_code = sanitizer.main(argv)
        self.assertEqual(exit_code, 0)
        created = set(output_root.iterdir()) - before
        self.assertEqual(len(created), 1)
        run_dir = created.pop()
        return run_dir, json.loads((run_dir / "report.json").read_text())

    def test_two_runs_yield_identical_fingerprint(self) -> None:
        first_dir, first = self.run_main()
        second_dir, second = self.run_main()
        self.assertNotEqual(first_dir, second_dir)
        self.assertEqual(first["fingerprint"], second["fingerprint"])
        self.assertEqual(
            first["fingerprint"]["denylist_sha256"],
            hashlib.sha256(self.denylist_path.read_bytes()).hexdigest(),
        )
        self.assertEqual(
            first["fingerprint"]["config_sha256"],
            hashlib.sha256(self.config_path.read_bytes()).hexdigest(),
        )
        self.assertIsNone(first["fingerprint"]["project_metadata_sha256"])
class RasterVectorParityTest(unittest.TestCase):
    """The same suppressible false positive and the same genuine identifier
    must reach the same verdict whether the page is vector text or a
    full-page scan — proving ocr_detection_boxes() now enforces the same
    lexicon-suppression policy as line_detections(), not a stricter/blinder
    one. "3 CIR" is caught by the street_address direct pattern (its "Cir"
    abbreviation) on both paths, then must be suppressed by the
    panel_circuit_reference structural rule on both paths too."""

    LINES = ("OWNER: Fictional Owner Holdings", "3 CIR")

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.settings = sanitizer.Settings(
            ocr_dpi=220, barcode_dpi=120, progress_every_pages=0,
            detect_barcodes=False, redact_repeated_margin_images=False,
        )
        self.lex = sanitizer.load_lexicons(REPO_LEXICONS, REPO_ALLOWLIST)

    def run_on(self, source: Path) -> tuple[dict, str]:
        destination = self.root / f"{source.stem}_out.pdf"
        report = sanitizer.sanitize_document(
            source, destination, source.stem, FAKE_TERMS, self.settings, self.root, os.urandom(32),
            lexicons=self.lex,
        )
        output = fitz.open(destination)
        text = output[0].get_text("text")
        output.close()
        return report, text

    def test_vector_and_scanned_pages_reach_the_same_verdict(self) -> None:
        vector_source = self.root / "vector.pdf"
        pdf = canvas.Canvas(str(vector_source), pagesize=letter)
        pdf.setFont("Helvetica", 12)
        y = 700
        for line in self.LINES:
            pdf.drawString(45, y, line)
            y -= 24
        pdf.showPage()
        pdf.save()

        scanned_source = self.root / "scanned.pdf"
        create_scanned_pdf_pages(scanned_source, [list(self.LINES)])

        vector_report, vector_text = self.run_on(vector_source)
        scanned_report, scanned_text = self.run_on(scanned_source)

        for report in (vector_report, scanned_report):
            self.assertEqual(report["release_status"], sanitizer.RELEASE_STATUS_AUTOMATED_PASS)
        for text in (vector_text, scanned_text):
            self.assertIn("3 CIR", text)
            self.assertNotIn("Fictional Owner Holdings", text)
        self.assertEqual(scanned_report["rasterized_pages"], [1])


class RasterClassificationTest(unittest.TestCase):
    """Direct tests of page_needs_raster_pass(), the min_text_chars
    replacement — no PDF needed."""

    def test_low_text_and_no_image_needs_raster(self) -> None:
        settings = sanitizer.Settings()
        self.assertTrue(sanitizer.page_needs_raster_pass(5, 0.0, settings))

    def test_ample_text_and_small_image_stays_vector(self) -> None:
        settings = sanitizer.Settings()
        self.assertFalse(sanitizer.page_needs_raster_pass(500, 0.01, settings))

    def test_ample_text_and_large_image_needs_raster(self) -> None:
        settings = sanitizer.Settings()
        self.assertTrue(sanitizer.page_needs_raster_pass(500, 0.02, settings))
        self.assertTrue(sanitizer.page_needs_raster_pass(500, 0.5, settings))

    def test_min_text_chars_field_no_longer_exists(self) -> None:
        with self.assertRaises(TypeError):
            sanitizer.Settings(min_text_chars=20)


class MixedPageRasterTest(unittest.TestCase):
    """A page with a substantial vector-text body AND a large embedded image
    is a mixed page: the image content must be inspected too, not skipped
    because the vector text alone clears min_vector_text_chars."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.settings = sanitizer.Settings(
            ocr_dpi=220, barcode_dpi=120, progress_every_pages=0,
            detect_barcodes=False, redact_repeated_margin_images=False,
        )
        self.lex = sanitizer.load_lexicons(REPO_LEXICONS, REPO_ALLOWLIST)

    def test_embedded_image_content_is_redacted_on_an_otherwise_vector_page(self) -> None:
        source = self.root / "mixed.pdf"
        doc = fitz.open()
        page = doc.new_page(width=letter[0], height=letter[1])
        page.insert_text((45, 700), "TECHNICAL: ASTM A36 structural steel; 2x6 studs at 16 inches O.C.")
        stamp = render_stamp_image(self.root / "stamp", ["Fictional Owner Holdings"])
        # 300x260pt on a letter page (612x792pt) is ~16% of page area, well
        # above the 2% raster_image_area_ratio default.
        page.insert_image(fitz.Rect(150, 300, 450, 560), stream=stamp)
        doc.save(source)
        doc.close()

        destination = self.root / "out.pdf"
        report = sanitizer.sanitize_document(
            source, destination, "doc", FAKE_TERMS, self.settings, self.root, os.urandom(32),
            lexicons=self.lex,
        )
        self.assertEqual(report["release_status"], sanitizer.RELEASE_STATUS_AUTOMATED_PASS)
        self.assertEqual(report["rasterized_pages"], [1])
        output = fitz.open(destination)
        text = output[0].get_text("text")
        output.close()
        self.assertNotIn("Fictional Owner Holdings", text)
        self.assertIn("ASTM A36", text)
        raw = destination.read_bytes().lower()
        self.assertNotIn(b"fictional owner holdings", raw)


class RasterFailureContainmentTest(unittest.TestCase):
    """A suppressible false positive discovered only by the raster path's
    post-redaction safety check must fail just that page, not abort the
    whole run. Every other raster failure mode (missing OCR executable,
    tesseract/ghostscript timeouts) is covered by the three pre-existing
    tests in SanitizerTests and is not re-tested here."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.settings = sanitizer.Settings(
            ocr_dpi=220, barcode_dpi=120, progress_every_pages=0,
            detect_barcodes=False, redact_repeated_margin_images=False,
        )

    def test_only_the_affected_page_fails_and_the_run_does_not_abort(self) -> None:
        source = self.root / "two_page_scan.pdf"
        create_scanned_pdf_pages(source, [
            ["OWNER: Fictional Owner Holdings", "TECHNICAL: gypsum board note"],
            ["TECHNICAL: page two rebar note"],
        ])
        real_ocr_detection_boxes = sanitizer.ocr_detection_boxes
        residual_checks = {"count": 0}

        def fake_ocr_detection_boxes(
            words, denylist, lexicons=None, scale=1.0,
            suppressed_counts=None, suppressed_categories=None,
        ):
            result = real_ocr_detection_boxes(
                words, denylist, lexicons, scale, suppressed_counts, suppressed_categories,
            )
            # The pre-redaction detection call always passes the document's
            # suppression counters; only the post-redaction residual-check
            # call (raster_page_pdf's second ocr_detection_boxes call)
            # leaves both at their None default — the deterministic seam
            # that distinguishes the two calls without touching production
            # code.
            is_residual_check = suppressed_counts is None and suppressed_categories is None
            if is_residual_check:
                residual_checks["count"] += 1
                if residual_checks["count"] == 2:  # page 2's residual check
                    return [((0, 0, 10, 10), "labelled_identifier")]
            return result

        with mock.patch.object(sanitizer, "ocr_detection_boxes", side_effect=fake_ocr_detection_boxes):
            destination = self.root / "out.pdf"
            report = sanitizer.sanitize_document(
                source, destination, "doc", FAKE_TERMS, self.settings, self.root, os.urandom(32),
            )

        self.assertEqual(
            report["raster_page_failures"],
            [{"page": 2, "reason": "residual identifier detected after raster redaction"}],
        )
        self.assertFalse(report["checks"]["raster_page_verification"])
        self.assertNotEqual(report["release_status"], sanitizer.RELEASE_STATUS_AUTOMATED_PASS)
        output = fitz.open(destination)
        page1_text = output[0].get_text("text")
        output.close()
        self.assertNotIn("Fictional Owner Holdings", page1_text)
        self.assertIn("gypsum", page1_text)


class ArchitectOfRecordTest(unittest.TestCase):
    """CONTEXT.md resolves architect-of-record disclosure as sensitive by
    default: the firm's name and location must be redacted like any other
    identifying detail, not preserved as an assumed-intentional disclosure.
    This proves the *generic* label-following mechanism catches it — not a
    per-run denylist entry, and not the same-line "Architect: <firm>" form
    the vector path already handled before this ticket."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.settings = sanitizer.Settings(
            ocr_dpi=220, barcode_dpi=120, progress_every_pages=0,
            detect_barcodes=False, redact_repeated_margin_images=False,
        )

    def test_architect_of_record_stamp_is_redacted_without_a_denylist_entry(self) -> None:
        source = self.root / "stamped.pdf"
        doc = fitz.open()
        page = doc.new_page(width=letter[0], height=letter[1])
        page.insert_text((45, 700), "TECHNICAL: ASTM A36 structural steel")
        stamp = render_stamp_image(self.root / "stamp", [
            "ARCHITECT OF RECORD",
            "Fakename Architecture Group",
            # A non-real state abbreviation so the city_state_zip direct
            # pattern doesn't independently fire — isolating the proof to
            # the label-following mechanism, not a second detector.
            "Someplace, ZZ 00000",
        ])
        # 300x260pt on a letter page is ~16% of page area, well above the 2%
        # raster_image_area_ratio default.
        page.insert_image(fitz.Rect(150, 300, 450, 560), stream=stamp)
        doc.save(source)
        doc.close()

        destination = self.root / "out.pdf"
        lex = sanitizer.load_lexicons(REPO_LEXICONS, REPO_ALLOWLIST)
        report = sanitizer.sanitize_document(
            source, destination, "doc", FAKE_TERMS, self.settings, self.root, os.urandom(32),
            lexicons=lex,
        )
        self.assertIn(1, report["rasterized_pages"])
        self.assertEqual(report["release_status"], sanitizer.RELEASE_STATUS_AUTOMATED_PASS)
        output = fitz.open(destination)
        text = output[0].get_text("text")
        output.close()
        folded = text.casefold()
        self.assertNotIn("fakename architecture group", folded)
        self.assertNotIn("someplace", folded)
        self.assertIn("ASTM A36", text)
        raw = destination.read_bytes().lower()
        self.assertNotIn(b"fakename architecture group", raw)
        self.assertNotIn(b"someplace", raw)
class AtomicRunPackagingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="atomic_run_test_")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "source.pdf"
        self.source.write_bytes(b"synthetic source")
        self.config = self.root / "config.json"
        self.config.write_text("{}")
        self.denylist = self.root / "denylist.json"
        self.denylist.write_text(json.dumps({"identifiers": ["Fictional Owner Holdings"]}))
        self.allowlist = self.root / "allowlist.json"
        self.allowlist.write_text("{}")
        self.lexicons = self.root / "lexicons"
        self.lexicons.mkdir()
        for name in sanitizer.LEXICON_FILENAMES:
            (self.lexicons / name).write_text("{}")
        self.output_root = self.root / "runs"
        self.temp_root = self.root / "tmp"
        self.temp_root.mkdir()

    @staticmethod
    def fake_sanitize(source, destination, document_id, denylist, settings, temp_root, run_key,
                      ner_detector=None, lexicons=None):
        destination.write_bytes(b"synthetic sanitized pdf")
        return {
            "document_id": document_id,
            "source_sha256": sanitizer.sha256_file(source),
            "output_sha256": sanitizer.sha256_file(destination),
            "pages": 2,
            "redaction_counts": {"denylist": 1},
            "post_flatten_redactions": {},
            "rasterized_pages": [2],
            "checks": {"rendered_page_ocr": True},
            "rendered_ocr_verification": {
                "profile_by_page_type": {
                    "searchable": {"pages": 1, "wall_seconds": 0.1, "peak_rss_bytes": 123},
                },
            },
            "release_status": sanitizer.RELEASE_STATUS_AUTOMATED_PASS,
        }

    def run_once(self) -> tuple[Path, dict]:
        with (
            mock.patch.object(sanitizer, "sanitize_document", side_effect=self.fake_sanitize),
            mock.patch.object(sanitizer, "runtime_versions", return_value={"python": "test"}),
        ):
            return sanitizer.orchestrate_run(
                sources=[self.source], output_root=self.output_root, output_index_start=1,
                denylist={"Fictional Owner Holdings"}, settings=sanitizer.Settings(),
                temp_root=self.temp_root, denylist_path=self.denylist,
                project_metadata_path=None, config_path=self.config,
                allowlist_path=self.allowlist, lexicon_dir=self.lexicons,
                lexicons=None, ner_detector=None,
            )

    def test_two_runs_are_distinct_complete_atomic_packages(self) -> None:
        real_replace = os.replace
        observed: list[tuple[Path, Path]] = []

        def asserting_replace(source, destination):
            source_path, destination_path = Path(source), Path(destination)
            self.assertFalse(destination_path.exists())
            for required in ("sanitized_document_01.pdf", "report.json", "manifest.json", "review-summary.md"):
                self.assertTrue((source_path / required).is_file())
            observed.append((source_path, destination_path))
            real_replace(source, destination)

        with mock.patch.object(sanitizer.os, "replace", side_effect=asserting_replace):
            first, first_payload = self.run_once()
            second, second_payload = self.run_once()
        self.assertNotEqual(first, second)
        self.assertEqual(len(observed), 2)
        self.assertEqual(first_payload["release_status"], sanitizer.RELEASE_STATUS_REVIEW_REQUIRED)
        self.assertEqual(second_payload["release_status"], sanitizer.RELEASE_STATUS_REVIEW_REQUIRED)
        self.assertFalse(any(path.name.startswith(".") for path in self.output_root.iterdir()))
        manifest = json.loads((first / "manifest.json").read_text())
        self.assertEqual(manifest["run_id"], first.name)
        self.assertIn("runtime_versions", manifest)
        self.assertEqual(manifest["review"], {
            "status": "not_started", "reviewer": None, "completed_at": None,
        })

    def test_run_key_is_never_written_to_disk(self) -> None:
        known_key = b"\x01" * 32
        with (
            mock.patch.object(sanitizer, "sanitize_document", side_effect=self.fake_sanitize),
            mock.patch.object(sanitizer, "runtime_versions", return_value={"python": "test"}),
            mock.patch.object(sanitizer.secrets, "token_bytes", return_value=known_key),
        ):
            run_dir, _ = sanitizer.orchestrate_run(
                sources=[self.source], output_root=self.output_root, output_index_start=1,
                denylist={"Fictional Owner Holdings"}, settings=sanitizer.Settings(),
                temp_root=self.temp_root, denylist_path=self.denylist,
                project_metadata_path=None, config_path=self.config,
                allowlist_path=self.allowlist, lexicon_dir=self.lexicons,
                lexicons=None, ner_detector=None,
            )
        for name in ("report.json", "manifest.json", "review-summary.md"):
            contents = (run_dir / name).read_bytes()
            self.assertNotIn(known_key, contents)
            self.assertNotIn(known_key.hex().encode(), contents)

    def test_failure_still_publishes_a_failure_record(self) -> None:
        with (
            mock.patch.object(sanitizer, "sanitize_document", side_effect=RuntimeError("boom")),
            mock.patch.object(sanitizer, "runtime_versions", return_value={"python": "test"}),
        ):
            run_dir, payload = sanitizer.orchestrate_run(
                sources=[self.source], output_root=self.output_root, output_index_start=1,
                denylist={"Fictional Owner Holdings"}, settings=sanitizer.Settings(),
                temp_root=self.temp_root, denylist_path=self.denylist,
                project_metadata_path=None, config_path=self.config,
                allowlist_path=self.allowlist, lexicon_dir=self.lexicons,
                lexicons=None, ner_detector=None,
            )
        self.assertTrue((run_dir / "manifest.json").is_file())
        self.assertEqual(payload["release_status"], sanitizer.RELEASE_STATUS_FAIL)
        self.assertEqual(payload["documents"][0]["release_status"], sanitizer.RELEASE_STATUS_FAIL)
        self.assertFalse(payload["documents"][0]["checks"]["processing_completed"])

    def test_manifest_records_full_empty_field_list_with_no_project_metadata(self) -> None:
        with (
            mock.patch.object(sanitizer, "sanitize_document", side_effect=self.fake_sanitize),
            mock.patch.object(sanitizer, "runtime_versions", return_value={"python": "test"}),
        ):
            run_dir, _ = sanitizer.orchestrate_run(
                sources=[self.source], output_root=self.output_root, output_index_start=1,
                denylist={"Fictional Owner Holdings"}, settings=sanitizer.Settings(),
                temp_root=self.temp_root, denylist_path=self.denylist,
                project_metadata_path=None, config_path=self.config,
                allowlist_path=self.allowlist, lexicon_dir=self.lexicons,
                lexicons=None, ner_detector=None, intake_waiver="no PM record for this pilot run",
            )
        manifest = json.loads((run_dir / "manifest.json").read_text())
        self.assertEqual(manifest["intake"], {
            "project_metadata_supplied": False,
            "empty_fields": list(sanitizer.PROJECT_METADATA_FIELDS),
            "waiver": "no PM record for this pilot run",
        })

    def test_manifest_records_exactly_the_blank_project_metadata_fields(self) -> None:
        project_metadata = self.root / "project.json"
        project_metadata.write_text(json.dumps({
            "project_name": "Example Confidential Project",
            "project_number": "ZX-FAKE-2048",
        }))
        with (
            mock.patch.object(sanitizer, "sanitize_document", side_effect=self.fake_sanitize),
            mock.patch.object(sanitizer, "runtime_versions", return_value={"python": "test"}),
        ):
            run_dir, _ = sanitizer.orchestrate_run(
                sources=[self.source], output_root=self.output_root, output_index_start=1,
                denylist={"Fictional Owner Holdings"}, settings=sanitizer.Settings(),
                temp_root=self.temp_root, denylist_path=self.denylist,
                project_metadata_path=project_metadata, config_path=self.config,
                allowlist_path=self.allowlist, lexicon_dir=self.lexicons,
                lexicons=None, ner_detector=None,
            )
        manifest = json.loads((run_dir / "manifest.json").read_text())
        self.assertTrue(manifest["intake"]["project_metadata_supplied"])
        self.assertIsNone(manifest["intake"]["waiver"])
        self.assertEqual(sorted(manifest["intake"]["empty_fields"]), sorted([
            field for field in sanitizer.PROJECT_METADATA_FIELDS
            if field not in ("project_name", "project_number")
        ]))
        # The hash of the supplied project-metadata file is always recorded.
        self.assertEqual(
            manifest["fingerprint"]["project_metadata_sha256"],
            hashlib.sha256(project_metadata.read_bytes()).hexdigest(),
        )


class RenderedPageOcrVerifierTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="render_verify_test_")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.settings = sanitizer.Settings(
            detect_barcodes=False, redact_repeated_margin_images=False,
            progress_every_pages=0,
        )

    def make_pdf(self, pages: int = 1) -> Path:
        path = self.root / "output.pdf"
        pdf = canvas.Canvas(str(path), pagesize=letter)
        for _ in range(pages):
            pdf.drawString(45, 700, "TECHNICAL: ASTM A36 structural steel")
            pdf.showPage()
        pdf.save()
        doc = fitz.open(path)
        doc.set_metadata({})
        cleaned = path.with_suffix(".cleaned.pdf")
        doc.save(cleaned)
        doc.close()
        cleaned.replace(path)
        return path

    @staticmethod
    def words(*values: str) -> list[sanitizer.OcrWord]:
        return [
            sanitizer.OcrWord(value, 10 + index * 90, 10, 80, 15, 1, 1, 1)
            for index, value in enumerate(values)
        ]

    def verify(self, path: Path, mocked_words) -> dict:
        with mock.patch.object(sanitizer, "run_tesseract_tsv", side_effect=mocked_words):
            return sanitizer.verify_output(
                path, [(612.0, 792.0)] * len(fitz.open(path)),
                sanitizer.DenylistMatcher({"Fictional Owner Holdings"}), set(),
                self.root / "triage", os.urandom(32), settings=self.settings,
                lexicons=sanitizer.load_lexicons(REPO_LEXICONS, REPO_ALLOWLIST),
            )

    def test_pixel_only_identifier_blocks_automated_pass(self) -> None:
        path = self.make_pdf()
        report = self.verify(path, [self.words("Fictional", "Owner", "Holdings")])
        self.assertTrue(report["checks"]["denylist_scan"])
        self.assertFalse(report["checks"]["rendered_page_ocr"])
        self.assertEqual(report["release_status"], sanitizer.RELEASE_STATUS_FAIL)
        page = report["rendered_ocr_verification"]["pages"][0]
        self.assertEqual(page["status"], "unresolved")
        self.assertEqual(page["unresolved_match_counts"], {"denylist": 1})

    def test_suppressed_rendered_match_stays_clean(self) -> None:
        path = self.make_pdf()
        report = self.verify(path, [self.words("3", "CIR")])
        self.assertTrue(report["checks"]["rendered_page_ocr"])
        self.assertEqual(report["release_status"], sanitizer.RELEASE_STATUS_AUTOMATED_PASS)
        page = report["rendered_ocr_verification"]["pages"][0]
        self.assertEqual(page["status"], "clean")
        self.assertEqual(page["suppressed_by_rule"], {"structural": 1})

    def test_every_page_has_a_rendered_verification_record(self) -> None:
        path = self.make_pdf(pages=2)
        report = self.verify(path, [[], []])
        pages = report["rendered_ocr_verification"]["pages"]
        self.assertEqual([page["page"] for page in pages], [1, 2])
        self.assertTrue(all("elapsed_seconds" in page and "process_peak_rss_bytes" in page for page in pages))

    def test_remediation_redacts_the_matched_region_and_reports_its_category(self) -> None:
        path = self.make_pdf()
        with mock.patch.object(
            sanitizer, "run_tesseract_tsv",
            side_effect=[self.words("Fictional", "Owner", "Holdings")],
        ):
            remediated = sanitizer.remediate_rendered_output(
                path, {1},
                sanitizer.DenylistMatcher({"Fictional Owner Holdings"}),
                sanitizer.load_lexicons(REPO_LEXICONS, REPO_ALLOWLIST),
                self.settings,
            )
        self.assertEqual(remediated, {1: ["denylist"]})
        doc = fitz.open(path)
        self.assertNotIn("Fictional Owner Holdings", doc[0].get_text("text"))
        doc.close()

    def test_remediation_is_a_noop_when_no_pages_are_unresolved(self) -> None:
        path = self.make_pdf()
        original_bytes = path.read_bytes()
        remediated = sanitizer.remediate_rendered_output(
            path, set(),
            sanitizer.DenylistMatcher({"Fictional Owner Holdings"}), None, self.settings,
        )
        self.assertEqual(remediated, {})
        self.assertEqual(path.read_bytes(), original_bytes)


class RenderedOcrNoiseRegressionTest(unittest.TestCase):
    TSV_WITH_LITERAL_QUOTE = (
        "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
        "5\t1\t1\t1\t1\t1\t10\t10\t5\t10\t90\t\"\n"
        "5\t1\t2\t1\t1\t1\t20\t30\t30\t10\t90\tNEXT\n"
    ).encode()

    def test_label_lookahead_is_aligned_and_not_limited_by_global_line_order(self) -> None:
        words = [
            sanitizer.OcrWord("JOB", 100, 10, 40, 15, 1, 1, 1),
            sanitizer.OcrWord("NUMBER", 145, 10, 70, 15, 1, 1, 1),
            sanitizer.OcrWord("SPECIFICATIONS", 500, 20, 160, 15, 2, 1, 1),
            sanitizer.OcrWord("EXH", 700, 30, 40, 15, 3, 1, 1),
            sanitizer.OcrWord("UNRELATED", 900, 35, 100, 15, 4, 1, 1),
            sanitizer.OcrWord("21109", 105, 45, 70, 15, 5, 1, 1),
        ]
        detections = sanitizer.ocr_detection_boxes(
            words, sanitizer.DenylistMatcher({"Never Matches"}), scale=1.0,
        )
        self.assertEqual([category for _box, category in detections], ["labelled_identifier"])
        self.assertLess(detections[0][0][0], 200)

    def test_short_ocr_at_fragments_are_not_emails(self) -> None:
        pattern = sanitizer.DIRECT_PATTERNS["email"]
        self.assertIsNone(pattern.search("a@c c@k KE@c"))
        self.assertIsNotNone(pattern.search("contact@example"))

    def test_dimension_and_lothil_noise_are_not_identifiers(self) -> None:
        self.assertIsNone(sanitizer.DIRECT_PATTERNS["parcel_or_lot"].search("Lothil"))
        lexicons = sanitizer.load_lexicons(REPO_LEXICONS, REPO_ALLOWLIST)
        reason = sanitizer.candidate_suppression(
            "street_address", "9 ST", lexicons, context='6"9 ST',
        )
        self.assertEqual(reason, "structural:feet_inches_dimension")

    def test_pipeline_tsv_parser_treats_ocr_quote_as_literal_text(self) -> None:
        completed = subprocess.CompletedProcess([], 0, stdout=self.TSV_WITH_LITERAL_QUOTE)
        with (
            tempfile.TemporaryDirectory() as temp_name,
            mock.patch.object(sanitizer.shutil, "which", return_value="/usr/bin/tesseract"),
            mock.patch.object(sanitizer.subprocess, "run", return_value=completed),
        ):
            words = sanitizer.run_tesseract_tsv(
                Image.new("RGB", (50, 50)), sanitizer.Settings(), Path(temp_name),
            )
        self.assertEqual([word.text for word in words], ['"', "NEXT"])

    def test_independent_tsv_parser_treats_ocr_quote_as_literal_text(self) -> None:
        completed = subprocess.CompletedProcess([], 0, stdout=self.TSV_WITH_LITERAL_QUOTE)
        with tempfile.TemporaryDirectory() as temp_name:
            pdf_path = Path(temp_name) / "blank.pdf"
            pdf = canvas.Canvas(str(pdf_path), pagesize=letter)
            pdf.showPage()
            pdf.save()
            document = fitz.open(pdf_path)
            with (
                mock.patch.object(verify_existing.shutil, "which", return_value="/usr/bin/tesseract"),
                mock.patch.object(verify_existing.subprocess, "run", return_value=completed),
            ):
                lines = verify_existing.independent_rendered_lines(
                    document[0], "tesseract", 300, Path(temp_name),
                )
            document.close()
        self.assertEqual([line.text for line in lines], ['"', "NEXT"])


class VerifyExistingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="verify_existing_test_")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.tools = self.root / "tools"
        self.tools.mkdir()
        self.script = self.tools / "anonymize_construction_pdfs.py"
        self.script.write_text("# synthetic sanitizer\n")
        self.config = self.root / "config.json"
        self.config.write_text("{}")
        self.denylist = self.root / "denylist.json"
        self.denylist.write_text(json.dumps({"identifiers": ["Fictional Owner Holdings"]}))
        self.allowlist = self.root / "allowlist.json"
        self.allowlist.write_text(json.dumps({"entries": []}))
        self.lexicons = self.root / "lexicons"
        self.lexicons.mkdir()
        (self.lexicons / "contract_defined_terms.json").write_text(json.dumps({"terms": []}))
        (self.lexicons / "structural_patterns.json").write_text(json.dumps({"suppress_if_span_matches": []}))
        (self.lexicons / "boilerplate.json").write_text(json.dumps({"phrases": []}))
        self.run_dir = self.root / "run"
        self.run_dir.mkdir()
        self.pdf = self.run_dir / "sanitized_document_01.pdf"
        pdf = canvas.Canvas(str(self.pdf), pagesize=letter)
        pdf.drawString(45, 700, "TECHNICAL: ASTM A36 structural steel")
        pdf.save()
        fingerprint = verify_existing.current_fingerprint(
            sanitizer_script=self.script, repo_root=self.root,
            denylist=self.denylist, project_metadata=None, config=self.config,
            allowlist=self.allowlist, lexicons=self.lexicons,
        )
        manifest = {
            "run_id": "test-run", "fingerprint": fingerprint,
            "documents": [{
                "document_id": "sanitized_document_01",
                "output": {"path": self.pdf.name, "sha256": verify_existing.sha256_file(self.pdf)},
            }],
        }
        (self.run_dir / "manifest.json").write_text(json.dumps(manifest))

    def verify(self) -> dict:
        with mock.patch.object(
            verify_existing, "independent_rendered_lines",
            return_value=[verify_existing.IndependentLine(
                "TECHNICAL: ASTM A36 structural steel", 0, 0, 500, 20,
            )],
        ):
            return verify_existing.verify_run(
                self.run_dir, denylist=self.denylist, project_metadata=None,
                config=self.config, lexicons=self.lexicons, allowlist=self.allowlist,
                sanitizer_script=self.script,
            )

    def test_matching_fingerprint_is_current(self) -> None:
        result = self.verify()
        self.assertEqual(result["fingerprint_status"], "current")
        self.assertEqual(result["release_status"], "AUTOMATED_PASS")
        self.assertEqual(result["documents"][0]["artifact_status"], "current")

    def test_mutated_config_is_stale_without_manual_hash_comparison(self) -> None:
        self.config.write_text(json.dumps({"verification_ocr_dpi": 301}))
        result = self.verify()
        self.assertEqual(result["fingerprint_status"], "stale")
        self.assertEqual(result["release_status"], "FAIL")

    def test_verifier_does_not_import_pipeline_plumbing(self) -> None:
        source = VERIFY_MODULE_PATH.read_text()
        self.assertNotRegex(
            source, r"(?m)^\s*(?:from|import)\s+anonymize_construction_pdfs\b",
        )
        self.assertIn("Independence is a safety property", source)

    def test_legacy_directory_without_manifest_is_unreleasable(self) -> None:
        legacy = self.root / "legacy-output"
        legacy.mkdir()
        (legacy / "sanitized_document_01.pdf").write_bytes(self.pdf.read_bytes())
        with self.assertRaisesRegex(ValueError, "no manifest"):
            verify_existing.verify_run(
                legacy, denylist=self.denylist, project_metadata=None,
                config=self.config, lexicons=self.lexicons,
                allowlist=self.allowlist, sanitizer_script=self.script,
            )

    def test_independent_label_geometry_ignores_table_codes(self) -> None:
        policy = verify_existing.IndependentPolicy.load(
            self.denylist, None, self.lexicons, self.allowlist,
        )
        lines = [
            verify_existing.IndependentLine("ENGINEERING", 100, 10, 190, 22),
            verify_existing.IndependentLine("190", 105, 30, 130, 42),
            verify_existing.IndependentLine('12"x6"', 105, 48, 150, 60),
            verify_existing.IndependentLine("SG5", 105, 66, 135, 78),
            verify_existing.IndependentLine("JOB NUMBER", 300, 10, 400, 22),
            verify_existing.IndependentLine("UNRELATED", 700, 30, 800, 42),
            verify_existing.IndependentLine("21109", 305, 66, 350, 78),
        ]
        self.assertEqual(policy.scan_lines(lines), {"labelled_identifier": 1})

    def test_independent_policy_suppresses_decimal_section_as_address(self) -> None:
        policy = verify_existing.IndependentPolicy.load(
            self.denylist, None, self.lexicons, self.allowlist,
        )
        line = verify_existing.IndependentLine(
            "1.5 OPERATION AND MAINTENANCE shall be tested in place before covering.",
            0, 0, 500, 20,
        )
        self.assertEqual(policy.scan_lines([line]), {})

    def test_independent_label_does_not_reach_into_distant_table_heading(self) -> None:
        policy = verify_existing.IndependentPolicy.load(
            self.denylist, None, self.lexicons, self.allowlist,
        )
        lines = [
            verify_existing.IndependentLine("PROJECT", 100, 10, 170, 22),
            verify_existing.IndependentLine("QUANTITY VOLTAGE", 105, 100, 250, 112),
        ]
        self.assertEqual(policy.scan_lines(lines), {})


class ReviewerTriageTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="reviewer_triage_test_")
        self.addCleanup(self.tmp.cleanup)
        self.run_dir = Path(self.tmp.name)
        self.decisions_path = self.run_dir / "decisions.json"

        self.doc1_crop = self.run_dir / "triage" / "sanitized_document_01" / "residual_0001_page0003_denylist.png"
        self.doc1_ner_crop = self.run_dir / "triage" / "sanitized_document_01" / "ner" / "ner_0001_page0001_person_name.png"
        self.doc2_crop = self.run_dir / "triage" / "sanitized_document_02" / "residual_0001_page0001_street_address.png"
        for crop in (self.doc1_crop, self.doc1_ner_crop, self.doc2_crop):
            crop.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (4, 4), (255, 255, 255)).save(crop)

        self.report = {
            "documents": [
                {
                    "document_id": "sanitized_document_01",
                    "residuals": [
                        {
                            "page": 3,
                            "category": "denylist",
                            "shape": "Aaaaa Aaaaa Aaaaaaaaaaa LLC",
                            "crop": str(self.doc1_crop.relative_to(self.run_dir)),
                        },
                    ],
                    "ner_review": {
                        "findings": [
                            {
                                "label": "person name",
                                "shape": "Aaaa A. Aaaaaaa",
                                "occurrences": 7,
                                "pages": [1, 4, 9],
                                "score_max": 0.91,
                                "zone": "title_block",
                                "evidence": [],
                                "crop": str(self.doc1_ner_crop.relative_to(self.run_dir)),
                            },
                        ],
                        "findings_truncated": 0,
                    },
                },
                {
                    "document_id": "sanitized_document_02",
                    "residuals": [
                        {
                            "page": 1,
                            "category": "street_address",
                            "shape": "999 Aaaaa Aa, Aaaaaaa, XX 99999",
                            "crop": str(self.doc2_crop.relative_to(self.run_dir)),
                        },
                    ],
                    "ner_review": {"findings": [], "findings_truncated": 0},
                },
            ],
        }
        (self.run_dir / "report.json").write_text(json.dumps(self.report))

        self.output_sha_1 = hashlib.sha256(b"doc1").hexdigest()
        self.output_sha_2 = hashlib.sha256(b"doc2").hexdigest()
        self.manifest = {
            "run_id": "test-run",
            "documents": [
                {
                    "document_id": "sanitized_document_01",
                    "output": {"path": "sanitized_document_01.pdf", "sha256": self.output_sha_1},
                },
                {
                    "document_id": "sanitized_document_02",
                    "output": {"path": "sanitized_document_02.pdf", "sha256": self.output_sha_2},
                },
            ],
        }
        (self.run_dir / "manifest.json").write_text(json.dumps(self.manifest))

        self.findings = reviewer_triage.load_findings(self.report)
        self.residual_finding_id = "sanitized_document_01:residual:3:denylist"
        self.ner_finding_id = "sanitized_document_01:ner:person name:Aaaa A. Aaaaaaa"
        self.doc2_finding_id = "sanitized_document_02:residual:1:street_address"

    def test_load_findings_flattens_real_report_shape(self) -> None:
        ids = {finding["id"] for finding in self.findings}
        self.assertEqual(
            ids, {self.residual_finding_id, self.ner_finding_id, self.doc2_finding_id},
        )
        residual = next(f for f in self.findings if f["id"] == self.residual_finding_id)
        self.assertEqual(residual["document_id"], "sanitized_document_01")
        self.assertEqual(residual["category"], "denylist")
        self.assertEqual(residual["pages"], [3])
        self.assertEqual(residual["crop"], str(self.doc1_crop.relative_to(self.run_dir)))
        ner = next(f for f in self.findings if f["id"] == self.ner_finding_id)
        self.assertEqual(ner["category"], "person name")
        self.assertEqual(ner["occurrences"], 7)
        self.assertEqual(ner["pages"], [1, 4, 9])

    def test_reviewer_payload_hides_internal_fields(self) -> None:
        payload = reviewer_triage.build_reviewer_payload(self.findings, {}, run_dir=self.run_dir)
        banned_keys = {"category", "label", "shape", "occurrences", "score_max", "evidence", "zone", "source"}
        allowed_keys = {"id", "plain_guess", "pages", "crop_url", "disposition", "note"}
        for item in payload:
            self.assertFalse(banned_keys & item.keys(), item)
            self.assertTrue(set(item.keys()) <= allowed_keys, item)

    def test_load_report_raises_when_manifest_is_missing(self) -> None:
        empty_dir = self.run_dir / "no_manifest_here"
        empty_dir.mkdir()
        (empty_dir / "report.json").write_text(json.dumps({"documents": []}))
        with self.assertRaises(ValueError):
            reviewer_triage.load_report(empty_dir)

    def test_plain_guess_covers_every_real_category_and_ner_label(self) -> None:
        real_categories = set(sanitizer.DIRECT_PATTERNS.keys()) | {"denylist", "labelled_identifier"}
        real_ner_labels = set(sanitizer.DEFAULT_NER_LABELS)
        missing = (real_categories | real_ner_labels) - reviewer_triage.PLAIN_GUESS.keys()
        self.assertEqual(missing, set(), f"no plain-language guess for: {missing}")

    def test_reviewer_payload_gives_plain_language_guesses(self) -> None:
        payload = reviewer_triage.build_reviewer_payload(self.findings, {}, run_dir=self.run_dir)
        by_id = {item["id"]: item for item in payload}
        self.assertEqual(
            by_id[self.residual_finding_id]["plain_guess"],
            reviewer_triage.PLAIN_GUESS["denylist"],
        )
        self.assertEqual(
            by_id[self.ner_finding_id]["plain_guess"],
            reviewer_triage.PLAIN_GUESS["person name"],
        )

    def test_reviewer_payload_crop_url_and_missing_crop_fallback(self) -> None:
        payload = reviewer_triage.build_reviewer_payload(self.findings, {}, run_dir=self.run_dir)
        by_id = {item["id"]: item for item in payload}
        self.assertEqual(by_id[self.residual_finding_id]["crop_url"], f"/crops?id={self.residual_finding_id}")
        self.doc1_crop.unlink()
        payload = reviewer_triage.build_reviewer_payload(self.findings, {}, run_dir=self.run_dir)
        by_id = {item["id"]: item for item in payload}
        self.assertIsNone(by_id[self.residual_finding_id]["crop_url"])

    def test_reviewer_payload_reflects_existing_decisions(self) -> None:
        decisions = {
            self.residual_finding_id: {"disposition": "safe", "note": "looks fine"},
        }
        payload = reviewer_triage.build_reviewer_payload(self.findings, decisions, run_dir=self.run_dir)
        by_id = {item["id"]: item for item in payload}
        self.assertEqual(by_id[self.residual_finding_id]["disposition"], "safe")
        self.assertEqual(by_id[self.residual_finding_id]["note"], "looks fine")
        self.assertNotIn("disposition", by_id[self.ner_finding_id])

    def test_record_disposition_writes_entry_keyed_to_output_hash(self) -> None:
        entry = reviewer_triage.record_disposition(
            self.residual_finding_id, "duplicate", "already in denylist", self.decisions_path,
        )
        self.assertEqual(entry["document_id"], "sanitized_document_01")
        self.assertEqual(entry["output_sha256"], self.output_sha_1)
        self.assertEqual(entry["disposition"], "duplicate")
        self.assertEqual(entry["note"], "already in denylist")
        self.assertIn("decided_at", entry)

        on_disk = json.loads(self.decisions_path.read_text())
        self.assertEqual(on_disk["run_id"], "test-run")
        self.assertEqual(on_disk["decisions"][self.residual_finding_id], entry)

    def test_record_disposition_upserts_same_finding(self) -> None:
        reviewer_triage.record_disposition(
            self.residual_finding_id, "duplicate", "first note", self.decisions_path,
        )
        second = reviewer_triage.record_disposition(
            self.residual_finding_id, "sensitive", "changed my mind", self.decisions_path,
        )
        on_disk = json.loads(self.decisions_path.read_text())
        self.assertEqual(len(on_disk["decisions"]), 1)
        self.assertEqual(on_disk["decisions"][self.residual_finding_id]["disposition"], "sensitive")
        self.assertEqual(second["note"], "changed my mind")

    def test_record_disposition_rejects_unknown_disposition(self) -> None:
        with self.assertRaises(ValueError):
            reviewer_triage.record_disposition(
                self.residual_finding_id, "not_a_real_disposition", "", self.decisions_path,
            )

    def test_record_disposition_rejects_unresolvable_document(self) -> None:
        with self.assertRaises(ValueError):
            reviewer_triage.record_disposition(
                "nonexistent_document:residual:1:denylist", "safe", "", self.decisions_path,
            )

    def test_record_disposition_keys_each_entry_to_its_own_document(self) -> None:
        reviewer_triage.record_disposition(self.residual_finding_id, "safe", "", self.decisions_path)
        entry2 = reviewer_triage.record_disposition(self.doc2_finding_id, "escalate", "", self.decisions_path)
        self.assertEqual(entry2["output_sha256"], self.output_sha_2)
        self.assertNotEqual(entry2["output_sha256"], self.output_sha_1)

    def test_promotion_preview_duplicate_and_safe_only(self) -> None:
        findings_by_id = {finding["id"]: finding for finding in self.findings}
        decisions = {
            self.residual_finding_id: {"disposition": "duplicate", "note": ""},
            self.ner_finding_id: {"disposition": "safe", "note": ""},
            self.doc2_finding_id: {"disposition": "sensitive", "note": ""},
        }
        preview = reviewer_triage.build_promotion_preview(findings_by_id, decisions)
        self.assertEqual(len(preview["denylist_additions"]), 1)
        self.assertEqual(preview["denylist_additions"][0]["shape"], "Aaaaa Aaaaa Aaaaaaaaaaa LLC")
        self.assertEqual(len(preview["lexicon_proposals"]), 1)
        self.assertEqual(preview["lexicon_proposals"][0]["shape"], "Aaaa A. Aaaaaaa")

    def test_promotion_preview_escalate_and_sensitive_produce_nothing(self) -> None:
        findings_by_id = {finding["id"]: finding for finding in self.findings}
        decisions = {
            self.residual_finding_id: {"disposition": "sensitive", "note": ""},
            self.ner_finding_id: {"disposition": "escalate", "note": ""},
        }
        preview = reviewer_triage.build_promotion_preview(findings_by_id, decisions)
        self.assertEqual(preview["denylist_additions"], [])
        self.assertEqual(preview["lexicon_proposals"], [])

    def test_reviewer_payload_never_contains_promotion_preview_data(self) -> None:
        findings_by_id = {finding["id"]: finding for finding in self.findings}
        decisions = {
            self.residual_finding_id: {"disposition": "duplicate", "note": ""},
            self.ner_finding_id: {"disposition": "safe", "note": ""},
        }
        preview = reviewer_triage.build_promotion_preview(findings_by_id, decisions)
        preview_keys = set(preview.keys())
        payload = reviewer_triage.build_reviewer_payload(self.findings, decisions, run_dir=self.run_dir)
        for item in payload:
            self.assertFalse(preview_keys & item.keys())

    def start_server(self):
        findings_by_id = {finding["id"]: finding for finding in self.findings}
        server = reviewer_triage.make_server(
            "127.0.0.1", 0, run_dir=self.run_dir, findings_by_id=findings_by_id,
            decisions_path=self.decisions_path,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        # addCleanup runs LIFO: register in reverse of the order they must
        # run (shutdown the serve_forever loop, then join the thread it was
        # running in, then close the socket) or thread.join() hangs forever
        # waiting for a thread nothing ever told to stop.
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join)
        self.addCleanup(server.shutdown)
        return server

    def test_server_get_findings_and_post_decision_round_trip(self) -> None:
        server = self.start_server()
        base = f"http://127.0.0.1:{server.server_address[1]}"

        with urllib.request.urlopen(f"{base}/api/findings") as response:
            findings_response = json.loads(response.read())
        ids = {item["id"] for item in findings_response}
        self.assertIn(self.residual_finding_id, ids)

        body = json.dumps({
            "finding_id": self.residual_finding_id, "disposition": "duplicate", "note": "seen before",
        }).encode("utf-8")
        request = urllib.request.Request(
            f"{base}/api/decisions", data=body, method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request) as response:
            self.assertEqual(response.status, 200)
            written = json.loads(response.read())
        self.assertEqual(written["disposition"], "duplicate")
        self.assertEqual(written["output_sha256"], self.output_sha_1)

        on_disk = json.loads(self.decisions_path.read_text())
        self.assertEqual(on_disk["decisions"][self.residual_finding_id]["note"], "seen before")

    def test_server_post_unknown_finding_id_is_404(self) -> None:
        server = self.start_server()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        body = json.dumps({
            "finding_id": "no_such_finding", "disposition": "safe", "note": "",
        }).encode("utf-8")
        request = urllib.request.Request(
            f"{base}/api/decisions", data=body, method="POST",
            headers={"Content-Type": "application/json"},
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(request)
        self.addCleanup(ctx.exception.close)
        self.assertEqual(ctx.exception.code, 404)

    def test_server_post_bad_disposition_is_400(self) -> None:
        server = self.start_server()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        body = json.dumps({
            "finding_id": self.residual_finding_id, "disposition": "maybe", "note": "",
        }).encode("utf-8")
        request = urllib.request.Request(
            f"{base}/api/decisions", data=body, method="POST",
            headers={"Content-Type": "application/json"},
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(request)
        self.addCleanup(ctx.exception.close)
        self.assertEqual(ctx.exception.code, 400)

    def test_server_serves_crop_image(self) -> None:
        server = self.start_server()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        with urllib.request.urlopen(f"{base}/crops?id={urllib.parse.quote(self.residual_finding_id)}") as response:
            self.assertEqual(response.headers["Content-Type"], "image/png")
            self.assertEqual(response.read(), self.doc1_crop.read_bytes())

    def test_server_ops_preview_never_reachable_from_reviewer_payload_endpoint(self) -> None:
        server = self.start_server()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        reviewer_triage.record_disposition(self.residual_finding_id, "duplicate", "", self.decisions_path)
        with urllib.request.urlopen(f"{base}/api/findings") as response:
            findings_response = json.loads(response.read())
        with urllib.request.urlopen(f"{base}/api/ops/preview") as response:
            preview_response = json.loads(response.read())
        self.assertEqual(len(preview_response["denylist_additions"]), 1)
        preview_keys = set(preview_response.keys())
        for item in findings_response:
            self.assertFalse(preview_keys & item.keys())

    def test_server_serves_reviewer_and_ops_html_pages(self) -> None:
        server = self.start_server()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        with urllib.request.urlopen(f"{base}/") as response:
            self.assertIn(b"Review flagged items", response.read())
        with urllib.request.urlopen(f"{base}/ops") as response:
            self.assertIn(b"Promotion preview", response.read())

    def test_reviewer_html_never_mentions_ops_or_promotion_terms(self) -> None:
        source = reviewer_triage.REVIEWER_HTML.read_text().lower()
        for banned in ("denylist", "lexicon", "/ops", "/api/ops"):
            self.assertNotIn(banned, source)

    def test_ops_html_has_no_disposition_controls(self) -> None:
        source = reviewer_triage.OPS_HTML.read_text().lower()
        for banned in ("sensitive", "fine to show", "already flagged", "ask someone else", "/api/decisions"):
            self.assertNotIn(banned, source)
class ResourceCeilingTest(unittest.TestCase):
    """Ticket 02: a memory/CPU/disk ceiling breach must fail closed through
    the same PageProcessingError path the Tesseract/Ghostscript timeouts use
    (SanitizerTests.test_tesseract_timeout_fails_closed_per_page and
    test_ghostscript_timeout_fails_closed_without_hanging) rather than
    crashing or hanging. Real OS-level rlimit enforcement is platform-
    inconsistent (notably unenforced on macOS), so breaches are forced here
    by mocking the polled check, not by relying on the kernel to enforce it."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="resource_ceiling_test_")
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_memory_ceiling_forces_controlled_fail(self) -> None:
        limits = sanitizer.ResourceLimits(max_memory_bytes=1)
        with mock.patch.object(sanitizer, "process_peak_rss_bytes", return_value=10**9):
            with self.assertRaises(sanitizer.PageProcessingError) as caught:
                sanitizer.check_resource_ceilings(self.root, limits)
        self.assertIn("memory", caught.exception.reason.lower())

    def test_disk_ceiling_forces_controlled_fail(self) -> None:
        (self.root / "big.bin").write_bytes(b"0" * 4096)
        limits = sanitizer.ResourceLimits(max_staging_disk_bytes=1024)
        with self.assertRaises(sanitizer.PageProcessingError) as caught:
            sanitizer.check_resource_ceilings(self.root, limits)
        self.assertIn("disk", caught.exception.reason.lower())

    def test_ceilings_pass_silently_when_within_limits(self) -> None:
        (self.root / "small.bin").write_bytes(b"0" * 16)
        limits = sanitizer.ResourceLimits(max_memory_bytes=10**12, max_staging_disk_bytes=10**9)
        sanitizer.check_resource_ceilings(self.root, limits)  # must not raise

    def test_memory_breach_during_processing_fails_closed_per_page(self) -> None:
        source = self.root / "source.pdf"
        create_searchable_pdf(source)
        destination = self.root / "sanitized_document_01.pdf"
        settings = sanitizer.Settings(
            ocr_dpi=220, barcode_dpi=72, min_vector_text_chars=20, progress_every_pages=0,
            detect_barcodes=True, redact_repeated_margin_images=False,
            resource_limits=sanitizer.ResourceLimits(max_memory_bytes=1, resource_check_every_pages=1),
        )
        with mock.patch.object(sanitizer, "process_peak_rss_bytes", return_value=10**9):
            with self.assertRaises(sanitizer.PageProcessingError) as caught:
                sanitizer.sanitize_document(
                    source, destination, "sanitized_document_01", FAKE_TERMS, settings, self.root,
                    os.urandom(32),
                )
        self.assertEqual(caught.exception.page_number, 1)
        self.assertIn("memory", caught.exception.reason.lower())

    def test_memory_breach_fails_the_run_closed_via_orchestrate_run(self) -> None:
        # The acceptance criterion asks that "the run" fail closed, not just
        # that sanitize_document raises — drive the real orchestrate_run
        # path (AtomicRunPackagingTest's fixture pattern) with a real
        # PageProcessingError from check_resource_ceilings and assert the
        # run-level FAIL shape, matching
        # AtomicRunPackagingTest.test_failure_still_publishes_a_failure_record.
        source = self.root / "source.pdf"
        source.write_bytes(b"synthetic source")
        config = self.root / "config.json"
        config.write_text("{}")
        denylist = self.root / "denylist.json"
        denylist.write_text(json.dumps({"identifiers": ["Fictional Owner Holdings"]}))
        allowlist = self.root / "allowlist.json"
        allowlist.write_text("{}")
        lexicons = self.root / "lexicons"
        lexicons.mkdir()
        for name in sanitizer.LEXICON_FILENAMES:
            (lexicons / name).write_text("{}")
        output_root = self.root / "runs"
        temp_root = self.root / "tmp"
        temp_root.mkdir()

        def breaching_sanitize(source, destination, document_id, denylist, settings, temp_root,
                                run_key, ner_detector=None, lexicons=None):
            sanitizer.check_resource_ceilings(destination.parent, settings.resource_limits, 1)
            raise AssertionError("check_resource_ceilings should have raised")

        settings = sanitizer.Settings(resource_limits=sanitizer.ResourceLimits(max_memory_bytes=1))
        with (
            mock.patch.object(sanitizer, "sanitize_document", side_effect=breaching_sanitize),
            mock.patch.object(sanitizer, "runtime_versions", return_value={"python": "test"}),
            mock.patch.object(sanitizer, "process_peak_rss_bytes", return_value=10**9),
        ):
            run_dir, payload = sanitizer.orchestrate_run(
                sources=[source], output_root=output_root, output_index_start=1,
                denylist={"Fictional Owner Holdings"}, settings=settings, temp_root=temp_root,
                denylist_path=denylist, project_metadata_path=None, config_path=config,
                allowlist_path=allowlist, lexicon_dir=lexicons, lexicons=None, ner_detector=None,
            )
        self.assertEqual(payload["release_status"], sanitizer.RELEASE_STATUS_FAIL)
        self.assertFalse(payload["documents"][0]["checks"]["processing_completed"])
        self.assertIn("memory ceiling exceeded", payload["documents"][0]["fail_reason"])
        self.assertTrue((run_dir / "manifest.json").is_file())

    def test_cpu_limit_signal_converts_to_controlled_fail(self) -> None:
        with self.assertRaises(sanitizer.PageProcessingError) as caught:
            sanitizer._handle_cpu_limit_signal(signal.SIGXCPU, None)
        self.assertIn("CPU", caught.exception.reason)

    def test_setrlimit_failures_on_unsupported_limits_do_not_raise(self) -> None:
        with mock.patch.object(
            sanitizer.resource, "setrlimit", side_effect=OSError("not supported"),
        ):
            sanitizer.apply_process_resource_limits(sanitizer.ResourceLimits())  # must not raise


class RunCleanupTest(unittest.TestCase):
    """Ticket 02 / issue #32: confidential triage crops survive run
    completion regardless of automated outcome — deletion only happens once
    a human completes review (see TriagePruningTest), never at pipeline
    completion. Reuses AtomicRunPackagingTest's fixture/run pattern."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="run_cleanup_test_")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "source.pdf"
        self.source.write_bytes(b"synthetic source")
        self.config = self.root / "config.json"
        self.config.write_text("{}")
        self.denylist = self.root / "denylist.json"
        self.denylist.write_text(json.dumps({"identifiers": ["Fictional Owner Holdings"]}))
        self.allowlist = self.root / "allowlist.json"
        self.allowlist.write_text("{}")
        self.lexicons = self.root / "lexicons"
        self.lexicons.mkdir()
        for name in sanitizer.LEXICON_FILENAMES:
            (self.lexicons / name).write_text("{}")
        self.output_root = self.root / "runs"
        self.temp_root = self.root / "tmp"
        self.temp_root.mkdir()

    @staticmethod
    def fake_sanitize_with_triage(release_status: str):
        def fake_sanitize(source, destination, document_id, denylist, settings, temp_root,
                           run_key, ner_detector=None, lexicons=None):
            destination.write_bytes(b"synthetic sanitized pdf")
            triage_dir = destination.parent / "triage" / document_id
            triage_dir.mkdir(parents=True, exist_ok=True)
            (triage_dir / "residual_0001_page0001_street_address.png").write_bytes(b"crop")
            return {
                "document_id": document_id,
                "source_sha256": sanitizer.sha256_file(source),
                "output_sha256": sanitizer.sha256_file(destination),
                "pages": 1,
                "checks": {"processing_completed": True},
                "release_status": release_status,
            }
        return fake_sanitize

    def run_once(self, fake_sanitize) -> tuple[Path, dict]:
        with (
            mock.patch.object(sanitizer, "sanitize_document", side_effect=fake_sanitize),
            mock.patch.object(sanitizer, "runtime_versions", return_value={"python": "test"}),
        ):
            return sanitizer.orchestrate_run(
                sources=[self.source], output_root=self.output_root, output_index_start=1,
                denylist={"Fictional Owner Holdings"}, settings=sanitizer.Settings(),
                temp_root=self.temp_root, denylist_path=self.denylist,
                project_metadata_path=None, config_path=self.config,
                allowlist_path=self.allowlist, lexicon_dir=self.lexicons,
                lexicons=None, ner_detector=None,
            )

    def test_successful_run_leaves_triage_directory_for_review(self) -> None:
        run_dir, payload = self.run_once(
            self.fake_sanitize_with_triage(sanitizer.RELEASE_STATUS_AUTOMATED_PASS),
        )
        self.assertTrue(payload["all_automated_checks_pass"])
        self.assertTrue((run_dir / "triage").exists())

    def test_failed_run_leaves_triage_directory_untouched(self) -> None:
        def raising_sanitize(source, destination, document_id, denylist, settings, temp_root,
                              run_key, ner_detector=None, lexicons=None):
            triage_dir = destination.parent / "triage" / document_id
            triage_dir.mkdir(parents=True, exist_ok=True)
            (triage_dir / "residual_0001_page0001_street_address.png").write_bytes(b"crop")
            raise RuntimeError("boom")

        run_dir, payload = self.run_once(raising_sanitize)
        self.assertEqual(payload["release_status"], sanitizer.RELEASE_STATUS_FAIL)
        self.assertTrue((run_dir / "triage" / "sanitized_document_01").is_dir())

    def test_partial_pass_with_one_failed_document_leaves_triage_untouched(self) -> None:
        second_source = self.root / "source2.pdf"
        second_source.write_bytes(b"synthetic source 2")
        statuses = iter([sanitizer.RELEASE_STATUS_AUTOMATED_PASS, sanitizer.RELEASE_STATUS_FAIL])

        def mixed_sanitize(source, destination, document_id, denylist, settings, temp_root,
                            run_key, ner_detector=None, lexicons=None):
            destination.write_bytes(b"synthetic sanitized pdf")
            triage_dir = destination.parent / "triage" / document_id
            triage_dir.mkdir(parents=True, exist_ok=True)
            (triage_dir / "residual_0001_page0001_street_address.png").write_bytes(b"crop")
            return {
                "document_id": document_id,
                "source_sha256": sanitizer.sha256_file(source),
                "output_sha256": sanitizer.sha256_file(destination),
                "pages": 1,
                "checks": {"processing_completed": True},
                "release_status": next(statuses),
            }

        with (
            mock.patch.object(sanitizer, "sanitize_document", side_effect=mixed_sanitize),
            mock.patch.object(sanitizer, "runtime_versions", return_value={"python": "test"}),
        ):
            run_dir, payload = sanitizer.orchestrate_run(
                sources=[self.source, second_source], output_root=self.output_root,
                output_index_start=1, denylist={"Fictional Owner Holdings"},
                settings=sanitizer.Settings(), temp_root=self.temp_root,
                denylist_path=self.denylist, project_metadata_path=None,
                config_path=self.config, allowlist_path=self.allowlist,
                lexicon_dir=self.lexicons, lexicons=None, ner_detector=None,
            )
        self.assertFalse(payload["all_automated_checks_pass"])
        self.assertTrue((run_dir / "triage").exists())


class RunRetentionPruningTest(unittest.TestCase):
    """Ticket 02: the retention-pruning maintenance step (tools/prune_runs.py)
    deletes only run directories older than the configured window, and
    never touches an orphaned staging directory from a crashed run (startup
    recovery for abandoned runs is explicitly out of scope)."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="run_retention_test_")
        self.addCleanup(self.tmp.cleanup)
        self.output_root = Path(self.tmp.name)

    def _make_run_dir(self, name: str, age_days: float) -> Path:
        run_dir = self.output_root / name
        run_dir.mkdir(parents=True)
        (run_dir / "report.json").write_text("{}")
        timestamp = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=age_days)).timestamp()
        os.utime(run_dir, (timestamp, timestamp))
        return run_dir

    def test_prune_removes_only_directories_older_than_window(self) -> None:
        old_one = self._make_run_dir("run-old-1", age_days=40)
        old_two = self._make_run_dir("run-old-2", age_days=10)
        recent_one = self._make_run_dir("run-recent-1", age_days=2)
        recent_two = self._make_run_dir("run-recent-2", age_days=0.1)

        removed = sanitizer.prune_expired_runs(self.output_root, retention_days=7)

        self.assertEqual(set(removed), {old_one, old_two})
        self.assertFalse(old_one.exists())
        self.assertFalse(old_two.exists())
        self.assertTrue(recent_one.exists())
        self.assertTrue(recent_two.exists())

    def test_prune_ignores_staging_temp_directories(self) -> None:
        stray_staging = self._make_run_dir(".20260101T000000.000000Z-abcd1234.tmp-xyz", age_days=40)

        removed = sanitizer.prune_expired_runs(self.output_root, retention_days=7)

        self.assertEqual(removed, [])
        self.assertTrue(stray_staging.exists())

    def test_prune_against_empty_output_root_returns_nothing(self) -> None:
        empty_root = self.output_root / "does-not-exist-yet"
        self.assertEqual(sanitizer.prune_expired_runs(empty_root, retention_days=7), [])


class TriagePruningTest(unittest.TestCase):
    """Issue #32: the reviewed-triage maintenance step
    (sanitizer.prune_reviewed_triage, wired into tools/prune_runs.py) only
    deletes a run's triage/ directory once its manifest.json records
    review.status == "complete" — never on run completion alone."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="triage_pruning_test_")
        self.addCleanup(self.tmp.cleanup)
        self.output_root = Path(self.tmp.name)

    def _make_run_dir(self, name: str, *, review_status: str | None, with_triage: bool = True) -> Path:
        run_dir = self.output_root / name
        run_dir.mkdir(parents=True)
        if review_status is not None:
            manifest = {"review": {"status": review_status}}
            (run_dir / "manifest.json").write_text(json.dumps(manifest))
        if with_triage:
            triage_dir = run_dir / "triage" / "sanitized_document_01"
            triage_dir.mkdir(parents=True)
            (triage_dir / "residual_0001_page0001_street_address.png").write_bytes(b"crop")
        return run_dir

    def test_reviewed_run_has_triage_directory_removed(self) -> None:
        run_dir = self._make_run_dir("run-reviewed", review_status="complete")

        removed = sanitizer.prune_reviewed_triage(self.output_root)

        self.assertEqual(removed, [run_dir / "triage"])
        self.assertFalse((run_dir / "triage").exists())

    def test_unreviewed_run_leaves_triage_directory_untouched(self) -> None:
        run_dir = self._make_run_dir("run-unreviewed", review_status="not_started")

        removed = sanitizer.prune_reviewed_triage(self.output_root)

        self.assertEqual(removed, [])
        self.assertTrue((run_dir / "triage").exists())

    def test_run_with_no_manifest_is_skipped_without_raising(self) -> None:
        run_dir = self._make_run_dir("run-no-manifest", review_status=None)

        removed = sanitizer.prune_reviewed_triage(self.output_root)

        self.assertEqual(removed, [])
        self.assertTrue((run_dir / "triage").exists())

    def test_reviewed_run_with_no_triage_directory_is_a_noop(self) -> None:
        self._make_run_dir("run-reviewed-no-triage", review_status="complete", with_triage=False)

        removed = sanitizer.prune_reviewed_triage(self.output_root)

        self.assertEqual(removed, [])

    def test_only_reviewed_runs_are_cleaned_in_a_mixed_set(self) -> None:
        reviewed = self._make_run_dir("run-a-reviewed", review_status="complete")
        incomplete = self._make_run_dir("run-b-incomplete", review_status="incomplete")
        unstarted = self._make_run_dir("run-c-not-started", review_status="not_started")

        removed = sanitizer.prune_reviewed_triage(self.output_root)

        self.assertEqual(removed, [reviewed / "triage"])
        self.assertFalse((reviewed / "triage").exists())
        self.assertTrue((incomplete / "triage").exists())
        self.assertTrue((unstarted / "triage").exists())

    def test_prune_ignores_staging_temp_directories(self) -> None:
        stray_staging = self.output_root / ".20260101T000000.000000Z-abcd1234.tmp-xyz"
        stray_staging.mkdir(parents=True)
        (stray_staging / "manifest.json").write_text(json.dumps({"review": {"status": "complete"}}))
        triage_dir = stray_staging / "triage" / "sanitized_document_01"
        triage_dir.mkdir(parents=True)
        (triage_dir / "residual_0001_page0001_street_address.png").write_bytes(b"crop")

        removed = sanitizer.prune_reviewed_triage(self.output_root)

        self.assertEqual(removed, [])
        self.assertTrue((stray_staging / "triage").exists())

    def test_prune_against_empty_output_root_returns_nothing(self) -> None:
        empty_root = self.output_root / "does-not-exist-yet"
        self.assertEqual(sanitizer.prune_reviewed_triage(empty_root), [])


if __name__ == "__main__":
    unittest.main()
