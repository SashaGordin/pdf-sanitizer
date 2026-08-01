Status: ready-for-agent

# Release Integrity Hardening

Scope: `PRODUCTION-READINESS-PLAN.md` immediate backlog items 0–6 (Phase 0,
Phase 1, and the raster/vector parity slice of Phase 2). Everything else in
the plan is explicitly deferred — see Out of Scope.

## Problem Statement

Right now, when the sanitizer says a document's redaction succeeded, that
claim can't be trusted, and there's no way to tell from the report alone
whether it still describes the file sitting on disk.

Two things can go wrong silently:

1. A page can be visually readable to a person while every automated check
   reports clean, because the checks read the same text stream that was
   already scrubbed rather than the rendered pixels a viewer actually sees.
   Content embedded in an image, converted to outlines, or otherwise absent
   from the text stream never gets looked at.
2. A report can say `PASS` about a PDF that no longer exists in that form.
   Outputs, reports, the denylist, lexicons, and code all change
   independently, and nothing ties a report to the exact code, policy, and
   file hash it was produced against. A stale, misleadingly-green report can
   sit beside a completely different, newer PDF with no automatic way to
   notice.

Today, the only way to catch either failure is a full manual eyeball of
every page and a by-hand comparison of file hashes — an approach that
doesn't scale past a handful of pages and leaves no durable proof after the
fact that a given release was ever actually checked.

## Solution

Rework the tool so that:

- Every source and output page is rendered and OCR'd as an independent
  detection/verification surface, catching sensitive content the text
  extractor cannot see.
- The raster (scanned-page) and vector (searchable-page) processing paths
  enforce the exact same redaction policy, so page type never changes what
  counts as sensitive.
- Every run publishes into its own immutable, atomically-written directory
  identified by a run ID, so a PDF and the report describing it can never
  drift apart or be ambiguous about which is current.
- Every report is stamped with a fingerprint of the exact code, denylist,
  config, allowlist, and lexicons used, so staleness can be detected by a
  tool instead of a human comparing files by hand.
- The tool fails closed — a controlled `FAIL`, never a crash, a hang, or a
  silent skip — on encrypted input, subprocess timeouts, and incomplete
  detection.
- The single ambiguous `PASS` is replaced with five explicit statuses
  (`AUTOMATED_PASS` / `REVIEW_REQUIRED` / `REVIEW_INCOMPLETE` / `FAIL` /
  `RELEASED`), where only `RELEASED` — automated gates clean **and** human
  visual review complete — means a file is safe to hand off.

## User Stories

**Source provenance & CI**

1. As an operator, I want the repository under version control with a
   defined tracked-file set, so that source changes are attributable and
   the ignore rules can't accidentally include confidential material or
   exclude code that should be reviewed.
2. As an operator, I want a local pre-push git hook that runs the full unit
   suite and the golden-set eval, so a regression can never be pushed past
   it silently.
3. As a future maintainer, I want every run manifest to record the exact
   source commit (or a build digest when no commit is available), so any
   past run's exact code can be reproduced or audited.

**Release status vocabulary**

4. As an operator, I want the report to use one of five explicit statuses
   instead of a single ambiguous `PASS`, so I can never mistake an
   automated-only result for something safe to hand off.
5. As an operator, I want `AUTOMATED_PASS` to never be treated as a terminal
   or shippable state, so a file can't leave my machine without my own
   visual review having happened and been recorded.

**Rendered-page OCR verification (in-pipeline)**

6. As an operator, I want every output page rendered and OCR'd as part of
   the pipeline's own verification gate, so content invisible to the text
   extractor (an image, outlined glyphs, a scanned region) can't silently
   survive a redaction pass.
7. As an operator, I want the rendered-page OCR pass to run on every page
   regardless of whether that page already has plenty of searchable text,
   so a mixed page (some vector text, some sensitive content inside an
   embedded image) isn't skipped just because it looks like a normal text
   page.
8. As an operator, I want the in-pipeline verifier's rendered-page OCR to
   block `AUTOMATED_PASS` on any unresolved match, so a mixed-page leak
   becomes a `FAIL`/`REVIEW_REQUIRED`, not a silent pass.

**Mixed-page remediation**

9. As an operator, I want the searchable/scanned page classification
   (currently a raw character-count threshold) replaced with one that
   accounts for raster content on an otherwise text-bearing page, so a page
   with a little vector text and a sensitive scanned image isn't
   misclassified as "searchable, skip raster detection."
