# 03 — Phase 2 P1 completion: per-label caps, review-completeness, intake-completeness

**What to build:** The three remaining Phase 2 P1 items, grouped in one
ticket because the wayfinder map (issue #2) explicitly grouped them and noted
`PRODUCTION-READINESS-PLAN.md` already specifies concrete behavior for all
three — no further design ticket was needed.

1. **Per-label detection caps.** Replace the single global `max_findings`
   integer with a per-label cap dict, so low-volume high-value labels
   (`street address`, `city`) aren't crowded out by high-volume ones
   (`organization`).
2. **Review-completeness gate.** `residuals_truncated > 0` or
   `findings_truncated > 0` on any document blocks that run from reaching
   `RELEASED` — truncation must be a hard incompleteness signal, not merely a
   counted field.
3. **Intake-completeness gate.** Require `--project-metadata`; hash the file
   into the manifest. If the file is missing or has empty fields, either fail
   this gate or record an explicit waiver (naming the empty fields) rather
   than proceeding silently.

**Blocked by:** none.

**Status:** ready-for-agent

**GitHub issue:** https://github.com/SashaGordin/pdf-sanitizer/issues/20

- [ ] A synthetic document with a high-volume label (e.g. many `organization`
      matches) and a low-volume high-value label (e.g. one `street address`)
      proves the low-volume label is never dropped due to the cap, with caps
      configurable per label.
- [ ] A synthetic run forced to truncate residuals or NER findings fails to
      reach `RELEASED`, and the report clearly states why.
- [ ] A run with no `--project-metadata` argument fails the intake-
      completeness gate (or, if a waiver flag is passed, records the waiver
      and the specific empty-field list in the manifest).
- [ ] A run with a `--project-metadata` file that has some empty fields
      records exactly those empty fields in the manifest.
- [ ] The project-metadata file's hash appears in the manifest for every run
      that supplies one.

## Comments
