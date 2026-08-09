# 06 — Real reviewer tool and promotion workflow

**What to build:** Productionize the human-reviewer tool whose shape was
validated through three rounds of HITL prototyping (issue #7): an
inbox-style static HTML page — list of flagged items on the left, selected
item's crop plus a plain-language guess ("looks like an address") on the
right, one of four plain-language dispositions per item ("Yes, this is
sensitive" / "No, fine to show" / "We've already flagged this" / "Not sure,
ask someone else"), plus an optional free-text note on every item — plus a
small Python `http.server`-based local launcher, matching the existing
`tools/*.py` convention and ticket 04's server pattern.

Two things change from the throwaway prototype
(`tools/prototype_reviewer_triage*.*` on the unmerged
`origin/worktree-prototype-reviewer-triage` branch, not present on `main`):

1. Crops shown are **real crops from an actual run** (reuse
   `render_residual_crop()`), not placeholders.
2. Each disposition **writes directly to a real `decisions.json`, tied to the
   run's output hash**, via the local server — not just held in browser
   state.

The promotion-preview logic (what a "we've already flagged this" disposition
would add to the denylist, what a "fine to show" disposition would propose
for a lexicon) is ported from `tools/prototype_reviewer_triage.py`'s already
schema-accurate implementation, rendered only on a separate ops-facing view
never shown to the reviewer, and never automatically applied to the denylist
or lexicons.

**Blocked by:** none.

**Status:** ready-for-agent

**GitHub issue:** https://github.com/SashaGordin/pdf-sanitizer/issues/23

- [ ] Launching the tool against a real run directory shows that run's real
      findings, each with its real crop and a plain-language category guess
      — no internal labels, occurrence counts, or match scores visible to
      the reviewer.
- [ ] Selecting any of the four plain-language dispositions, with or without
      a note, writes an entry to that run's `decisions.json`, keyed to the
      run's output hash.
- [ ] A test calling the server's disposition-recording function directly
      (no browser) with a synthetic finding and disposition asserts the
      correct entry lands in `decisions.json`.
- [ ] The ops-facing promotion-preview view (denylist/lexicon preview) is
      reachable only from a separate view, never the reviewer's screen, and
      a test confirms no promotion preview data appears in the reviewer-
      facing payload.
- [ ] No disposition, by itself, modifies `config/denylist.local.json` or any
      lexicon file — promotion stays preview-only pending a separate human
      action.
- [ ] A manual/headless-browser smoke pass confirms the interactive review
      flow works end-to-end against a real run.

## Comments
