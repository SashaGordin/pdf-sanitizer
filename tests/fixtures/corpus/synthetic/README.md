# Synthetic locked-corpus documents

Three small, generated PDFs covering the dimension-table cells no public
document supplies (PRODUCTION-READINESS-PLAN.md Phase 3): encrypted,
malformed, and non-English. Zero real PII — safe to commit, unlike the
locked corpus's real documents (see `.scratch/corpus/`, which are
untracked).

Regenerate with:

    .venv-anonymizer/bin/python tools/build_synthetic_corpus_documents.py

- `encrypted_spec.pdf` — password-protected (reportlab `StandardEncryption`).
  Expected outcome: `sanitize_document()` returns a document-level
  `release_status: FAIL` with `fail_reason` mentioning "encrypt" — this tool
  never attempts decryption.
- `malformed_spec.pdf` — every byte after the `%PDF-1.x` header line is
  overwritten with fixed-seed pseudo-random bytes. Not a truncated download:
  the file's total length is unchanged, nothing is missing from the tail.
  Expected outcome: `fitz.open()` raises; `sanitize_document()` raises
  `SystemExit`. (Corrupting only the xref offset, or only the trailer, was
  empirically insufficient — MuPDF's repair mode recovers from those.)
- `non_english_spec.pdf` — a Japanese-language (CJK) spec-shaped page mixing
  person/firm/address content (expected to show a real recall gap against
  the unmodified, English-oriented pipeline — a documented generalization
  finding, not a bug fixed by this ticket) with one script-agnostic email
  address (expected to still be caught, since email/phone/URL detection is
  regex-based, not language-dependent).

See `tools/build_synthetic_corpus_documents.py` and
`tests/test_build_synthetic_corpus_documents.py` for the empirical checks
backing each of these expectations.
