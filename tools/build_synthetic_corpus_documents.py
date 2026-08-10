#!/usr/bin/env python3
"""Build the three synthetic locked-corpus documents that cover the
dimension-table cells no public source document supplies: encrypted,
malformed, and non-English. See PRODUCTION-READINESS-PLAN.md Phase 3 and
.scratch/production-readiness-phase3-6/issues/05-locked-corpus-and-document-level-regression-suite.md.

Regenerate with:
    .venv-anonymizer/bin/python tools/build_synthetic_corpus_documents.py

Output lands in tests/fixtures/corpus/synthetic/ — committed, since these
are small, generated, and contain zero real PII (the one existing
.gitignore carve-out for *.pdf is exactly this directory pattern).
"""

from __future__ import annotations

import random
from pathlib import Path

import fitz
from reportlab.lib.pagesizes import letter
from reportlab.lib.pdfencrypt import StandardEncryption
from reportlab.pdfgen import canvas

OUTPUT_DIR = Path(__file__).parents[1] / "tests" / "fixtures" / "corpus" / "synthetic"


def build_encrypted_document(path: Path) -> None:
    """A password-protected PDF. This tool never attempts decryption or
    accepts a password (see anonymize_construction_pdfs.py's is_encrypted
    check), so the expected outcome is a document-level FAIL, not per-item
    scoring — reuses the same reportlab StandardEncryption pattern already
    validated by tests/test_anonymize_construction_pdfs.py's
    create_encrypted_pdf()."""
    pdf = canvas.Canvas(
        str(path), pagesize=letter,
        encrypt=StandardEncryption("userpw", ownerPassword="ownerpw", canPrint=1, canModify=0),
    )
    for page_number in range(1, 4):
        pdf.setFont("Helvetica", 12)
        pdf.drawString(45, 700, f"SYNTHETIC PROJECT MANUAL — SECTION 0{page_number}")
        pdf.drawString(45, 680, "Prepared for Fabricated Test Owner Holdings")
        pdf.drawString(45, 660, "4321 Fabricated Test Parkway, Testville, TS 00000")
        pdf.drawString(45, 600, "TECHNICAL: ASTM A615 reinforcing steel, 4000 psi concrete")
        pdf.showPage()
    pdf.save()


def build_malformed_document(path: Path) -> None:
    """A structurally corrupted PDF — not a truncated download (the file's
    total length is unchanged and nothing is missing from the tail).
    Everything after the %PDF-1.x header line is overwritten with
    fixed-seed pseudo-random bytes, destroying every object definition, the
    xref table, and the trailer, while the header itself stays a valid PDF
    signature — the shape of e.g. a storage-layer bit-flip or an
    interrupted in-place rewrite that clobbered the body but left the
    first, separately-flushed block intact.

    Empirically verified (see implementation notes / ticket 05): corrupting
    only the xref offset, or only the trailer, or even the full body while
    leaving the trailer, is not enough — MuPDF's repair mode recovers a
    surprising amount. Only corrupting the header-to-EOF span in full
    reliably makes fitz.open() raise rather than silently repair.
    """
    valid = path.with_suffix(".valid.tmp")
    doc = fitz.open()
    for page_number in range(1, 4):
        page = doc.new_page()
        page.insert_text((50, 100), f"SYNTHETIC PROJECT MANUAL — SECTION 0{page_number}", fontsize=12)
    doc.save(str(valid))
    doc.close()

    data = bytearray(valid.read_bytes())
    valid.unlink()
    header_end = data.find(b"\n") + 1
    rng = random.Random(20260809)
    for i in range(header_end, len(data)):
        data[i] = rng.randrange(256)
    path.write_bytes(bytes(data))


def build_non_english_document(path: Path) -> None:
    """A Japanese-language spec-shaped document (CJK, non-Latin script —
    deliberately not a Latin-script language, which would understate the
    generalization gap this dimension exists to measure). Mixes categories
    that depend on English-oriented NER/lexicon matching (person, firm,
    address — expected to show a real recall gap when run through the
    unmodified pipeline) with one script-agnostic category (an email
    address, regex-detected, expected to still be caught) — see ticket 05's
    scope decision to run this document as-is and report the resulting gap,
    not to build language detection.
    """
    doc = fitz.open()
    page = doc.new_page()
    lines = [
        "総合建設仕様書",  # "General construction specification" — project title
        "発注者: 山田太郎",  # "Owner: Taro Yamada" — a person
        "施工会社: アルファ建設株式会社",  # "Contractor: Alpha Construction Co., Ltd." — a firm
        "所在地: 東京都渋谷区1-2-3",  # site address, Japanese formatting
        "電話: 03-1234-5678",  # phone, Japanese formatting
        "連絡先: contact@example.invalid",  # contact email — script-agnostic, ASCII
        "技術仕様: ASTM A615 鉄筋、4000 psi コンクリート",  # negative content: standards/materials
    ]
    for index, line in enumerate(lines):
        page.insert_text((50, 100 + 24 * index), line, fontname="japan", fontsize=13)
    doc.save(str(path))
    doc.close()


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    build_encrypted_document(OUTPUT_DIR / "encrypted_spec.pdf")
    build_malformed_document(OUTPUT_DIR / "malformed_spec.pdf")
    build_non_english_document(OUTPUT_DIR / "non_english_spec.pdf")
    print(f"Wrote 3 synthetic corpus documents to {OUTPUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
