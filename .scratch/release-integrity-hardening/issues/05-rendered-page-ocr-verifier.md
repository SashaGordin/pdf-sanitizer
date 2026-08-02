# 05 — Rendered-page OCR verifier (in-pipeline)

**What to build:** The in-pipeline verifier gains a rendered-page OCR pass:
it renders every output page — regardless of that page's text/scanned
classification — at a documented OCR resolution, OCRs the full page, and runs
the same unified policy (ticket 03's shared lexicons/suppression/label logic)
against the OCR text, in addition to its existing structural checks (page
count/size, metadata, annotations, links, forms, bookmarks, attachments,
optional-content groups) and existing extracted-text policy scan. Any
unresolved match from any of these checks blocks `AUTOMATED_PASS` (ticket
01's vocabulary). The hard-coded `raster_ocr_from_sanitized_images_only: true`
report field is removed rather than measured separately — the rendered-OCR
pass makes the property it claimed true by construction.

**Blocked by:** 01 (blocks `AUTOMATED_PASS` using the new vocabulary), 03
(reuses the unified detection policy against OCR text).

**Status:** ready-for-agent

- [x] Every output page has a recorded rendered-verification result, whether
      or not it was already classified as searchable text.
- [x] A planted sensitive value that exists only in an embedded raster image
      on an otherwise text-heavy page is caught by the rendered-OCR pass even
      though the existing extracted-text scan misses it (extends the
      existing verifier test suite with this case).
- [x] Any unresolved rendered-OCR match blocks `AUTOMATED_PASS`; a
      resolved/suppressed one does not.
- [x] The hard-coded `raster_ocr_from_sanitized_images_only` field no longer
      appears anywhere in the report.
- [x] A fresh run of the 43-page MLK reference document contains no
      current-policy match in full-page rendered OCR.
- [ ] Wall-clock and peak memory per page type are recorded for this pass on
      both reference documents.

## Comments

Implementation and focused coverage completed 2026-08-01. Fresh run
`20260801T231421.035584Z-a6b4b0d9` recorded 43/43 output pages clean. It
recorded wall-clock and peak RSS for searchable and mixed pages on the
43-page MEP reference. The 1,475-page Specs reference benchmark remains open.
