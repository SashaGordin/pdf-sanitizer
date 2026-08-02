# 06 — verify-existing standalone command

**What to build:** `tools/verify_output_text.py` grows from a denylist-only
text scanner into the `verify-existing` command: it gains the same unified
policy (lexicons, suppression rules, label-following-line logic) the pipeline
enforces, plus its own independent render-and-OCR pass implemented with
genuinely separate code from the pipeline's rendering/OCR/matching calls, so
the two verifiers cannot share a blind spot. It's pointed at a run directory
(not a bare PDF path) and re-derives whether that run's report is current by
recomputing the same fingerprint (ticket 02) the manifest (ticket 04)
recorded and comparing it against the live denylist/config/lexicons/code.

**Blocked by:** 02 (needs the fingerprint format to detect staleness), 04
(needs a run directory to point at).

**Status:** done

- [x] `verify-existing` accepts a run directory and reports each document's
      status against the current policy, not just denylist terms.
- [x] It recomputes the ticket-02 fingerprint from the live
      denylist/config/lexicons/code and compares it against the run's
      recorded manifest fingerprint.
- [x] Given a run directory with a fingerprint matching the current policy,
      it reports "current."
- [x] Given the same run directory after the denylist or config is mutated,
      it reports "stale" without any manual hash comparison.
- [x] Its rendering, OCR, and text-matching implementation remains genuinely
      independent code from the pipeline's own (no shared helper functions
      for extraction/rendering/matching) — call this out explicitly in a
      code comment or test so it isn't "simplified" away later.
- [x] Re-running `verify-existing` against the artifacts described in
      `CURRENT-STATE-ASSESSMENT-2026-08-01.md` reports them as
      stale/unreleasable.

## Comments

Completed 2026-08-01. The command rejects legacy directories without a
manifest as unreleasable. Against fresh run
`20260801T231421.035584Z-a6b4b0d9`, its independent 43-page pass reported a
current fingerprint and artifact, zero extracted or rendered matches, zero
OCR errors, and `AUTOMATED_PASS`.
