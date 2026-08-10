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
  person/firm/address/phone content with one script-agnostic email address.
  Verified (with no project-metadata/denylist seeding, so English-oriented
  denylist/NER matching gets no help): the pipeline redacts only the email
  (`redaction_counts == {"email": 1}`) — the person/firm/address/phone lines
  are untouched. A rendered pixmap of the output confirms this by eye: the
  Japanese glyphs remain fully legible; only the email line is blacked out.
  This is a documented generalization finding, not a bug fixed by this
  ticket. Note: `get_text("text")` on the sanitized output is *not* a
  reliable way to check this — something in the flatten path re-encodes the
  reserved CJK font in a way that garbles ToUnicode mapping on extraction
  (mojibake, not blank), even though the rendered appearance is unaffected.
  The regression test asserts on `redaction_counts`/`page_redactions`
  instead, for exactly this reason.

See `tools/build_synthetic_corpus_documents.py` and
`tests/test_build_synthetic_corpus_documents.py` for the empirical checks
backing each of these expectations.
