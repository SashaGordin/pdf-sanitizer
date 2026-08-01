# 01 — Release status vocabulary, fail-closed input handling & CI gate

**What to build:** Report and manifest fields expose one of five explicit
statuses instead of today's single `PASS`/`FAIL` string and the
`HUMAN_VISUAL_REVIEW_REQUIRED` constant: `AUTOMATED_PASS`, `REVIEW_REQUIRED`,
`REVIEW_INCOMPLETE`, `FAIL`, `RELEASED` — applied consistently to both the
per-document report and the overall run. `AUTOMATED_PASS` is never terminal or
shippable on its own. An encrypted source PDF is detected immediately after
opening, before any page-dependent operation, and produces a controlled
`FAIL` explaining that the input is encrypted — never an uncaught traceback,
and never any decryption attempt or password argument. Every Tesseract
invocation (once per page) times out at 120 seconds and every Ghostscript
invocation (once per whole document) times out at 30 minutes, both failing
closed rather than hanging or crashing. A local git pre-push hook runs the
full pytest suite and the golden-set eval script (`tools/eval_sanitizer.py`),
blocking the push on either's failure.

**Blocked by:** None — can start immediately.

**Status:** ready-for-agent

- [ ] Per-document and overall-run reports use `release_status` with one of
      exactly `AUTOMATED_PASS`/`REVIEW_REQUIRED`/`REVIEW_INCOMPLETE`/`FAIL`/
      `RELEASED`; no code path can still produce the old `PASS`/
      `HUMAN_VISUAL_REVIEW_REQUIRED` strings.
- [ ] `AUTOMATED_PASS` is documented and treated in code as non-terminal —
      nothing downstream reads it as "safe to ship."
- [ ] A synthetic encrypted PDF run produces `release_status FAIL` with an
      explanatory message naming encryption as the cause; no traceback, no
      password prompt, no decryption code path anywhere in the tool.
- [ ] A forced Tesseract call exceeding 120 seconds (mocked) fails that page
      in a controlled way rather than hanging.
- [ ] A forced Ghostscript call exceeding 30 minutes (mocked) fails the run in
      a controlled way rather than hanging.
- [ ] `git push` runs the full unit suite and `tools/eval_sanitizer.py` via a
      pre-push hook and is blocked on failure of either.
- [ ] New behavior is covered by tests following the existing
      synthetic-PDF-in-memory pattern in `tests/test_anonymize_construction_pdfs.py`.
