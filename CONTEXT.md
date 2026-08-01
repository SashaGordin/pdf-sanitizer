# PDF Sanitizer

Strips personally- and project-identifying information from construction
documents (drawings, specs) before they're shared outside the firm, and
produces a report proving what was found and removed.

## Language

### Release status

A run's outcome is one of five statuses, replacing the old single `PASS`:

**AUTOMATED_PASS**:
All automated gates (structural checks, denylist scan, rendered-page OCR
scan, etc.) are clean. This is an internal checkpoint only — it is never
itself a safe-to-ship status. A deliverable cannot leave the machine on the
strength of `AUTOMATED_PASS` alone; it must still become `RELEASED`.
_Avoid_: PASS (ambiguous — doesn't say whether a human reviewed it).

**REVIEW_REQUIRED**:
Automated gates are clean, but the mandatory human visual review has not yet
started.

**REVIEW_INCOMPLETE**:
Human review started but was not finished or signed off — the same kind of
incompleteness signal as `residuals_truncated`/`findings_truncated` elsewhere
in the report.

**FAIL**:
An automated gate failed, a required detector errored/timed out/truncated, or
an unsupported input condition was hit (e.g. encrypted PDF). Never silently
downgraded to a lesser status.

**RELEASED**:
Automated gates are clean **and** human review is complete and signed off.
The only status safe to hand off as a deliverable.
_Avoid_: Using "released" loosely for any output file that exists on disk —
an output only earns this status once every gate and the review are done.

### Recall metrics

**Policy-vocabulary recall**:
What `tools/eval_sanitizer.py` measures today: whether the denylist/lexicon
matcher correctly matches a fixed set of bare label strings
(`tests/golden/mlk_labels.json`). It never opens a PDF, so it says nothing
about extraction, redaction, or rendering.
_Avoid_: "recall" unqualified — always say which kind.

**Document-level recall** (a.k.a. end-to-end recall):
Whether a sensitive value present in an actual source PDF survives all the
way through to the sanitized output. This is the number people mean when they
say "recall," and it doesn't exist yet — it's what Phase 3's locked
generalization corpus is meant to produce.

### Policy defaults

**Architect of record**:
The firm stamped/named as the responsible design professional on a drawing or
spec sheet. Treated as sensitive by default: its name and location are
redacted like any other identifying detail, not preserved as an
assumed-intentional disclosure. (Resolves the plan's open
"ambiguous architect-of-record case" — when in doubt, redact.)
