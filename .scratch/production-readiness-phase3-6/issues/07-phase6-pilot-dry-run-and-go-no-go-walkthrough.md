# 07 — Phase 6 pilot dry run and go/no-go walkthrough

**What to build:** Execute the Phase 6 dry run issue #9 specified — one
workflow exercise of the full pipeline against the locked corpus, collapsing
shadow mode and the confidential-pilot comparison steps into a single run on
public documents (not a confidentiality test) — then walk the production
go/no-go checklist item by item against its actual results.

**Sequence:**

1. Run the full pipeline against the assembled locked corpus (ticket 05).
2. Have the client annotate the same documents using the real labeling tool
   (ticket 04), producing independent ground truth.
3. Compare automated findings against the client's annotations; record
   reviewer-only catches and the disagreement rate — the metrics
   `PRODUCTION-READINESS-PLAN.md` names for the first pilot.
4. Walk every item in the production go/no-go checklist
   (`PRODUCTION-READINESS-PLAN.md`, "Production go/no-go checklist" section)
   against the dry run's actual results, recording each item's current
   true/false state directly in this ticket's Comments.
5. The single operator (per ADR-0001/issue #9) makes the go/no-go call based
   on the recorded checklist state — this ticket records that decision, it
   doesn't make it for them.

The real confidential pilot (Phase 6 steps 2 and 5–6) is explicitly not part
of this ticket — it stays unscheduled pending an actual paying client
engagement.

**Blocked by:** 05 (needs the assembled locked corpus); 08 (needs a way to
get real annotations from the actual client, who is not the operator and
not on the operator's machine).

**Status:** ready-for-agent

**GitHub issue:** https://github.com/SashaGordin/pdf-sanitizer/issues/24

- [ ] A full pipeline run against the locked corpus completes and produces a
      report/manifest for every corpus document.
- [ ] Client annotations for the same documents exist (produced via ticket
      04's tool).
- [ ] A written comparison summary records reviewer-only catches and the
      disagreement rate between automated findings and client annotations.
- [ ] Every item in `PRODUCTION-READINESS-PLAN.md`'s go/no-go checklist has a
      recorded true/false state in this ticket's Comments, based on the dry
      run's actual results (not assumed).
- [ ] The recorded go/no-go decision, and who made it, is written into this
      ticket's Comments.
- [ ] Nothing in this ticket schedules or requires a confidential client
      engagement.

## Comments

2026-08-09: Only 1 of the 8 real locked-corpus documents
(`ph_quezon_city_health_center`) is labeled — ticket 05 left the other 7 for
the operator. Investigating that gap surfaced that the real annotator for
this ticket's "client" step is a specific trusted person the operator
doesn't run the labeling tool on behalf of, and isn't the operator running
it locally themselves — so ticket 04's tool needs to reach that person on
their own machine before step 2 can happen at all.

Per the operator's explicit decision, this ticket is on hold: the pipeline
run, the comparison, and the go/no-go checklist walkthrough are
deliberately **not** being executed yet. Filed ticket 08 to package
`tools/corpus_labeler.py` as a standalone executable that person can run
with no setup; once they return real annotations, this ticket resumes with
those labels in `.scratch/corpus/labels/`. Nothing in this ticket's own
acceptance checklist has been touched.
