# Plan: Unify detection/verification + triage output (audit defect b)

Audit next-step #2. Two failures today: (1) the verifier scans whole-page
`get_text("text")` while detection scans block-level text, so the verifier
flags matches that cross block boundaries in the page stream — text detection
could never see and that mostly isn't a real identifier (the 22 wrapped
spec-clause "street_address" hits); (2) a FAIL gives the reviewer nothing to
act on — 38 anonymous residuals across 1,475 pages.

## Design

**One match enumerator for both sides.** New `block_matches(blocks, denylist)`
generator yields `(category, block, match)` for every `DIRECT_PATTERNS` and
denylist hit over `TextBlock` text. `line_detections()` consumes it for
redaction; `verify_output()` consumes it over the *output* document's
`lines_from_page → text_blocks` stream. Symmetry by construction: any text
stream the verifier can flag is exactly the stream detection redacts from.
The label heuristics (`LABEL_VALUE_RE`, label→following-line) stay
detection-only: they are precision-limited redaction helpers, not leak
definitions, and re-running them on redacted output (labels remain, values
gone) would manufacture false positives.

**Triage output.** For each residual, the report gains an entry:
`{page, category, shape, crop}` where `shape` is the audit's masking
convention (uppercase→A, lowercase→a, digit→9, other chars kept, capped at 80
chars) — no original letter or digit can survive. `crop` points to a locally
rendered PNG under `<output_dir>/triage/<document_id>/`: the match region
plus context at 150 DPI, matched lines outlined in red, so a reviewer clears
a false positive in seconds. Caps: 200 residual entries/crops per document,
`residuals_truncated` counts the rest. The triage directory is cleared per
run. Crop rendering failures degrade to an entry without a crop — the FAIL
verdict never depends on rendering.

**Report/docs contract.** The JSON report now contains hashes, counts, page
numbers, categories, and *masked shapes* — still no recoverable content.
Crops DO contain original pixels; ANONYMIZATION.md documents them as
sensitive local material to delete after review, and updates the report
description. Existing check keys (`direct_identifier_scan`, `denylist_scan`,
counts) keep their names and meaning.

## Changes

- `tools/anonymize_construction_pdfs.py`: `block_matches()`, `masked_shape()`,
  `render_residual_crop()`; `line_detections()` refactored onto
  `block_matches()`; `verify_output()` rewritten to scan block text, build
  `residuals` list, render crops; `sanitize_document()` passes the triage dir.
- `ANONYMIZATION.md`: report contents sentence + triage section.

## Tests (fail first, then pass)

- `masked_shape` masks every letter/digit, preserves case pattern and
  punctuation, truncates.
- `verify_output` run directly against an *unsanitized* leaky PDF (email +
  wrapped denylist term): FAIL verdict; residual entries carry correct page,
  category; shapes contain none of the original letters/digits; referenced
  crop files exist and open as images.
- Cross-block page-stream false positive: two distant text blocks whose
  concatenation in `get_text("text")` forms an address-shaped string. Old
  verifier FAILs the sanitized output; unified verifier must PASS and the
  technical prose must survive in the output.
- Full existing suite stays green.

## Verification on real data

Re-run the residual scan against the existing FAILED real specs output
(counts only, no content): old whole-page scan vs new block-level scan, to
measure how much of the 38-residual noise the unification removes. Full
sanitize re-run of the specs stays deferred until defect (c) is fixed.

## Risks / accepted tradeoffs

- Block-level verification is deliberately blinder to cross-block matches
  than the old page-stream scan — that asymmetry was the false-positive
  machine, and unlabeled cross-block leaks are not reachable by any current
  detector anyway. The human gate remains the backstop.
- Masked shapes reveal length/case/punctuation structure. Accepted: that is
  the audit's own masking convention, and report hygiene tests enforce that
  no denylist term appears.