10. As an operator, I want a sensitive value found only in pixels or
    graphics redacted by redacting those pixels directly, or by
    rasterizing the whole affected page when coordinate confidence is too
    low, so no readable identifier survives just because it wasn't
    extractable as text.

**Raster/vector parity**

11. As an operator, I want the raster (OCR-driven) detection path to use the
    exact same lexicon, suppression, and label-following-line logic as the
    vector (text-extraction-driven) path, so a scanned page and a
    searchable page are held to the identical redaction policy.
12. As an operator, I want a suppressible false positive found during
    raster verification to fail only that page (or force it to
    `REVIEW_REQUIRED`), never abort the entire multi-hundred-page run, so
    one noisy scanned page can't take down an otherwise-successful
    1,475-page job.

**Policy fingerprint & verify-existing**

13. As an operator, I want every report to include hashes of the code,
    denylist, project metadata, configuration, allowlist, and each lexicon
    file used to produce it, so I can tell whether a report still describes
    the current policy without comparing files by hand.
14. As an operator, I want a standalone `verify-existing` command that
    re-checks an already-produced run directory against the *current*
    denylist/lexicons/policy and reports whether it's still current or now
    stale, so I don't have to manually reconstruct which report matches
    which output.
15. As an operator, I want `verify-existing` to keep fully independent
    extraction, matching, and rendering code from the pipeline's own
    verifier, so a shared blind spot between detection and verification
    can't produce a false pass in either one.

**Atomic run packaging**

16. As an operator, I want every run to publish into its own immutable
    directory (sanitized PDF, report, manifest together), written
    atomically, so a PDF and the report describing it can never become
    mismatched or ambiguous about which is current.
17. As an operator, I want old run directories to never be overwritten by a
    rerun, so I can always go back and compare exactly what a previous run
    produced.
18. As an operator, I want every failed run — including a crash partway
    through — to still publish a failure record, so a failed run never
    leaves a stale, previously-successful report sitting as the only thing
    there.
19. As an operator, I want the existing stale artifacts in the current
    shared output directory (the `PASS` report describing hashes that no
    longer exist, the hand-renamed backup denylist, the retired-naming
    -scheme PDF) deleted as part of this work, so nothing misleading is
    left on disk once the new layout exists.

**Fail-closed input handling**

20. As an operator, I want an encrypted PDF to produce a controlled `FAIL`
    explaining that the input is encrypted, so I get an actionable message
    instead of an unhandled traceback.
21. As an operator, I want the sanitizer to never attempt to decrypt a
    password-protected PDF itself, so no decryption password ever has to be
    handled, stored, or passed through this tool.
22. As an operator, I want every subprocess call (Tesseract, Ghostscript) to
    enforce a timeout with a fail-closed result, so a pathological or
    corrupt input can't hang a run indefinitely.
23. As an operator, I want a Tesseract call that exceeds 120 seconds on a
    single page to fail just that page's OCR, not hang the whole run.
24. As an operator, I want a Ghostscript flatten call that exceeds 30
    minutes for the whole document to fail that run in a controlled way, so
    a hang doesn't consume resources indefinitely.
25. As an operator, I want the hard-coded
    `raster_ocr_from_sanitized_images_only: true` report field removed
    rather than measured separately, so the report doesn't contain a field
    that looks verified but isn't.

**Domain/policy**

26. As an operator, I want an architect-of-record's firm name and location
    redacted by default like any other identifying detail, so ambiguity
    about "is this an intentional disclosure" doesn't leave a real
    identifier in a sanitized deliverable.

## Implementation Decisions

- The five-status vocabulary (`AUTOMATED_PASS`, `REVIEW_REQUIRED`,
  `REVIEW_INCOMPLETE`, `FAIL`, `RELEASED`) replaces the current single
  automated-checks pass/fail string as the top-level `release_status` for
  both the per-document report and the overall run. `AUTOMATED_PASS` is
  never a terminal status; only `RELEASED` (automated gates clean **and**
  human review complete) is safe to hand off.
