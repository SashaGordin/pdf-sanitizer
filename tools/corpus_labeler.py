#!/usr/bin/env python3
"""Real corpus-labeling tool (ticket 04, production-readiness-phase3-6).

Draw a bounding box on a real rendered PDF page, tag it with category /
sensitivity decision / expected disposition / an optional note, and export
the labeled items straight to disk. Replaces the throwaway prototype
(`tools/prototype_corpus_labeler.*`, unmerged branch
`origin/worktree-prototype-corpus-labeler`), which rendered a synthetic
placeholder page and only offered a browser-download export.

Binds to localhost only, launched by the operator on their own machine --
this does not make the tool a network service (ADR-0001).

    .venv-anonymizer/bin/python tools/corpus_labeler.py path/to/document.pdf
    .venv-anonymizer/bin/python tools/corpus_labeler.py path/to/document.pdf --doc-id my_doc
"""

from __future__ import annotations

import argparse
import datetime as dt
import http.server
import importlib.util
import io
import json
import re
import sys
import webbrowser
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import parse_qs, urlsplit

try:
    import fitz  # PyMuPDF
    from PIL import Image
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "Required local PDF dependencies are missing. Install requirements-anonymizer.txt."
    ) from exc

MODULE_PATH = Path(__file__).with_name("anonymize_construction_pdfs.py")
_spec = importlib.util.spec_from_file_location("pdf_sanitizer", MODULE_PATH)
sanitizer = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
sys.modules[_spec.name] = sanitizer
_spec.loader.exec_module(sanitizer)

HTML_PATH = Path(__file__).with_name("corpus_labeler.html")

CATEGORIES: tuple[tuple[str, str, str | None], ...] = (
    ("person", "Person name", "sensitive"),
    ("firm", "Firm / company name", "sensitive"),
    ("project", "Project identifier", "sensitive"),
    ("address", "Address", "sensitive"),
    ("contact", "Contact info (phone / email)", "sensitive"),
    ("account_id", "Account / ID number", "sensitive"),
    ("path", "File path", "sensitive"),
    ("signature", "Signature", "sensitive"),
    ("barcode", "Barcode / QR", "sensitive"),
    ("manufacturer", "Manufacturer / brand", "negative"),
    ("standard", "Standard / code reference", "negative"),
    ("schedule", "Schedule / table cell", "negative"),
    ("boilerplate", "Boilerplate text", "negative"),
    ("structural", "Structural artifact (dimension, section code)", "negative"),
    ("garbage", "OCR / extraction garbage", "negative"),
    ("other", "Other (see note)", None),
)
CATEGORY_VALUES = frozenset(value for value, _, _ in CATEGORIES)
SENSITIVITY_VALUES = ("sensitive", "not_sensitive")
DISPOSITION_VALUES = ("redact", "keep")

_UNSAFE_DOC_ID_CHARS = re.compile(r"[^A-Za-z0-9_-]+")


def derive_doc_id(pdf_path: Path, override: str | None = None) -> str:
    """The `<doc-id>` half of `.scratch/corpus/labels/<doc-id>.json`."""
    raw = override if override is not None else pdf_path.stem
    doc_id = _UNSAFE_DOC_ID_CHARS.sub("_", raw).strip("_")
    if not doc_id:
        raise ValueError(
            f"could not derive a usable doc-id from {raw!r}; pass --doc-id explicitly"
        )
    return doc_id


def render_page_png(
    doc: fitz.Document, page_index: int, dpi: int, max_dimension: int | None,
) -> tuple[bytes, int, int, float]:
    """Rasterize one page via the sanitizer's own PyMuPDF path, as a PNG."""
    page = doc[page_index]
    image, scale = sanitizer.page_image(page, dpi, max_dimension)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue(), image.width, image.height, scale


