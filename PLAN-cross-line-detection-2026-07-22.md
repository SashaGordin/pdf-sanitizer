# Plan: Fix cross-line detection (audit defect a)

Audit next-step #1. Goal: detection sees text the way the verifier does, so the
38-residual FAIL on the specs run stops being structurally inevitable.

## Root causes (verified in code)

1. `line_detections()` runs `DIRECT_PATTERNS` and the denylist per `Line`.
   Most patterns already use `\s+`/`\s*` separators, so scanning block-level
   text fixes them with no pattern changes.
2. `LABEL_RE` (label alone on a line → value on following lines) exists only in
   `derive_terms()`; detection never uses it.
3. `DenylistMatcher` builds `re.escape(term)` — literal spaces — so a wrapped
   multi-word term can never match, in detection *or* verification.
4. `LABEL_NAME` accepts only " number" spelled out; `PROJECT NO.:` /
   `JOB #` style labels (the masked page-122 residual shape) match nothing.

## Changes — all in `tools/anonymize_construction_pdfs.py`

1. **Block-level scanning.** New `block_texts(lines)` groups `Line`s by their
   MuPDF block number (content order preserved via line numbers), joins with
   `\n`, and records per-line segment offsets. New `rects_for_block_span()`
   maps a match span back to one rect per touched line. `line_detections()`
   runs `DIRECT_PATTERNS` + denylist over block text instead of per line.
   `LABEL_VALUE_RE` stays per-line (it is `^…$` anchored).
2. **Port label→following-line values into detection.** Shared helper
   `following_value_lines(lines, index)` encoding the exact `derive_terms`
   rules (≤3 following lines, ≤120 pt vertical gap, `redactable_phrase`
   allow_short, ≤10 words, not itself a label). `derive_terms` and a new
   detection step both use it; detection emits the full line rect as
   `labelled_identifier`. Geometry-based, so it also covers label/value pairs
   split across MuPDF blocks.
3. **Whitespace-tolerant denylist.** Join escaped term words with `\s+`.
   Strengthens detection and verification identically (verifier reuses the
   same matcher), preserving symmetry.
4. **Label variants.** Extend the number-label alternatives to
   `(?: (?:number|no\.?|num\.?|#))?` so `PROJECT NO.:`, `JOB #` are labels.
5. **Same fix in the OCR path.** `ocr_detection_boxes()` gets the identical
   treatment via paragraph-level grouping of `OcrWord`s (block, paragraph),
   so the raster fallback and its post-redaction re-check stay symmetric with
   the vector path.

## Tests — `tests/test_anonymize_construction_pdfs.py`

Regression tests built from the masked residual shapes (synthetic values only,
never real document content):

- `PROJECT NO.:` on one line, value on the next → value gone from output.
- Label alone (`OWNER:`) → unlisted firm name on the next line redacted.
- Name on one line, `PE` credential on the next → redacted.
- Street address wrapped across two lines → redacted.
- Multi-word denylist term wrapped across two lines → redacted.
- Technical text on the same pages survives (over-redaction guard).

Discipline: add tests first, confirm they FAIL on current code, then implement
and confirm PASS plus the full existing suite.

## Risks / accepted tradeoffs

- Detection now matches some things the verifier previously flagged as false
  positives (e.g. numbered spec clauses shaped like addresses) → more
  redaction of prose. Accepted: fail-toward-redaction is the NDA posture, and
  triage tooling is audit next-step #2, not this change.
- If MuPDF block grouping proves too fine (label/value in separate blocks),
  the geometric label→following-line step still covers labelled cases;
  a geometric block-merge fallback is the contingency, not built now.
- Re-running the real specs document is deferred until defect (c)
  (Ghostscript rotation) is also fixed — a clean PASS needs both.