- A new run manifest is produced per run, containing: run ID and
  timestamps; source and output SHA-256 hashes; source commit identity or a
  build digest when no commit is available; denylist, project-metadata,
  config, allowlist, and lexicon file hashes; OCR/PDF/barcode/NER-model/
  Ghostscript/dependency versions; page counts and processing statistics;
  automated-gate results; and review status/reviewer/completion fields
  (populated once visual review is done — no reviewer UI is in scope, only
  the manifest field to record it).
- Run directory layout: each run publishes into its own directory
  (sanitized PDF, machine-readable report, human-readable review summary,
  manifest together), written to a temporary location and atomically
  renamed into place (per ADR-0002). Old run directories are retained,
  never overwritten by a rerun.
- A new orchestration function sits between the CLI entrypoint and the
  per-document sanitize call: it generates the run ID, computes the policy
  fingerprint, invokes the per-document sanitizer for each input, aggregates
  per-document statuses into an overall run status, writes the manifest,
  and performs the atomic publish. On any exception or failure partway
  through, it still writes a failure record into that run's directory
  rather than leaving no report at all.
- The per-document sanitize function gains:
  - An encrypted-input check immediately after opening the source, before
    any page-dependent operation, producing a controlled `FAIL` rather than
    letting the underlying library's exception propagate as a traceback.
  - Timeouts on the Tesseract call (120 seconds, invoked once per page) and
    the Ghostscript call (30 minutes, invoked once per whole document),
    both fail-closed to a controlled per-page or per-run `FAIL` rather than
    hanging or crashing.
  - A reworked mixed-page classification that accounts for raster/image
    content on an otherwise text-bearing page (replacing the current flat
    character-count threshold), forcing raster detection to run on such
    pages instead of skipping them.
  - Raster-path detection gaining the same lexicon, suppression, and
    label-following-line logic the vector path already has, so both paths
    enforce one policy.
  - A suppressible false positive discovered during raster verification now
    fails only the affected page rather than raising an error that aborts
    the entire run.
  - Removal of the hard-coded `raster_ocr_from_sanitized_images_only`
    report field (not replaced with a separate measurement — the
    rewritten verifier design below makes the property it claimed true by
    construction).
- The in-pipeline verifier gains a rendered-page OCR pass: it renders every
  output page — regardless of that page's text/scanned classification — at
  a documented OCR resolution, OCRs the full page, and runs the same
  unified policy as detection against the OCR text, in addition to its
  existing structural checks (page count/size, metadata, annotations,
  links, forms, bookmarks, attachments, optional-content groups) and
  existing extracted-text policy scan. Any unresolved match from any of
  these checks blocks `AUTOMATED_PASS`. Uses a single rendering engine
  (PyMuPDF) for this cycle; a second independent rendering engine is out of
  scope.
- The standalone verifier tool grows from a denylist-only text scanner into
  the `verify-existing` command: it gains the same unified policy
  (lexicons, suppression rules, label-following-line logic) the pipeline
  enforces, plus its own independent render-and-OCR pass implemented with
  genuinely separate code from the pipeline's rendering/OCR/matching calls,
  so the two verifiers cannot share a blind spot. It's pointed at a run
  directory (not a bare PDF path), and re-derives whether that run's report
  is current by recomputing the same fingerprint the manifest recorded and
  comparing it against the live denylist/config/lexicons/code.
- Encrypted-input handling has no password argument and no decryption code
  path anywhere in the tool — detection only, always a hard fail.
- Stale-artifact cleanup: the current shared output directory's existing
  contents (all current reports and sanitized PDFs, the backup denylist
  file, the retired-naming-scheme PDF) are deleted once the new run
  -directory layout is in place. This is a one-time cleanup, not an ongoing
  retention policy.
- CI: a local git pre-push hook runs the full unit test suite and the
  golden-set eval script, blocking the push on failure. No hosted CI or git
  remote is configured in this cycle.
- The architect-of-record firm name/location is added to (or confirmed
  already covered by) the denylist/lexicon content so it's redacted like
  any other identifying detail.

## Testing Decisions

- A good test here asserts on externally observable behavior — the
  report's status field, the manifest's contents, whether a rendered-OCR
  pass catches a planted match, the run directory's contents and
  immutability — never on internal call counts or which private helper
  fired.