def validate_export_payload(payload: Any) -> None:
    """Raise ValueError describing the first schema violation found."""
    if not isinstance(payload, dict):
        raise ValueError("payload must be a JSON object")
    for key, expected in (
        ("doc_id", str), ("source_pdf", str), ("page_count", int), ("exported_at", str),
    ):
        if key not in payload:
            raise ValueError(f"payload missing required field {key!r}")
        if not isinstance(payload[key], expected):
            raise ValueError(f"payload field {key!r} must be {expected.__name__}")
    if isinstance(payload["page_count"], bool) or payload["page_count"] <= 0:
        raise ValueError("payload field 'page_count' must be a positive integer")
    if not payload["doc_id"]:
        raise ValueError("payload field 'doc_id' must not be empty")

    items = payload.get("items")
    if not isinstance(items, list):
        raise ValueError("payload missing required field 'items' (must be a list)")

    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError(f"items[{index}] must be an object")
        if not isinstance(item.get("id"), str) or not item["id"]:
            raise ValueError(f"items[{index}].id must be a non-empty string")
        page = item.get("page")
        if isinstance(page, bool) or not isinstance(page, int):
            raise ValueError(f"items[{index}].page must be an integer")
        if not (0 <= page < payload["page_count"]):
            raise ValueError(f"items[{index}].page {page} is out of range for page_count {payload['page_count']}")
        bbox = item.get("bbox")
        if not isinstance(bbox, list) or len(bbox) != 4 or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in bbox):
            raise ValueError(f"items[{index}].bbox must be a list of four numbers")
        x0, y0, x1, y1 = bbox
        if not (x0 < x1 and y0 < y1):
            raise ValueError(f"items[{index}].bbox must satisfy x0<x1 and y0<y1")
        scale = item.get("scale")
        if isinstance(scale, bool) or not isinstance(scale, (int, float)) or scale <= 0:
            raise ValueError(f"items[{index}].scale must be a positive number")
        if item.get("category") not in CATEGORY_VALUES:
            raise ValueError(f"items[{index}].category must be one of {sorted(CATEGORY_VALUES)}")
        if item.get("sensitivity") not in SENSITIVITY_VALUES:
            raise ValueError(f"items[{index}].sensitivity must be one of {SENSITIVITY_VALUES}")
        if item.get("disposition") not in DISPOSITION_VALUES:
            raise ValueError(f"items[{index}].disposition must be one of {DISPOSITION_VALUES}")
        note = item.get("note")
        if note is not None and not isinstance(note, str):
            raise ValueError(f"items[{index}].note must be a string or null")


