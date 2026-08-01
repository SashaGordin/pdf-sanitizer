# PDF Sanitizer Current-State Assessment — 2026-08-01

This dated companion to `PRODUCTION-READINESS-PLAN.md` records measurements
and implementation findings from the working tree on 2026-08-01. Current-state
claims below were measured; architectural recommendations and capacity
estimates remain proposals until implemented and benchmarked.

## The environment baseline

Python 3.14.3 · PyMuPDF 1.28.0 (MuPDF 1.29.0) · Pillow 12.3.0 · zxing-cpp
3.1.0 · Tesseract 5.5.2 (leptonica 1.87.0) · Ghostscript 10.07.1 · GLiNER
`gliner_multi_pii-v1`. Requirements are range-constrained
(`PyMuPDF>=1.24,<2`) but unlocked and not hash-pinned; there is no lockfile or
SBOM.

Record this in the first run manifest. It is the thing a clean machine has to
reproduce.

## What is healthy

- **Unit suite: 40 tests, all passing** in 2.6 s. Test breadth is broad —
  searchable and image-only pages, cross-line and cross-block detection, page
  scope vs. geometry order, rotation through flattening, the post-flatten
  sweep, NER report-only contract, lexicon scoping, denylist seeding, and
  detection/verification suppression symmetry.
- **Golden-set scoring is clean**: recall 100% (31/31), over-redaction 0%
  (0/64), noise handled 100% (60/60), against a 40-term denylist and lexicons
  holding 98 defined terms, 16 structural patterns, 108 boilerplate phrases,
  and 59 allowlist entries.
- **`GoldenSetTest` fails the test suite** on a recall or over-redaction
  regression. That is the regression-gate pattern Phase 3 wants; extend it
  rather than reinventing it. No CI build gate is configured yet.

## What that scoring does *not* mean

`tools/eval_sanitizer.py` never opens a PDF. It scores three pure functions —
`DenylistMatcher.finditer`, `Lexicons.suppression_reason`, and
`Lexicons.rejects_proposed_term` — against 95 bare strings in
`tests/golden/mlk_labels.json`.

So "recall 100%" means *the matcher matches these 95 strings*. It says nothing
about extraction, coordinates, redaction, flattening, rasterization, or
rendering. An identifier can score as redacted here and still survive in an
output, because no page was ever involved. The metric is useful and worth
keeping — it is the fastest signal on a lexicon change — but it must be
labelled **policy-vocabulary recall**, not recall, everywhere it is reported.
Phase 3's document-level corpus is what produces the number people think this
number is.

## The stale-artifact failure mode is the current state on disk

This is the finding that reorders the backlog.

| Artifact | Last written | Relationship to current PDF |
|---|---|---|
| `tools/anonymize_construction_pdfs.py` | 2026-07-31 22:31 | Newer than both PDFs |
| `config/denylist.local.json` | 2026-07-31 22:21 | Newer than both PDFs |
| `config/lexicons/*`, `allowlist.shared.json` | 2026-07-31 21:31 – 22:05 | Newer than both PDFs |
| `output/pdf/sanitized_document_01.pdf` | 2026-07-31 20:37 | SHA-256 `cb0e383e…` |
| `output/pdf/ner_review_report_mep.json` | 2026-07-31 20:39 | Hash-matches document 01; older code/policy |
| `output/pdf/sanitized_document_02.pdf` | 2026-07-23 18:56 | SHA-256 `82244618…` |
| `output/pdf/ner_review_report.json` | 2026-07-23 19:16 | Hash-matches document 02; older code/policy |
| `output/pdf/sanitization_review_report.json` | **2026-07-22 20:01** | Hash-matches neither current PDF |
| `output/pdf/sanitization_review_report_02.json` | 2026-07-20 22:13 | Historical FAIL; hash-matches neither current PDF |

The named sanitization report is older than both current PDFs and declares
`PASS` for two hashes that no longer exist at those paths:

- `sanitized_document_01` — report `cd7efbab…`, actual `cb0e383e…`
- `sanitized_document_02` — report `eb6d6a13…`, actual `82244618…`

Two NER reports do hash-match the current PDFs. They still cannot establish
current validity because they were produced before the current code and policy.
The directory has no canonical report-selection rule, the CLI does not enforce
the recorded output hash when a report is consumed, and no report fingerprints
the code, denylist, project metadata, configuration, allowlist, or lexicons.

Running the independent check against the **current** 40-term denylist finds
denylist terms still extractable from both:

- `sanitized_document_01.pdf` (43 pages): 3 terms, one of them a firm-name
  shape present on **41 of 43 sheets**.
- `sanitized_document_02.pdf` (1,475 pages): 15 terms, including a
  project-number shape on **1,012 pages** and another identifier on **1,092
  pages**.

Read this carefully, because the honest reading is stronger than the alarming
one. These outputs were produced under an *older* policy, so the hits are not
proof that today's code leaks — the page-scope fix, the file-path pattern, and
several denylist terms all landed after both files were written. What they
prove is:

1. Every PDF and every report in `output/pdf/` is unreleasable and must not be
   cited as evidence of anything.
2. **Nothing in the normal workflow resolves that ambiguity.** Output hashes
   are recorded, but they are not enforced automatically; there is no canonical
   report selector and no code or policy fingerprint. A human must choose a
   report, compare hashes, and separately determine whether its policy is still
   current.

