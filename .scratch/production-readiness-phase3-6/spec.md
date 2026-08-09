Status: ready-for-agent

# Production Readiness: Phases 3–6

Scope: `PRODUCTION-READINESS-PLAN.md` phases 3 (locked generalization corpus),
4 (human review and learning loop), 5 (operational and software security),
and 6 (controlled pilot and production launch), plus the three remaining
Phase 2 P1 items (per-label caps, review-completeness gate,
intake-completeness gate) that the wayfinder map explicitly grouped in with
this work rather than ticketing separately. Everything not named here is
explicitly deferred — see Out of Scope.

This is the phases-3–6 counterpart to the closed
`.scratch/release-integrity-hardening/` spec, which covered phases 0–1 and the
raster/vector-parity slice of phase 2. That spec's tickets are all closed.

## Problem Statement

Phases 0–1 made a redaction claim trustworthy and current: a report can now
prove which code and policy produced it, rendered-pixel content is checked
alongside the text stream, and a stale report can no longer sit unnoticed
beside a newer PDF. Three problems remain before the tool can defensibly go
to production:

1. Detector coverage is still measured against the same corpus every
   detector, lexicon, and denylist term was tuned on. "100% recall" today
   means the matcher matches 95 known strings — it says nothing about an
   unseen document, and there is no locked, held-out corpus to measure that.
2. Human review — the control that is supposed to catch what automation
   misses — has a fully validated design (three prototype iterations, signed
   off) but no real tool. Nobody can actually run a review today.
3. A handful of operational loose ends remain open: the report still
   contains a technically-reversible field, there is no resource ceiling
   beyond the timeouts phase 0 already added, nothing prunes accumulating
   run/triage artifacts, and dependencies are still range-specified rather
   than pinned.

None of these are open design questions anymore. Wayfinder map issue #2 spent
nine child tickets (research, grilling, and two rounds of HITL prototyping)
resolving every decision that was blocking this work: locked-corpus
composition and labeling schema, the reviewer workflow's exact shape, Phase
5's scope against the tool's actual single-operator deployment, the Phase 6
pilot sequence, AGPL licensing posture, and the NER re-triage framework. What
remains is turning those decisions into build tickets.

## Solution

- Close the report's last reversible-value leak by replacing `masked_shape()`
  with a keyed digest as the default report field.
- Add the resource ceilings, artifact cleanup/retention, and dependency
  pinning that round out Phase 5's operational posture for this tool's actual
  (fully-trusted, internal, non-distributed) deployment shape.
- Finish the three Phase 2 P1 items already fully specified in
  `PRODUCTION-READINESS-PLAN.md`'s own text: per-label detection caps, the
  review-completeness gate, and the intake-completeness gate.
- Build the real corpus-labeling tool and the real reviewer tool — both
  already validated through HITL prototyping — as a static page plus a small
  localhost-only server that gives them real persistence in place of the
  prototypes' browser-download stand-in.