def write_label_export(payload: dict[str, Any], dest_path: Path) -> Path:
    """Validate then atomically write a labeled-items export.

    Re-exporting the same doc-id overwrites the prior file -- there is no
    versioning across exports within a single labeling session.
    """
    validate_export_payload(payload)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = dest_path.with_suffix(dest_path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp_path.replace(dest_path)
    return dest_path


class LabelerHTTPServer(http.server.HTTPServer):
    def __init__(
        self, server_address: tuple[str, int], handler_cls: type[http.server.BaseHTTPRequestHandler],
        *, doc: fitz.Document, doc_id: str, source_pdf: Path, dpi: int,
        max_dimension: int | None, output_dir: Path,
    ) -> None:
        super().__init__(server_address, handler_cls)
        self.doc = doc
        self.doc_id = doc_id
        self.source_pdf = source_pdf
        self.dpi = dpi
        self.max_dimension = max_dimension
        self.output_dir = output_dir


class LabelerRequestHandler(http.server.BaseHTTPRequestHandler):
    server: LabelerHTTPServer  # type: ignore[assignment]

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        pass  # keep the operator's terminal quiet; errors still surface via responses

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _page_index_from_query(self, query: dict[str, list[str]]) -> int | None:
        raw = query.get("index", [None])[0]
        if raw is None:
            self._send_json(400, {"status": "error", "message": "missing required query param 'index'"})
            return None
        try:
            index = int(raw)
        except ValueError:
            self._send_json(400, {"status": "error", "message": f"invalid page index {raw!r}"})
            return None
        if not (0 <= index < self.server.doc.page_count):
            self._send_json(400, {"status": "error", "message": f"page index {index} out of range"})
            return None
        return index

    def do_GET(self) -> None:  # noqa: N802
        parts = urlsplit(self.path)
        query = parse_qs(parts.query)

        if parts.path == "/":
            body = HTML_PATH.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if parts.path == "/api/session":
            self._send_json(200, {
                "doc_id": self.server.doc_id,
                "source_pdf": str(self.server.source_pdf),
                "page_count": self.server.doc.page_count,
                "categories": [
                    {"value": value, "label": label, "group": group}
                    for value, label, group in CATEGORIES
                ],
                "sensitivity_values": list(SENSITIVITY_VALUES),
                "disposition_values": list(DISPOSITION_VALUES),
            })
            return

        if parts.path == "/api/page.png":
            index = self._page_index_from_query(query)
            if index is None:
                return
            png_bytes, _width, _height, _scale = render_page_png(
                self.server.doc, index, self.server.dpi, self.server.max_dimension,
            )
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(png_bytes)))
            self.end_headers()
            self.wfile.write(png_bytes)
            return

        if parts.path == "/api/page-meta":
            index = self._page_index_from_query(query)
            if index is None:
                return
            _png_bytes, width, height, scale = render_page_png(
                self.server.doc, index, self.server.dpi, self.server.max_dimension,
            )
            self._send_json(200, {"width": width, "height": height, "scale": scale})
            return

        self._send_json(404, {"status": "error", "message": f"no such route: {parts.path}"})

    def do_POST(self) -> None:  # noqa: N802
        parts = urlsplit(self.path)
        if parts.path != "/api/export":
            self._send_json(404, {"status": "error", "message": f"no such route: {parts.path}"})
            return

        length = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw_body.decode("utf-8")) if raw_body else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._send_json(400, {"status": "error", "message": f"invalid JSON body: {exc}"})
            return

        if not isinstance(body, dict) or not isinstance(body.get("items"), list):
            self._send_json(400, {"status": "error", "message": "request body must be an object with an 'items' list"})
            return

        payload = {
            "doc_id": self.server.doc_id,
            "source_pdf": str(self.server.source_pdf),
            "page_count": self.server.doc.page_count,
            "exported_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "items": body["items"],
        }
        dest_path = self.server.output_dir / f"{self.server.doc_id}.json"
        try:
            write_label_export(payload, dest_path)
        except ValueError as exc:
            self._send_json(400, {"status": "error", "message": str(exc)})
            return
        self._send_json(200, {
            "status": "ok", "path": str(dest_path), "item_count": len(payload["items"]),
        })


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    root = Path(__file__).resolve().parents[1]
    parser.add_argument("pdf_path", type=Path)
    parser.add_argument("--doc-id", default=None)
    parser.add_argument("--dpi", type=int, default=150)
    parser.add_argument("--max-dimension", type=int, default=1600)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--output-dir", type=Path, default=root / ".scratch/corpus/labels")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)

    if args.host not in ("127.0.0.1", "localhost"):
        print(
            "corpus_labeler refuses to bind outside localhost "
            "(ADR-0001: this tool is not a network service)",
            file=sys.stderr,
        )
        return 2

    try:
        doc_id = derive_doc_id(args.pdf_path, args.doc_id)
    except ValueError as exc:
        print(f"corpus_labeler failed: {exc}", file=sys.stderr)
        return 2

    try:
        doc = fitz.open(args.pdf_path)
    except (OSError, RuntimeError) as exc:
        print(f"corpus_labeler failed to open {args.pdf_path}: {exc}", file=sys.stderr)
        return 2

    args.output_dir.mkdir(parents=True, exist_ok=True)

    server = LabelerHTTPServer(
        (args.host, args.port), LabelerRequestHandler,
        doc=doc, doc_id=doc_id, source_pdf=args.pdf_path.resolve(),
        dpi=args.dpi, max_dimension=args.max_dimension, output_dir=args.output_dir,
    )
    url = f"http://{args.host}:{args.port}/"
    dest_path = args.output_dir / f"{doc_id}.json"
    print(f"Labeling {args.pdf_path} ({doc.page_count} pages) as doc-id {doc_id!r}")
    print(f"Serving at {url}")
    print(f"Export will write to {dest_path}")
    if not args.no_browser:
        webbrowser.open(url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        doc.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