Phase 0 is not housekeeping. It is the control that would have made the last
nine days of artifacts self-evidently void instead of plausibly green.

*(Masked shapes are described here in aggregate rather than tabulated. The
report's `masked_shape` is a strict one-to-one character map, so a shape table
sitting beside the output it describes is partially reconstructable — see the
report-hygiene item in Phase 5. Counts and page spans carry the argument.)*

## Confirmed defects, with locations

Line numbers are against `tools/anonymize_construction_pdfs.py` as of
2026-07-31 22:31.

- **Encrypted input crashes rather than failing controlled.** Verified against
  a synthetic AES-256 PDF: `fitz.open()` succeeds with `needs_pass=1`, then
  `sanitize_document` builds `source_sizes` by iterating pages at line 2048 —
  outside any `try` — so MuPDF's `ValueError: document closed or encrypted`
  escapes as a traceback. §1 of the architecture asks for "a controlled result
  that explains whether a password is required"; today it is an unhandled
  exception.
- **No subprocess timeouts.** Neither `subprocess.run` call has `timeout=` —
  Tesseract at line 1338, Ghostscript at line 1582. A pathological or hostile
  input can hang a run indefinitely, with no cancellation path.
- **One hard-coded verification check.** `"raster_ocr_from_sanitized_images_only":
  True` at line 1991 is asserted, never measured. It is the only literal in the
  `checks` dict and it is the one Phase 0 must remove.
- **Raster and vector paths run different policies.** `ocr_detection_boxes()`
  (line 1428) takes no `Lexicons` and never calls `candidate_suppression()`.
  The vector path, the post-flatten sweep, and the verifier all do. The raster
  path also lacks the label-following-line logic that `line_detections()` has.
  This is worse than asymmetric recall: `raster_page_pdf()` raises
  `RuntimeError("residual identifier detected after raster redaction")` on any
  post-redaction hit, which becomes a `PageProcessingError` and **aborts the
  entire run**. A single suppressible false positive on one scanned page can
  kill a 1,475-page job.
- **`min_text_chars = 20` is the mixed-page hole.** A page with 20 characters
  of vector text and everything else in a raster image is classified searchable
  (line 2066) and never OCRed. Phase 1's remediation has to replace this
  classification, not merely add a verifier behind it.
- **Review lists can truncate without blocking completion.** `TRIAGE_LIMIT =
  200` (line 1795) caps detailed residuals per document. The historical specs
  NER report written 2026-07-23 listed 500 findings and reported 11,423 more as
  truncated; it predates the current surface-form deduplication implementation
  and must not be used to describe current cap behavior. Current code caps
  distinct forms globally at `max_findings` (default 500). The remaining risks
  are label crowding and the fact that `residuals_truncated` or
  `findings_truncated` does not prevent a review from being called complete.
- **No version control.** There is no `.git` in the tree. A source-bundle
  digest can still be computed, but there is no commit identity, review history,
  or CI integration to support source provenance and controlled releases.
- **Permissions are inherited, not set.** One `os.chmod(0o600)` exists, on the
  candidates file (line 1119). No `mkdir()` call passes a mode. Measured:
  `output/`, `output/pdf/`, `output/pdf/triage/`, and `tmp/pdfs/` are all
  `0755`; every report is `0644`; **1,000 triage crops containing rendered
  output regions that may include residual identifiers are sitting at `0644`**
  under `output/pdf/triage/`. ANONYMIZATION.md
  instructs deleting the triage directory after review; nothing enforces or
  prompts it.
- **Retired artifacts persist beside current ones.**
  `sanitization_review_report_02.json` is a hand-renamed FAIL report from an
  earlier run; `anonymous-document-001.pdf` is from a retired naming scheme;
  `config/denylist.local.json.bak` shadows the live denylist.

## What already exists and should be extended, not rebuilt

The 2026-07-31 plan described several Phase 0 and Phase 3 deliverables as new
work. Partial implementations already exist and are the right starting points.

| Plan deliverable | Already exists | Gap to close |
|---|---|---|
| "Standalone command that verifies an existing output" (Phase 0) | `tools/verify_output_text.py` | Denylist terms only — no `DIRECT_PATTERNS`, no lexicon policy, no rendered OCR, no report binding, no policy fingerprint. Grow this into `verify-existing`; do not write a second tool. |
| "Generalization corpus and regression suite" (Phase 3) | `tools/eval_sanitizer.py` + `tests/golden/mlk_labels.json` (95 entries) | Wrong granularity: labelled *strings*, not labelled *pages* with bounding boxes. Keep the harness and its three-metric framing; move the corpus to documents. |
| "Previously discovered leaks remain permanent regression tests" (Phase 3) | `PageScopeTest`, `FilePathPatternTest`, `SuppressionSymmetryTest`, `GoldenSetTest` | Already the right pattern. Every new leak gets a test here. |
| "Deduplicated NER findings into review decisions" (Phase 2) | Surface-form dedup with `occurrences`/`pages`/`score_max`/`zone` | Done. Remaining gap is the global cap crowding out low-volume labels. |

One deliberate design property is worth protecting through all of this:
`tools/verify_output_text.py` **shares none of the pipeline's plumbing** — that
is why it caught the split-title-block leak the pipeline's own verifier could
not see. When it grows, it must keep independent extraction, independent
matching, and eventually an independent renderer. Refactoring it to reuse
`block_matches` would destroy the only thing it is for.