- Assemble the locked generalization corpus from the real documents already
  sourced (issue #10) plus targeted synthetic fill, and build a document-level
  regression harness that reports recall against real pages, distinct from
  the existing policy-vocabulary metric.
- Run the Phase 6 dry run — one workflow exercise of the full pipeline against
  the locked corpus, compared against the client's own annotations — and walk
  the production go/no-go checklist item by item against its results.

## User Stories

**Report keyed-digest fix**

1. As an operator, I want the report to use a keyed digest instead of a
   reversible masked shape for every redacted value, so sending the sanitized
   PDF and its report to the same external cloud AI system can't let someone
   reconstruct a value by matching shapes against the output text.
2. As an operator, I want that digest to be stable within one run's report
   (so repeated occurrences of the same value correlate) but not reproducible
   across runs and not guessable without the run's key, so it serves
   correlation without becoming a new reversible channel.
3. As an operator, I want `masked_shape()` kept only for the reviewer-local
   in-tool crop view, and never present in the report or manifest that
   travels with the output, so the one place it's still useful doesn't
   reintroduce the leak everywhere else.

**Resource limits, retention, and dependency pinning**

4. As an operator, I want the per-document worker to enforce a memory and CPU
   ceiling, not just the existing Tesseract/Ghostscript timeouts, so a
   pathological input that doesn't hang can't still exhaust the machine.
5. As an operator, I want a disk-usage ceiling on the per-run staging
   directory, so a malformed or adversarial input can't fill the disk before
   any timeout fires.
6. As an operator, I want any resource-limit breach to produce the same
   controlled `FAIL` path the existing subprocess-timeout handling already
   established, so there's one failure shape for "this run can't safely
   continue," not a second ad hoc one.
7. As an operator, I want staging and triage-crop directories deleted
   automatically once a run completes successfully, so confidential derived
   artifacts (crops that may contain residual identifiers) stop accumulating
   with only a README instruction telling someone to delete them by hand.
8. As an operator, I want a configurable retention window that prunes run
   directories older than N days, with an explicit default rather than an
   unset value, so historical runs don't accumulate indefinitely on disk.
9. As an operator, I want the crash-recovery/abandoned-run cleanup path
   explicitly *not* built (per issue #8's "light version" scope), so this
   ticket doesn't grow into a startup-recovery feature nobody asked for.
10. As an operator, I want dependencies pinned to exact, hash-verified
    versions in a lockfile generated from the existing
    `requirements-anonymizer*.txt` range specs, with a documented
    regeneration command, so a fresh install can't silently resolve a
    different PyMuPDF/Pillow/GLiNER version than a report's provenance
    claims.

**Phase 2 P1 completion (per-label caps, review-completeness, intake-completeness)**

11. As an operator, I want the global `max_findings` cap replaced with
    per-label caps, so a document full of `organization` matches can't crowd
    out a handful of high-value `street address` or `city` findings.
12. As an operator, I want `residuals_truncated > 0` or
    `findings_truncated > 0` to block `RELEASED`, so a review list that was
    silently cut off can never be signed off as complete just because it
    stayed under a display cap.
13. As an operator, I want a run with no `--project-metadata` file, or one
    with empty intake fields, to fail an explicit intake-completeness gate
    unless a waiver is recorded, so nothing reaches `RELEASED` on an
    unverified "nothing else to redact" assumption.
14. As an operator, I want the project-metadata file's hash, and the list of
    empty intake fields (or the recorded waiver), written into the manifest,
    so intake completeness is auditable after the fact, not just enforced at
    run time.

**Real corpus-labeling tool**

15. As the operator labeling the locked corpus, I want a tool that draws a
    bounding box directly on a real rendered PDF page and tags it inline with
    category, sensitivity decision, expected disposition, and an optional
    note, so I can label real sourced documents instead of hand-editing JSON.
16. As that operator, I want the page shown to be a real rendered page from
    the actual corpus document, not a synthetic placeholder, so labels are
    grounded in what the sanitizer will actually see.
17. As that operator, I want a running, editable/removable list of labeled
    items alongside the page, so I can review and correct labels within a
    session without re-drawing boxes.
18. As that operator, I want the "Export" action to write the labeled-items
    file straight to disk via the tool's local server, so I don't have to
    manually move a browser download into place.
19. As that operator, I want no raw JSON/export-schema ever visible on the
    working screen, so the labeling session stays focused on the document,
    not the internal data shape.

**Locked corpus and document-level regression suite**

20. As an operator, I want the locked corpus assembled from the 8 real
    documents sourced in issue #10 (~2,600 pages across 7 agencies), so the
    corpus reflects real, publicly-available construction documents rather
    than only the MLK development corpus every detector was already tuned
    against.
21. As an operator, I want synthetic documents filling exactly the dimension-
    table cells no sourced real document can supply — encrypted, malformed,
    and non-English-language PDFs — so the corpus covers the full dimension
    table in `PRODUCTION-READINESS-PLAN.md` Phase 3 without pretending a real
    source exists where it doesn't.
22. As an operator, I want every labeled item in the corpus to carry a
    bounding box, category, sensitivity decision, and expected disposition,
    replacing the current 95 string-level `mlk_labels.json` entries with
    page-level ground truth.
23. As an operator, I want a document-level regression harness, alongside
    (not replacing) the existing `tools/eval_sanitizer.py`, that runs the
    real sanitizer against the locked corpus and scores recall/over-
    redaction per document.
24. As an operator, I want document-level recall reported as a distinct
    metric from policy-vocabulary recall everywhere it appears, so nobody
    can mistake "the matcher matches 95 known strings" for "the sanitizer
    catches identifiers in a real document."
25. As an operator, I want results reported separately by document type,
    detection surface, category, language, and image quality, so a
    regression in one slice can't hide inside one aggregate percentage.
26. As an operator, I want every leak discovered while building against the
    locked corpus to become a permanent regression test in the existing
    `GoldenSetTest`-style pattern, so no locked-corpus finding is a one-time
    fix.

**Real reviewer tool and promotion workflow**

27. As a reviewer with zero codebase context, I want an inbox-style tool — a
    list of flagged items on the left, the selected item's real crop plus a
    plain-language guess ("looks like an address") on the right — so I can
    review findings without needing to know how the detector works.
28. As that reviewer, I want to make exactly one of four plain-language calls
    per item ("Yes, this is sensitive" / "No, fine to show" / "We've already
    flagged this" / "Not sure, ask someone else"), plus an optional free-text
    note on every item, so my review vocabulary matches what was already
    validated in the prototype, not a re-litigated set of options.
29. As that reviewer, I want my disposition to write to a real
    `decisions.json`, tied to the run's output hash, via the tool's local
    server, so the decision is durable and auditable instead of living only
    in a browser tab that closes.
30. As an operator, I want a separate ops-facing view — not shown to the
    reviewer — that previews what a "we've already flagged this" disposition
    would add to the denylist and what a "fine to show" disposition would
    propose for a scoped lexicon rule, so policy promotion stays a
    deliberate, separately-reviewed step and never applies automatically.
31. As an operator, I want the promotion-preview logic ported from
    `tools/prototype_reviewer_triage.py`'s already-schema-accurate
    implementation rather than reimplemented from scratch.

**Phase 6 pilot dry run and go/no-go walkthrough**

32. As an operator, I want to run the full pipeline once against the
    assembled locked corpus, comparing automated findings against the
    client's own labeler-tool annotations, so the Phase 6 dry run (which
    collapses shadow mode and the confidential-pilot comparison steps into
    one workflow exercise on public documents) has real comparison data.
33. As an operator, I want that comparison to record reviewer-only catches
    and the disagreement rate between automated findings and annotations, so
    the metrics `PRODUCTION-READINESS-PLAN.md` names for the first pilot
    exist from the very first run, not retrofitted later.
34. As the single-operator go/no-go authority, I want the production
    go/no-go checklist walked item by item against the dry run's actual
    results, with each item's current true/false state recorded in the
    ticket, so "go" is a documented decision, not an impression.
35. As an operator, I want the real confidential pilot (Phase 6 steps 2 and
    5–6) to stay explicitly unscheduled pending an actual paying client
    engagement, so this ticket doesn't invent a confidentiality test the
    business doesn't have a real case for yet.

## Implementation Decisions

- **Report digest.** Replace `masked_shape()` as the default value in every
  report field that currently carries it (`residuals`, `ner_review.findings`)
  with an HMAC-SHA256 digest keyed by a random 256-bit value generated fresh
  per run. The key is held only in memory for the duration of the run and is
  never written to the report or the manifest — this gives within-run
  correlation (the same value hashes the same way across occurrences in one
  report) without cross-run reproducibility or a stored key an attacker could
  use to test candidate values. `masked_shape()` remains available only for
  the reviewer-local in-tool crop view; it must not appear in anything that
  leaves the machine alongside the output.
- **Resource limits.** Wrap the per-document worker with `resource.setrlimit`
  (`RLIMIT_AS`/`RLIMIT_RSS` where the platform supports it, `RLIMIT_CPU`), and
  add an explicit disk-usage check on the per-run staging directory polled
  during processing. A breach of any limit produces the same controlled
  `FAIL` result shape the existing Tesseract/Ghostscript timeout handling
  already produces (ticket 01 of the closed spec) — no new failure taxonomy.
- **Cleanup and retention.** On successful completion of a run (terminal
  `AUTOMATED_PASS`/`RELEASED` path only — explicitly not on failure or crash,
  matching issue #8's "light version, skip startup-recovery-for-abandoned-
  runs" scope), delete that run's staging directory and triage-crop
  directory. Separately, add a retention window (default: a stated number of
  days, configurable) that prunes entire run directories older than the
  window, run as an explicit maintenance step rather than automatically on
  every invocation.
- **Dependency pinning.** Generate a hash-pinned lockfile (e.g. `pip-compile
  --generate-hashes`) from the existing `requirements-anonymizer.txt` and
  `requirements-anonymizer-ner.txt` range specs. Check the lockfile in and
  document the regeneration command. No SBOM or vulnerability-scanning
  program — matches issue #8's explicitly light scope.
- **Per-label caps.** Replace the single global `max_findings` integer with a
  per-label cap dict, so low-volume high-value labels aren't crowded out by
  high-volume ones (`PRODUCTION-READINESS-PLAN.md` line ~379).
- **Review-completeness gate.** `residuals_truncated > 0` or
  `findings_truncated > 0` on any document blocks that run from reaching
  `RELEASED` (plan lines ~387–388) — truncation must be surfaced as an
  incompleteness signal, not merely counted.
- **Intake-completeness gate.** Require `--project-metadata` and hash it into
  the manifest; if the file is missing or has empty fields, either fail this
  gate or record an explicit waiver (with the empty-field list) rather than
  proceeding silently (plan lines ~186–190, ~609–611).
- **Corpus-labeling tool and reviewer tool** both take the same shape: the
  validated prototype's static HTML page (interaction/content model
  unchanged from the HITL-approved design in issues #7 and #11) plus a small
  Python `http.server`-based local launcher, matching the existing
  `tools/*.py` launcher convention. The launcher is extended, relative to the
  throwaway prototypes, to *serve* real rendered page crops (reusing
  `render_residual_crop()` from `tools/anonymize_construction_pdfs.py`) and
  to *accept* POSTed exports, writing them directly to
  `.scratch/corpus/labels/<doc-id>.json` (labeler) or a run's
  `decisions.json` (reviewer) — replacing the prototypes' browser-download
  stand-in with a real write. The server binds to `localhost` only and is
  launched by the operator on their own machine; this does not change
  ADR-0001's "not a network service" status.
- **Locked corpus composition.** Assemble from the 8 documents identified in
  issue #10 (2 independently portal-sourced, 6 client-provided, ~2,600 pages
  across 7 agencies/jurisdictions) plus synthetic documents built to cover
  exactly the three dimension-table cells no public source supplies:
  encrypted PDFs, malformed PDFs, and non-English-language documents. The
  Missouri school-district document's explicit reproduction-restriction
  notice is flagged for counsel review before use in the corpus — this
  ticket does not resolve that, only flags it (same treatment as the AGPL
  ticket's open nuance).
- **Labeling schema.** Every corpus item carries bounding box, category,
  sensitivity decision, and expected disposition — the step-up in
  granularity issue #5 locked in, replacing the current 95 string-level
  `tests/golden/mlk_labels.json` entries.
- **Document-level regression harness.** New module alongside (not
  replacing) `tools/eval_sanitizer.py`; runs the real sanitizer end-to-end
  against locked-corpus documents and scores recall/over-redaction per
  document, category, and detection surface. Reported as "document-level
  recall," never conflated with the existing "policy-vocabulary recall"
  metric `eval_sanitizer.py` already reports (per `CONTEXT.md`'s vocabulary).
- **Promotion-preview logic** in the real reviewer tool is ported from
  `tools/prototype_reviewer_triage.py`'s already-schema-accurate
  implementation (grounded in the real `residuals`/`ner_review.findings`
  shapes), not reinvented. It renders on a separate ops-facing view, never
  shown to the reviewer, and never automatically applies to the denylist or
  lexicons — a human still has to act on the preview.
- **Phase 6 dry run.** One execution of the full pipeline against the
  assembled locked corpus, with the client using the real corpus-labeling
  tool to annotate the same documents; automated findings are compared
  against those annotations to compute reviewer-only catches and a
  disagreement rate. This collapses Phase 6 steps 1–3 (shadow mode,
  confidential pilot, comparison) into the one workflow exercise issue #9
  specified — it is not a confidentiality test, since no confidential client
  documents are involved. The go/no-go checklist in
  `PRODUCTION-READINESS-PLAN.md` is then walked item by item, with each
  item's current state recorded directly in ticket 07, under the single
  operator's go/no-go authority (issue #9, per ADR-0001).

## Testing Decisions

- A good test here asserts on externally observable behavior: the returned
  report/manifest dict, the actual bytes/permissions/existence of files on
  disk, or the content of a written `decisions.json`/labels export — never on
  internal call counts or which private helper fired. This matches the
  standard already set by the closed spec and the existing test suite.
- **Report digest, resource limits, retention, per-label caps, review- and
  intake-completeness:** no new seam. Tested the same way the existing suite
  already does — build a small synthetic PDF in-memory, drive the relevant
  function directly, assert on the returned report/manifest dict and the
  re-opened output. New cases needed: a resource limit forced past its
  ceiling (mocked) produces the controlled `FAIL`, not a crash or hang; a
  synthetic PDF with truncated findings fails to reach `RELEASED`; a run
  missing `--project-metadata` fails intake-completeness or records the
  expected waiver; a mixed high/low-volume finding set proves per-label caps
  don't crowd out the low-volume label.
- **Corpus-labeling tool and reviewer tool (new seam):** the local server's
  write/validate endpoint becomes a plain function
  (`write_label_export(payload, dest_path)`,
  `record_disposition(finding_id, disposition, note, decisions_path)`),
  tested directly with a synthetic payload, asserting on the written JSON
  file's contents — no browser required for this part. The interactive/
  visual side is not re-tested here; it was already validated through HITL
  prototyping (issues #7, #11) including one headless-browser smoke check
  during the labeler prototype, and this ticket doesn't re-litigate that.
- **Document-level regression harness:** validated the same way
  `eval_sanitizer.py` is validated today — against a small fixture slice of
  the locked corpus initially, extended as more of the corpus and its
  discovered leaks accumulate. Every corpus-discovered leak becomes a
  permanent regression test in the existing `GoldenSetTest`-style pattern
  (`PageScopeTest`, `FilePathPatternTest`, `SuppressionSymmetryTest`), per
  the closed spec's Further Notes.
- **Phase 6 dry run:** no synthetic test — verified by the actual pipeline
  run's report/manifest output plus a written comparison summary (automated
  findings vs. client annotations), and by the go/no-go ticket's recorded
  checklist state.
- Prior art: the local pre-push git hook (`tools/git-hooks/pre-push`, from
  the closed spec) already runs the full unit suite and the golden-set eval
  on every push; the new document-level harness and any new unit tests join
  that same enforced gate, not a separate one.

## Out of Scope

- The real confidential pilot and go/no-go execution beyond the checklist
  walkthrough itself — waits on an actual paying client engagement supplying
  confidential documents (issue #9); not scheduled by this spec.
- File/directory permission hardening, log-identifier scrubbing, and
  reproducible-build/provenance tooling — explicitly scoped out by issue #8
  for this fully-trusted, internal-only, non-distributed deployment. (Note:
  file/dir permissions are already `0700`/`0600` as of the closed spec's
  work, contrary to the stale 2026-08-01 assessment — no further action
  needed either way.)
- Encrypted-at-rest storage — issue #14 resolved this as unnecessary for now,
  pending unconfirmed IT/legal facts flagged in that ticket; reopen only if
  those facts change.
- A dedicated backup/restore mechanism — issue #16 resolved this as
  unnecessary; regeneration from the retained source plus pinned manifest
  serves as the restore path.
- Startup recovery for abandoned/crashed runs — explicitly not built; cleanup
  in this spec runs only on successful completion (issue #8).
- AGPL legal review, the subprocess-vs-linked-invocation nuance, and the
  Missouri document's reproduction-restriction notice — all flagged for
  counsel, not resolved by any ticket here (issue #4, issue #10).
- Concrete NER re-triage tickets — issue #6 only defined the template; no
  re-triage ticket (e.g. re-verifying the architect-of-record fix against the
  locked corpus) is created by this spec. That's separate future work, once
  the corpus this spec builds actually exists.
- A second independent rendering engine for either verifier — still deferred,
  unchanged from the closed spec's Out of Scope.
- Any password/decryption handling for encrypted input — permanently out of
  scope by design (unchanged from the closed spec).
- Hosted CI / a git remote-based pipeline beyond the existing local pre-push
  hook — deferred until there's an actual collaborator or remote to justify
  it (unchanged from the closed spec).

## Further Notes

- This spec is the phases-3–6 counterpart to the closed
  `.scratch/release-integrity-hardening/` spec (phases 0–1, plus the
  raster/vector-parity slice of phase 2). That spec's Further Notes documents
  a load-bearing independence rule between the in-pipeline verifier and
  `verify-existing` (separate rendering/OCR/matching code, deliberately not
  merged) — nothing in this spec touches that boundary; do not consolidate
  them while building any of the tickets below.
- Every design decision cited above traces to a specific, closed GitHub
  issue under wayfinder map issue #2: #4 (AGPL), #5 (corpus composition/
  schema), #6 (re-triage template), #7 (reviewer workflow), #8 (Phase 5
  scope), #9 (Phase 6 sequence), #10 (corpus sourcing), #11 (labeling tool),
  #14 (encryption at rest), #16 (backup/restore). This spec adds no new
  design decisions — it only converts existing ones into buildable tickets,
  per the map's own stated destination: "an implementation team could pick
  up any remaining phase and start building without a design conversation
  first."
- Relevant prior decisions: ADR-0001 (local-CLI-only deployment) and ADR-0002
  (per-run-ID output directories) in `docs/adr/`; canonical vocabulary (the
  five release statuses, policy-vocabulary vs. document-level recall, the
  architect-of-record policy default) in `CONTEXT.md`.
- Current-state verification performed while writing this spec (not just
  trusting the 2026-08-01 assessment, since tickets 01–06 landed after it):
  file permissions are already `0700`/`0600`; `--project-metadata` and
  `load_project_metadata()` already exist and feed the denylist. Confirmed
  still open: `masked_shape()` is still the reversible default report field;
  no resource/retention/cleanup limits exist beyond the Tesseract/Ghostscript
  timeouts; no dependency lockfile exists; `max_findings` is still a single
  global cap; no real (non-prototype) reviewer or labeling tool exists
  anywhere — the validated prototypes live only on unmerged branches
  `origin/worktree-prototype-reviewer-triage` and
  `origin/worktree-prototype-corpus-labeler`.
