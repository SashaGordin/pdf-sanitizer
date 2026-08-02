# 04 — Atomic run packaging & manifest

**What to build:** Every run publishes into its own immutable directory (per
ADR-0002): the sanitized PDF(s), machine-readable report, human-readable
review summary, and manifest, written to a temporary location and atomically
renamed into place. A new orchestration function sits between the CLI
entrypoint and the per-document sanitize call: it generates the run ID,
computes the policy fingerprint (ticket 02), invokes the per-document
sanitizer for each input, aggregates per-document statuses (ticket 01's
vocabulary) into an overall run status, writes the manifest, and performs the
atomic publish. Old run directories are never overwritten by a rerun. Any
exception or failure partway through a run still results in a failure record
published into that run's own directory — no path leaves a stale,
previously-successful report as the newest thing on disk. As part of this
work, the existing stale artifacts in the current shared `output/pdf/`
directory (the `PASS` report describing hashes that no longer exist, the
hand-renamed backup denylist, the retired-naming-scheme PDF) are deleted — a
one-time cleanup, not an ongoing retention policy.

**Blocked by:** 01 (manifest carries the release-status vocabulary), 02
(manifest carries the fingerprint hashes).

**Status:** done

- [x] Two consecutive runs against the same inputs produce two distinct
      run-ID directories; neither overwrites the other.
- [x] A forced failure partway through a run (mocked exception) still
      produces a failure record in its own run directory rather than no
      report at all.
- [x] The manifest contains run ID, timestamps, source/output SHA-256 hashes,
      source commit identity or build digest, the ticket-02 fingerprint
      hashes, OCR/PDF/barcode/NER-model/Ghostscript/dependency versions, page
      counts and processing statistics, automated-gate results, and review
      status/reviewer/completion fields (populated once a human records
      review — no reviewer UI in scope).
- [x] Publishing is atomic: the run directory only ever appears fully-formed
      (temp-write then rename), never partially written at its final path.
- [x] The pre-existing stale artifacts named above are deleted from the
      current shared output directory as part of this ticket landing —
      confirm with the user immediately before deleting, since this is
      destructive.
- [x] Existing tooling that assumed fixed paths under `output/pdf/` (e.g. any
      ad hoc invocations of `tools/verify_output_text.py`) is updated to
      operate against a run directory instead.

## Comments

Implementation and automated coverage completed 2026-08-01. A fresh 43-page
run published atomically at `output/runs/20260801T231421.035584Z-a6b4b0d9`.
The user confirmed the one-time legacy cleanup immediately before deletion;
the seven fixed-path output artifacts and backup denylist were removed.
