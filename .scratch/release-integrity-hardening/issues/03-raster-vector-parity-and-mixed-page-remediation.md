# 03 — Raster/vector detection parity, mixed-page remediation & architect-of-record default

**What to build:** The raster (OCR-driven) detection path is brought to
parity with the vector (text-extraction-driven) path: it gains the same
lexicons, `candidate_suppression()` logic, and label-following-line detection,
so a scanned page and a searchable page are held to the identical redaction
policy. A suppressible false positive discovered during raster verification
fails only that page (forced to a failed/`REVIEW_REQUIRED` state for that
page), never aborts the entire run. The searchable/scanned page
classification (currently a flat `min_text_chars` character-count threshold)
is replaced with one that accounts for raster/image content on an otherwise
text-bearing page, so a mixed page isn't misclassified as "searchable, skip
raster detection." A sensitive value found only in pixels or graphics is
redacted by redacting those pixels directly, or by rasterizing the whole
affected page when coordinate confidence is too low. The architect-of-record's
firm name and location are added to (or confirmed already covered by) the
denylist/lexicon content so they're redacted by default like any other
identifying detail.

**Blocked by:** None — can start immediately.

**Status:** done

- [x] `ocr_detection_boxes()` (or its replacement) accepts lexicons and
      applies `candidate_suppression()` and label-following-line logic
      identically to the vector path.
- [x] A parity test feeds equivalent sensitive/suppressible content through
      vector text and scanned images and asserts equivalent candidate/policy
      decisions.
- [x] A synthetic run with one page carrying a suppressible false positive on
      the raster path completes with only that page marked failed/
      `REVIEW_REQUIRED` — the run does not abort.
- [x] A mixed page (some vector text, some sensitive content inside an
      embedded image) is no longer classified as pure "searchable," and its
      embedded-image content is proven redacted.
- [x] The old `min_text_chars` flat-threshold classification is gone; a
      documented replacement rule accounts for raster/image content ratio.
- [x] A synthetic architect-of-record stamp (firm name + city/state) is
      proven redacted by default without an explicit per-run denylist entry.

**Comments**

- Architect-of-record redaction was previously an open policy question in
  `PRODUCTION-READINESS-PLAN.md`; `CONTEXT.md` now resolves it as
  "redact by default," which this ticket implements.
