#!/usr/bin/env python3
"""Independent leak check: scan a sanitized PDF's raw text for known identifiers.

The pipeline's own verifier can only flag what its detectors can see. When
detection and verification share a blind spot the report says PASS with zero
residuals while the identifier is still on the page — which is exactly what
happened when a title block split "CCR Architecture &" from "Interiors" into
separate MuPDF blocks and the architect's name survived on all 43 sheets.

This tool deliberately shares none of that plumbing. It takes raw
`page.get_text()` output, normalizes whitespace across the whole page, and
looks for denylist terms with the loosest reasonable separator tolerance. It
will report things the pipeline correctly considers fine; that is the point.
A hit here means look at the page.

Local-only and read-only.

    .venv-anonymizer/bin/python tools/verify_output_text.py output/pdf/sanitized_document_*.pdf
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from pathlib import Path

try:
    import fitz
except ImportError as exc:  # pragma: no cover
    raise SystemExit("PyMuPDF is required. Install requirements-anonymizer.txt.") from exc

DASHES = r"\-‐-―−"


def term_pattern(term: str) -> re.Pattern[str]:
    """Loosest reasonable rendering tolerance for one known identifier."""
    parts = [re.escape(p) for p in re.split(rf"[\s{DASHES}]+", term) if p]
    if not parts:
        return re.compile(r"(?!)")
    body = rf"[\s{DASHES}.,]*".join(parts)
    return re.compile(rf"(?i)(?<![A-Z0-9]){body}(?![A-Z0-9])")


def load_terms(denylist: Path, extra: Path | None) -> list[str]:
    terms: set[str] = set()
    for path in (denylist, extra):
        if path is None or not path.is_file():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        stack = [payload]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                stack.extend(v for k, v in node.items() if not k.startswith("_"))
            elif isinstance(node, list):
                stack.extend(node)
            elif isinstance(node, str) and len(node.strip()) >= 3:
                terms.add(node.strip())
    return sorted(terms, key=len, reverse=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    root = Path(__file__).parents[1]
    parser.add_argument("pdfs", nargs="+", type=Path)
    parser.add_argument("--denylist", type=Path, default=root / "config/denylist.local.json")
    parser.add_argument("--extra-terms", type=Path, default=None,
                        help="optional JSON of additional strings to hunt for")
    parser.add_argument("--show", type=int, default=3, help="example pages per hit")
    args = parser.parse_args()

    terms = load_terms(args.denylist, args.extra_terms)
    if not terms:
        raise SystemExit("No terms to check for")
    patterns = [(term, term_pattern(term)) for term in terms]
    print(f"checking {len(terms)} known identifiers against {len(args.pdfs)} document(s)\n")

    failures = 0
    for pdf_path in args.pdfs:
        if not pdf_path.is_file():
            raise SystemExit(f"Not a readable file: {pdf_path}")
        doc = fitz.open(pdf_path)
        hits: dict[str, list[int]] = collections.defaultdict(list)
        for index, page in enumerate(doc):
            # Whole-page text, whitespace flattened, so a term broken across
            # blocks, columns, or lines is still visible here.
            text = re.sub(r"\s+", " ", page.get_text())
            for term, pattern in patterns:
                if pattern.search(text):
                    hits[term].append(index + 1)
        total_pages = len(doc)
        doc.close()
        status = "CLEAN" if not hits else f"{len(hits)} TERM(S) FOUND"
        print(f"{pdf_path.name}  ({total_pages} pages): {status}")
        for term, pages in sorted(hits.items(), key=lambda kv: -len(kv[1])):
            shown = ", ".join(str(p) for p in pages[: args.show])
            more = f" (+{len(pages) - args.show} more)" if len(pages) > args.show else ""
            masked = "".join("A" if c.isupper() else "a" if c.isalpha()
                             else "9" if c.isdigit() else c for c in term)[:48]
            print(f"    {len(pages):>5} pages  shape={masked:<50} first: {shown}{more}")
            failures += 1
        print()

    if failures:
        print("A hit is not automatically a leak — inspect the page. But the pipeline")
        print("report cannot be treated as evidence of a clean output while any remain.")
        return 1
    print("No known identifier found in the extracted text of any output.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
