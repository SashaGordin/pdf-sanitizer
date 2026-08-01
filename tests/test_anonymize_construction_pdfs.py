from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import subprocess

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

    def run_sanitizer(self, source: Path, settings=None):
        destination = self.root / "sanitized_document_01.pdf"
        report = sanitizer.sanitize_document(
            source, destination, "sanitized_document_01", FAKE_TERMS,
            settings or self.settings, self.root,
        )
        return destination, report

    def test_searchable_rotated_hidden_and_interactive_content(self) -> None:
        source = self.root / "source.pdf"
        create_searchable_pdf(source)
        destination, report = self.run_sanitizer(source)
        self.assertEqual(report["release_status"], sanitizer.RELEASE_STATUS_AUTOMATED_PASS)
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

        def timing_out(cmd, *args, **kwargs):
            raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout"))

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
        result = sanitizer.verify_output(
            source, sizes, sanitizer.DenylistMatcher(FAKE_TERMS), set(), triage_dir,
        )
        self.assertEqual(result["release_status"], sanitizer.RELEASE_STATUS_FAIL)
        residuals = result["residuals"]
        self.assertGreaterEqual(len(residuals), 2)
        self.assertEqual({residual["page"] for residual in residuals}, {1})
        categories = {residual["category"] for residual in residuals}
        self.assertIn("email", categories)
        self.assertIn("denylist", categories)
        for residual in residuals:
            for ch in residual["shape"]:
                if ch.isalpha():
                    self.assertIn(ch, "Aa")
                elif ch.isdigit():
                    self.assertEqual(ch, "9")
            crop = self.root / residual["crop"]
            self.assertTrue(crop.is_file())
            with Image.open(crop) as image:
                self.assertGreater(image.width, 10)
        denylist_shapes = [r["shape"] for r in residuals if r["category"] == "denylist"]
        self.assertTrue(any("\n" in shape for shape in denylist_shapes))

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

    def test_ner_review_is_report_only_with_masked_findings_and_crops(self) -> None:
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
            self.settings, self.root, ner_detector=detector,
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
        for ch in finding["shape"]:
            if ch.isalpha():
                self.assertIn(ch, "Aa")
            elif ch.isdigit():
                self.assertEqual(ch, "9")
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
            max_findings=1,
        )
        result = sanitizer.verify_output(
            source, sizes, sanitizer.DenylistMatcher(FAKE_TERMS), set(),
            self.root / "triage" / "sanitized_document_01", ner_detector=detector,
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
            self.root / "triage" / "sanitized_document_01",
        )
        self.assertNotIn("ner_review", without)

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
            FAKE_TERMS, sanitizer.Settings(), root,
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
            FAKE_TERMS, self.settings, self.root, lexicons=lexicons,
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
            source, destination, source.stem, FAKE_TERMS, self.settings, self.root,
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
            source, destination, "doc", FAKE_TERMS, self.settings, self.root,
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
                source, destination, "doc", FAKE_TERMS, self.settings, self.root,
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
            source, destination, "doc", FAKE_TERMS, self.settings, self.root,
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


if __name__ == "__main__":
    unittest.main()