- The per-document sanitize function (extended): tested the same way the
  existing suite already does — build a small synthetic PDF in-memory,
  run it through the function directly, assert on the returned report dict
  and the re-opened output PDF's content. New cases needed: an encrypted
  synthetic PDF (controlled `FAIL`, no traceback); a mixed page (vector
  text plus an embedded image containing a denylisted term) proving it's
  now caught; a raster page with a suppressible false positive (only that
  page fails, not the whole run); a Tesseract/Ghostscript call forced past
  its timeout (fail-closed, not a hang — via mocking the subprocess call to
  simulate a timeout).
- The in-pipeline verifier: extend the existing verifier tests with a case
  where a sensitive value exists only in an embedded raster image on an
  otherwise text-heavy page — assert it's caught by the new rendered-OCR
  pass even though the existing extracted-text scan would miss it. This is
  the specific proven leak class from the current-state assessment.
- `verify-existing`: new tests analogous to the existing golden-set test
  pattern but exercised through the grown standalone tool — build a run
  directory with a known-current fingerprint and assert "current," then
  mutate the denylist/config and assert the same run directory now reports
  "stale" without a human comparing hashes by hand. This is the concrete
  regression test for the exact scenario in the current-state assessment
  (days of drift producing a plausibly-green report).
- Run publishing/manifest: new tests asserting two consecutive runs against
  the same inputs produce two distinct run directories (never overwriting),
  that a forced failure partway through a run still produces a failure
  record in its own run directory, and that the manifest's recorded hashes
  match the actual on-disk denylist/config/lexicon file contents.
- Prior art: the existing page-scope, file-path-pattern, and
  suppression-symmetry regression tests are exactly the
  "previously-discovered-leak becomes a permanent regression test" pattern
  this work should extend — every new leak class found while building this
  (mixed-page, stale-report drift) gets its own permanent test in that same
  style. The existing golden-set test remains the fast policy-vocabulary
  regression gate and should keep passing unmodified.
- The local pre-push git hook runs exactly this suite (the full pytest
  suite plus the golden-set eval script), so enforcement stays the same set
  of checks whether run locally or, eventually, in a hosted pipeline.

## Out of Scope

- Everything not in backlog items 0–6 of `PRODUCTION-READINESS-PLAN.md`:
  the locked generalization corpus (Phase 3), the human review
  workflow/tooling (Phase 4), operational/software security hardening —
  private file permissions, encrypted-at-rest storage, worker isolation,
  dependency locking/SBOM (Phase 5) — and the pilot/launch process
  (Phase 6).
- Per-label detection caps, intake-completeness gating, and
  review-completeness gating (all explicitly P1, not P0).
- A second independent rendering engine for either verifier (tracked as a
  follow-up once the single-renderer verifier is proven).
- Hosted CI / a git remote — deferred until there's an actual remote or
  collaborator to justify it.
- Any password/decryption handling for encrypted input — permanently out
  of scope by design, not just deferred this cycle.
- A retention/cleanup policy for accumulating run directories over time
  (Phase 5 concern) — this cycle performs only a one-time cleanup of the
  pre-existing stale artifacts.
- Resolving the AGPL licensing posture with formal legal review, and the
  NER review-qualification tickets — both explicitly tracked separately,
  non-blocking for this cycle.
- Worker/process isolation and resource quotas beyond the specific
  subprocess timeouts named above (the plan's broader file-size/page-count/
  rendered-pixel/object-count intake limits) — deferred along with the
  local-cli-only, single-operator deployment model.

## Further Notes

- This spec covers exactly the plan's own recommended next-cycle scope —
  its final recommendation is to focus on backlog items 0 through 6 and
  explicitly not spend this cycle tuning regexes or NER thresholds.
- Two verification code paths are being extended, not merged: the
  in-pipeline verifier and the standalone `verify-existing` tool are kept
  deliberately independent (separate rendering, OCR, and matching
  implementations) per the current-state assessment's explicit note that
  this independence is what caught a real leak the pipeline's own verifier
  missed. Don't simplify this into one shared verifier later without
  re-reading that finding.
- Relevant prior decisions: ADR-0001 (local-CLI-only deployment) and
  ADR-0002 (per-run-ID output directories) in `docs/adr/`; canonical
  vocabulary (the five release statuses, policy-vocabulary vs.
  document-level recall, the architect-of-record policy default) in
  `CONTEXT.md`.
- The plan's three originally-open decisions are now resolved or explicitly
  deferred: architect-of-record → redact by default; AGPL licensing
  posture and NER review qualification → deferred (see Out of Scope).
