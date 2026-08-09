# 05 — Locked corpus and document-level regression suite

**What to build:** Assemble the Phase 3 locked generalization corpus and
build the document-level regression harness that measures against it.

**Corpus composition:** the 8 documents identified in issue #10 (2
independently portal-sourced — CA DGS project manual, Miami-Dade DTPW
specifications — plus 6 client-provided documents, ~2,600 pages across 7
agencies/jurisdictions), plus synthetic documents built to cover exactly the
three dimension-table cells no public source supplies: encrypted PDFs,
malformed PDFs, and non-English-language documents. Flag the Missouri
school-district document's explicit reproduction-restriction notice for
counsel review before it's used (do not resolve that here — just don't use
it until cleared).

**Labeling schema:** every corpus item carries a bounding box, category,
sensitivity decision, and expected disposition — the step-up in granularity
issue #5 locked in, replacing the current 95 string-level
`tests/golden/mlk_labels.json` entries. Label the corpus using ticket 04's
real labeling tool.

**Regression harness:** a new module alongside (not replacing)
`tools/eval_sanitizer.py` that runs the real sanitizer end-to-end against
locked-corpus documents and scores recall/over-redaction per document,
category, and detection surface — reported as "document-level recall,"
distinct from and never conflated with the existing "policy-vocabulary
recall" `eval_sanitizer.py` reports.

**Blocked by:** 04 (needs the real labeling tool to produce the corpus's
labels).

**Status:** todo

- [ ] The locked corpus directory contains the 8 sourced real documents (or a
      documented subset, if the Missouri document is excluded pending
      counsel review) plus synthetic documents for encrypted, malformed, and
      non-English cases.
- [ ] Every corpus item's label carries bounding box, category, sensitivity
      decision, and expected disposition.
- [ ] The new document-level regression harness runs the real sanitizer
      against the locked corpus and reports recall/over-redaction, broken
      out by document type, category, detection surface, language, and image
      quality — not as one aggregate percentage.
- [ ] The harness's output explicitly labels its metric "document-level
      recall," and a test/lint-style check confirms it's never referred to
      as unqualified "recall" or conflated with `eval_sanitizer.py`'s output.
- [ ] Zero known false negatives on the locked corpus's must-redact set;
      zero unexplained over-redactions on its must-survive set (or, for any
      exception, an explicit documented reason).
- [ ] Every leak discovered while running the harness against the locked
      corpus gets a permanent regression test in the existing
      `GoldenSetTest`-style pattern.

## Comments
