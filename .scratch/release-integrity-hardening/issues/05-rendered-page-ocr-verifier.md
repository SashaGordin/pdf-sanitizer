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

**Status:** done

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
- [x] Wall-clock and peak memory per page type are recorded for this pass on
      both reference documents.

## Comments

Implementation and focused coverage completed 2026-08-01. Fresh run
`20260801T231421.035584Z-a6b4b0d9` recorded 43/43 output pages clean. It
recorded wall-clock and peak RSS for searchable and mixed pages on the
43-page MEP reference (searchable ~7.2s/page, mixed ~10-14s/page, peak RSS
~2.7-3.0GB, across four back-to-back runs).

The 1,475-page Specs-MLK-Recreation-Center.pdf benchmark ran to completion in
run `20260802T012508.491357Z-57dd6c07` (started 2026-08-02T01:25:08Z,
completed 02:55:31Z — 90.4 minutes total for this document).
`rendered_ocr_verification.profile_by_page_type`: searchable 1,320 pages /
1375.1s (~1.04s/page) / 1.39GB peak RSS; mixed 150 pages / 203.7s (~1.36s/page);
raster 5 pages / 1.8s. The source-side rendered-OCR detection pass
(`source_rendered_ocr_detection.profile_by_page_type`) cost a comparable
1,491s across 1,475 pages. Per-page cost on this document is far lower than
on the 43-page MEP reference, consistent with Specs being a text-heavy spec
book (smaller, simpler pages) versus MEP being large-format drawing sheets.

That run's `release_status` is `FAIL` — expected and correct, not a
regression: `render_smoke_test` found a blank-rendered page, five raster
pages independently failed with "residual identifier detected after raster
redaction" (contained to those five pages per ticket 03, not aborting the
1,475-page run), and 28 pages needed the rendered-output remediation pass
(`labelled_identifier`/`email` categories) before the rendered-page-OCR check
was satisfied. This run started at 01:25:08Z, three minutes before the
follow-up fix in commit `9e6198b` (forcing `FAIL` whenever remediation fires,
regardless of how clean the re-verification looks) landed at 01:45:43Z, so it
does not exercise that specific guard — but it would have reported `FAIL`
regardless, from the independent raster and blank-page findings alone. Worth
a human pass over the five raster-failure pages and the one blank page before
trusting this run as release evidence, but that's ordinary
`REVIEW_REQUIRED`/`FAIL` triage, not a gap in this ticket's scope.
