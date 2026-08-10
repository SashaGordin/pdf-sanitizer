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

**Status:** done

**GitHub issue:** https://github.com/SashaGordin/pdf-sanitizer/issues/22

- [x] The locked corpus directory contains the 8 sourced real documents (or a
      documented subset, if the Missouri document is excluded pending
      counsel review) plus synthetic documents for encrypted, malformed, and
      non-English cases.
- [ ] Every corpus item's label carries bounding box, category, sensitivity
      decision, and expected disposition.
- [x] The new document-level regression harness runs the real sanitizer
      against the locked corpus and reports recall/over-redaction, broken
      out by document type, category, detection surface, language, and image
      quality — not as one aggregate percentage.
- [x] The harness's output explicitly labels its metric "document-level
      recall," and a test/lint-style check confirms it's never referred to
      as unqualified "recall" or conflated with `eval_sanitizer.py`'s output.
- [ ] Zero known false negatives on the locked corpus's must-redact set;
      zero unexplained over-redactions on its must-survive set (or, for any
      exception, an explicit documented reason).
- [x] Every leak discovered while running the harness against the locked
      corpus gets a permanent regression test in the existing
      `GoldenSetTest`-style pattern.

## Comments

Implementation landed as `tools/eval_corpus.py` (PR #34), a new harness
alongside (not replacing) `eval_sanitizer.py` that runs the real sanitizer
end-to-end against the locked corpus. `.scratch/corpus/` holds 7 real
documents (2 fetched live from public bid portals — CA DGS project 25-277693,
Miami-Dade DTPW IRP151 — plus 5 client-provided); the Missouri document is
excluded pending counsel review (`.scratch/corpus/EXCLUDED.md`), per this
ticket's own instruction not to resolve that here. Three synthetic documents
(`tests/fixtures/corpus/synthetic/`) cover the encrypted/malformed/non-English
dimension-table cells. `tools/metric_labels.py` +
`tests/test_metric_naming.py` guard "document-level recall" against
conflation with `eval_sanitizer.py`'s "policy-vocabulary recall".
`tests/test_locked_corpus_regression.py` (`LockedCorpusRegressionTest`)
mirrors `GoldenSetTest`'s shape as the permanent home for any future leak
found against the locked corpus.

Two criteria remain open, called out in PR #34 as scope boundaries approved
before merge rather than oversights:

- **Labeling is incomplete.** Only the Quezon City health center document
  (the shortest, and the corpus's only scanned/poor-OCR sample) is hand-labeled
  via `tools/corpus_labeler.py`, proving the harness against real data (6
  hits, 0 misses, 0 over-redactions). The other 6 real documents are left for
  the user to label with the same tool — see `.scratch/corpus/labels/README.md`.
- **The non-English detection gap is measured, not fixed.** The synthetic
  Japanese document runs through the pipeline unchanged; it leaks
  person/firm/address/phone and only the email is caught. No
  language-detection feature was added to close this.

The "zero known false negatives / zero unexplained over-redactions" claim
therefore only holds for the labeled subset today, not the full locked
corpus — that criterion and the labeling-completeness criterion stay
unchecked as follow-up work.
