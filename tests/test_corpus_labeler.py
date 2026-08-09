from __future__ import annotations

import http.client
import importlib.util
import io
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

import fitz
from PIL import Image
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

MODULE_PATH = Path(__file__).parents[1] / "tools" / "corpus_labeler.py"
SPEC = importlib.util.spec_from_file_location("corpus_labeler", MODULE_PATH)
labeler = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = labeler
SPEC.loader.exec_module(labeler)

HTML_PATH = Path(__file__).parents[1] / "tools" / "corpus_labeler.html"


def create_multipage_pdf(path: Path, page_count: int = 2) -> None:
    pdf = canvas.Canvas(str(path), pagesize=letter)
    for i in range(page_count):
        pdf.setFont("Helvetica", 24)
        pdf.drawString(72, 700, f"Synthetic page {i + 1} of {page_count}")
        pdf.showPage()
    pdf.save()


def valid_payload(**overrides) -> dict:
    payload = {
        "doc_id": "sample_doc",
        "source_pdf": "/tmp/sample_doc.pdf",
        "page_count": 2,
        "exported_at": "2026-08-09T00:00:00+00:00",
        "items": [
            {
                "id": "item-001", "page": 0, "bbox": [10.0, 20.0, 110.0, 60.0],
                "scale": 2.0833, "category": "person", "sensitivity": "sensitive",
                "disposition": "redact", "note": None,
            },
            {
                "id": "item-002", "page": 1, "bbox": [5.0, 5.0, 50.0, 25.0],
                "scale": 2.0833, "category": "manufacturer", "sensitivity": "not_sensitive",
                "disposition": "keep", "note": "hard negative, looks like a brand mark",
            },
        ],
    }
    payload.update(overrides)
    return payload


class DeriveDocIdTest(unittest.TestCase):
    def test_uses_filename_stem(self):
        self.assertEqual(labeler.derive_doc_id(Path("2024 Schedule.pdf")), "2024_Schedule")

    def test_override_wins(self):
        self.assertEqual(
            labeler.derive_doc_id(Path("2024 Schedule.pdf"), override="custom_id"), "custom_id",
        )

    def test_sanitizes_unsafe_characters(self):
        self.assertEqual(
            labeler.derive_doc_id(Path("Addendum #2 (final).pdf")), "Addendum_2_final",
        )

    def test_empty_after_sanitize_raises(self):
        with self.assertRaises(ValueError):
            labeler.derive_doc_id(Path("...pdf"), override="***")


class RenderPagePngTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.pdf_path = self.root / "doc.pdf"
        create_multipage_pdf(self.pdf_path, page_count=2)
        self.doc = fitz.open(self.pdf_path)

    def tearDown(self):
        self.doc.close()
        self.temp.cleanup()

    def test_renders_valid_png(self):
        png_bytes, width, height, scale = labeler.render_page_png(self.doc, 0, dpi=150, max_dimension=None)
        self.assertTrue(png_bytes.startswith(b"\x89PNG\r\n\x1a\n"))
        with Image.open(io.BytesIO(png_bytes)) as image:
            self.assertEqual(image.width, width)
            self.assertEqual(image.height, height)
        self.assertAlmostEqual(scale, 150 / 72.0)

    def test_different_pages_render_different_bytes(self):
        page0_bytes, *_ = labeler.render_page_png(self.doc, 0, dpi=150, max_dimension=None)
        page1_bytes, *_ = labeler.render_page_png(self.doc, 1, dpi=150, max_dimension=None)
        self.assertNotEqual(page0_bytes, page1_bytes)

    def test_max_dimension_caps_size_and_lowers_scale(self):
        _bytes, width, height, scale = labeler.render_page_png(self.doc, 0, dpi=600, max_dimension=200)
        self.assertLessEqual(max(width, height), 200)
        self.assertLess(scale, 600 / 72.0)


class WriteLabelExportTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_writes_valid_payload(self):
        dest = self.root / "labels" / "sample_doc.json"
        payload = valid_payload()
        result = labeler.write_label_export(payload, dest)
        self.assertEqual(result, dest)
        self.assertEqual(json.loads(dest.read_text(encoding="utf-8")), payload)

    def test_creates_missing_parent_directories(self):
        dest = self.root / "a" / "b" / "c" / "sample_doc.json"
        labeler.write_label_export(valid_payload(), dest)
        self.assertTrue(dest.exists())

    def test_overwrite_keeps_only_latest(self):
        dest = self.root / "sample_doc.json"
        labeler.write_label_export(valid_payload(), dest)
        second = valid_payload(items=[])
        labeler.write_label_export(second, dest)
        self.assertEqual(json.loads(dest.read_text(encoding="utf-8")), second)

    def test_missing_items_key_raises_and_writes_nothing(self):
        dest = self.root / "sample_doc.json"
        bad = valid_payload()
        del bad["items"]
        with self.assertRaises(ValueError):
            labeler.write_label_export(bad, dest)
        self.assertFalse(dest.exists())

    def test_bad_category_raises_and_writes_nothing(self):
        dest = self.root / "sample_doc.json"
        bad = valid_payload()
        bad["items"][0]["category"] = "not_a_real_category"
        with self.assertRaises(ValueError):
            labeler.write_label_export(bad, dest)
        self.assertFalse(dest.exists())

    def test_bad_sensitivity_raises(self):
        bad = valid_payload()
        bad["items"][0]["sensitivity"] = "maybe"
        with self.assertRaises(ValueError):
            labeler.write_label_export(bad, self.root / "sample_doc.json")

    def test_bad_disposition_raises(self):
        bad = valid_payload()
        bad["items"][0]["disposition"] = "shred"
        with self.assertRaises(ValueError):
            labeler.write_label_export(bad, self.root / "sample_doc.json")

    def test_malformed_bbox_wrong_length_raises(self):
        bad = valid_payload()
        bad["items"][0]["bbox"] = [1.0, 2.0, 3.0]
        with self.assertRaises(ValueError):
            labeler.write_label_export(bad, self.root / "sample_doc.json")

    def test_malformed_bbox_non_increasing_raises(self):
        bad = valid_payload()
        bad["items"][0]["bbox"] = [50.0, 50.0, 10.0, 10.0]
        with self.assertRaises(ValueError):
            labeler.write_label_export(bad, self.root / "sample_doc.json")

    def test_note_may_be_null(self):
        dest = self.root / "sample_doc.json"
        labeler.write_label_export(valid_payload(), dest)
        written = json.loads(dest.read_text(encoding="utf-8"))
        self.assertIsNone(written["items"][0]["note"])


class ServerRouteSmokeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.pdf_path = self.root / "doc.pdf"
        create_multipage_pdf(self.pdf_path, page_count=2)
        self.doc = fitz.open(self.pdf_path)
        self.output_dir = self.root / "labels"
        self.server = labeler.LabelerHTTPServer(
            ("127.0.0.1", 0), labeler.LabelerRequestHandler,
            doc=self.doc, doc_id="sample_doc", source_pdf=self.pdf_path,
            dpi=100, max_dimension=None, output_dir=self.output_dir,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.doc.close()
        self.temp.cleanup()

    def _connect(self) -> http.client.HTTPConnection:
        return http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)

    def test_get_html_page(self):
        conn = self._connect()
        conn.request("GET", "/")
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)
        self.assertIn("text/html", resp.getheader("Content-Type"))
        conn.close()

    def test_get_session(self):
        conn = self._connect()
        conn.request("GET", "/api/session")
        resp = conn.getresponse()
        body = json.loads(resp.read())
        self.assertEqual(resp.status, 200)
        self.assertEqual(body["doc_id"], "sample_doc")
        self.assertEqual(body["page_count"], 2)
        conn.close()

    def test_get_page_png(self):
        conn = self._connect()
        conn.request("GET", "/api/page.png?index=0")
        resp = conn.getresponse()
        body = resp.read()
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.getheader("Content-Type"), "image/png")
        self.assertTrue(body.startswith(b"\x89PNG\r\n\x1a\n"))
        conn.close()

    def test_get_page_meta(self):
        conn = self._connect()
        conn.request("GET", "/api/page-meta?index=0")
        resp = conn.getresponse()
        body = json.loads(resp.read())
        self.assertEqual(resp.status, 200)
        self.assertIn("width", body)
        self.assertIn("height", body)
        self.assertIn("scale", body)
        conn.close()

    def test_get_page_out_of_range(self):
        conn = self._connect()
        conn.request("GET", "/api/page.png?index=99")
        resp = conn.getresponse()
        resp.read()
        self.assertEqual(resp.status, 400)
        conn.close()

    def test_post_export_writes_file(self):
        items = [
            {
                "id": "item-001", "page": 0, "bbox": [1.0, 2.0, 30.0, 40.0],
                "scale": 100 / 72.0, "category": "person", "sensitivity": "sensitive",
                "disposition": "redact", "note": None,
            },
        ]
        conn = self._connect()
        body = json.dumps({"items": items}).encode("utf-8")
        conn.request("POST", "/api/export", body=body, headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        result = json.loads(resp.read())
        self.assertEqual(resp.status, 200)
        self.assertEqual(result["status"], "ok")
        dest = self.output_dir / "sample_doc.json"
        self.assertEqual(result["path"], str(dest))
        written = json.loads(dest.read_text(encoding="utf-8"))
        self.assertEqual(written["items"], items)
        self.assertEqual(written["doc_id"], "sample_doc")
        conn.close()

    def test_post_export_rejects_invalid_item(self):
        conn = self._connect()
        body = json.dumps({"items": [{"id": "item-001"}]}).encode("utf-8")
        conn.request("POST", "/api/export", body=body, headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        result = json.loads(resp.read())
        self.assertEqual(resp.status, 400)
        self.assertEqual(result["status"], "error")
        self.assertFalse((self.output_dir / "sample_doc.json").exists())
        conn.close()


class NoVisibleJsonTest(unittest.TestCase):
    def test_html_never_previews_raw_json(self):
        html = HTML_PATH.read_text(encoding="utf-8")
        self.assertNotIn("<textarea", html)
        self.assertNotRegex(html, r"JSON\.stringify\(items[^)]*\)\s*;?\s*\n?\s*[a-zA-Z0-9_.]*\.(textContent|innerHTML)")


if __name__ == "__main__":
    unittest.main()
